"""Read-only client for Bangumi's official v0 API."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from openbiliclaw import __version__
from openbiliclaw.network import outbound_httpx_kwargs, outbound_proxy_mode
from openbiliclaw.sources.platforms import (
    OVERSEAS_DIRECT_MODE_ERROR_SUFFIX,
    PLATFORM_BANGUMI,
    requires_overseas_network,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

BANGUMI_API_BASE_URL = "https://api.bgm.tv"
BANGUMI_PROJECT_URL = "https://github.com/whiteguo233/OpenBiliClaw"
BANGUMI_USER_AGENT = f"whiteguo233/OpenBiliClaw/{__version__} ({BANGUMI_PROJECT_URL})"

SUBJECT_TYPE_IDS: dict[str, int] = {
    "book": 1,
    "anime": 2,
    "music": 3,
    "game": 4,
    "real": 6,
}


@dataclass(frozen=True)
class BangumiPage:
    """A validated page returned by the Bangumi v0 API."""

    data: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class BangumiAPIError(RuntimeError):
    """Stable, UI-safe failure raised by :class:`BangumiClient`."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message[:240])
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def subject_type_id(value: str) -> int:
    """Map a stable config name to Bangumi's integer SubjectType."""

    key = str(value or "").strip().lower()
    try:
        return SUBJECT_TYPE_IDS[key]
    except KeyError as exc:
        raise ValueError(f"unsupported Bangumi subject type: {value!r}") from exc


def validate_bangumi_username(value: object) -> str:
    """Validate an explicitly configured public Bangumi username."""

    username = str(value or "").strip()
    if not username:
        return ""
    if len(username) > 128:
        raise ValueError("Bangumi username must be at most 128 characters")
    if "/" in username or any(ord(char) < 32 or ord(char) == 127 for char in username):
        raise ValueError("Bangumi username contains an unsupported character")
    return username


def validate_bangumi_access_token(value: object) -> str:
    """Validate an explicitly configured Bangumi personal access token.

    Structural-only (no network): confirms the token is a single-line ASCII
    string of a sane length. Live validity is confirmed separately via
    :meth:`BangumiClient.get_me`. Never log the token itself — only its
    presence or length (privacy: personal tokens read private collections).
    """

    token = str(value or "").strip()
    if not token:
        return ""
    if len(token) > 512:
        raise ValueError("Bangumi access token must be at most 512 characters")
    if any(ord(char) < 32 or ord(char) == 127 or ord(char) > 126 for char in token):
        raise ValueError("Bangumi access token contains an unsupported character")
    return token


def me_username(payload: Mapping[str, Any]) -> str:
    """Defensively extract the username from a ``/v0/me`` response.

    Bangumi returns ``username`` (the stable URL slug) alongside a display
    ``nickname``. Only ``username`` addresses ``/v0/users/{username}``. Missing
    or malformed values raise so callers surface a diagnosable failure rather
    than silently querying an empty path.
    """

    username = str(payload.get("username") or "").strip()
    if not username:
        raise BangumiAPIError("schema_changed", "Bangumi /v0/me response is missing a username")
    return username


def _clamp_page(limit: int, offset: int) -> tuple[int, int]:
    return min(50, max(1, int(limit))), max(0, int(offset))


def _retry_after_seconds(value: str | None) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return min(86_400, max(0, int(raw)))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return min(86_400, max(0, math.ceil((retry_at - datetime.now(UTC)).total_seconds())))


class BangumiClient:
    """Minimal client for discovery, public collections, and self-identity.

    Read-only: the public API surface has no collection write or OAuth
    helpers. When an ``access_token`` (a user's personal access token) is
    supplied, every request carries ``Authorization: Bearer <token>`` so the
    caller can resolve their own identity via :meth:`get_me` and read their
    private collections. With ``access_token=None`` the client behaves exactly
    like the historical anonymous client. An injected ``httpx.AsyncClient``
    remains owned by its caller.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        access_token: str | None = None,
        request_interval_seconds: float = 1.0,
        transient_retry_delay_seconds: float = 0.25,
    ) -> None:
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=BANGUMI_API_BASE_URL,
            timeout=15.0,
            **outbound_httpx_kwargs(),
        )
        # Captured next to outbound_httpx_kwargs() so a failure message can
        # describe the transport that actually failed. Only meaningful for a
        # client WE built from the outbound policy: an injected client is the
        # caller's own transport, about which ``[network].mode`` says nothing.
        self._outbound_mode = outbound_proxy_mode() if self._owns_http_client else ""
        # Structural validation only; keep the token in memory, never log it.
        self._access_token = validate_bangumi_access_token(access_token) or None
        self._request_interval_seconds = max(0.0, float(request_interval_seconds))
        self._transient_retry_delay_seconds = max(0.0, float(transient_retry_delay_seconds))
        self._request_lock = asyncio.Lock()
        self._last_request_started_at = 0.0

    def _network_failure_message(self, message: str) -> str:
        """Name the likely cause when ``direct`` mode is what broke the request.

        bgm.tv is Cloudflare-fronted and resolves overseas, so a mainland-China
        install running ``[network].mode = direct`` times out on every call.
        Attaching the advice at the point of failure — rather than in each
        caller — is what lets the CLI smokes, the API and discovery all explain
        the timeout without re-implementing the check (rule 7: propagate the
        real cause). The classification itself stays in ``sources.platforms``.
        """
        if self._outbound_mode != "direct" or not requires_overseas_network(PLATFORM_BANGUMI):
            return message
        return f"{message}{OVERSEAS_DIRECT_MODE_ERROR_SUFFIX}"

    async def __aenter__(self) -> BangumiClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    @property
    def has_access_token(self) -> bool:
        """Whether this client authenticates requests with a Bearer token."""

        return self._access_token is not None

    def disable_access_token(self) -> None:
        """Drop the Bearer token so subsequent requests go anonymous.

        Used to degrade a discovery client to the public path after Bangumi
        rejects the token (expired/revoked) — public discovery endpoints need
        no auth, so the client keeps working instead of failing every cycle.
        """

        self._access_token = None

    async def get_me(self) -> dict[str, Any]:
        """Return the account for the configured access token (``GET /v0/me``).

        Requires an ``access_token``. A 401/403 raises ``BangumiAPIError`` with
        code ``unauthorized`` (never swallowed) so callers can tell the user
        their token is missing, wrong, or expired.
        """

        if self._access_token is None:
            raise BangumiAPIError("unauthorized", "Bangumi /v0/me requires a personal access token")
        return await self._request_json("GET", "/v0/me")

    async def get_user(self, username: str) -> dict[str, Any]:
        """Fetch one user's public profile (``GET /v0/users/{username}``).

        Anonymous endpoint — no token required. The path parameter is the
        username slug; users who never set a custom slug keep the default
        ``str(uid)`` slug, so a numeric uid resolves exactly for them (verified
        2026-07-18: ``/v0/users/474349`` → ``id=474349``, while ``/v0/users/1``
        404s because uid 1 uses the custom slug ``sai``). The response's ``id``
        field enables authoritative uid↔username cross-checks.
        """

        normalized = validate_bangumi_username(username)
        if not normalized:
            raise ValueError("Bangumi username is required")
        return await self._request_json("GET", f"/v0/users/{quote(normalized, safe='')}")

    async def search_subjects(
        self,
        keyword: str,
        *,
        subject_types: tuple[str, ...],
        limit: int,
        offset: int = 0,
        sort: str = "match",
    ) -> BangumiPage:
        query = str(keyword or "").strip()
        if not query:
            page_limit, page_offset = _clamp_page(limit, offset)
            return BangumiPage([], 0, page_limit, page_offset)
        if sort not in {"match", "heat", "rank", "score"}:
            raise ValueError(f"unsupported Bangumi search sort: {sort!r}")
        type_ids = [subject_type_id(value) for value in subject_types]
        if not type_ids:
            raise ValueError("Bangumi search requires at least one subject type")
        page_limit, page_offset = _clamp_page(limit, offset)
        payload = await self._request_json(
            "POST",
            "/v0/search/subjects",
            params={"limit": page_limit, "offset": page_offset},
            json_body={
                "keyword": query,
                "sort": sort,
                "filter": {"type": type_ids, "nsfw": False},
            },
        )
        return self._page(payload, limit=page_limit, offset=page_offset)

    async def browse_subjects(
        self,
        subject_type: str,
        *,
        sort: str,
        limit: int,
        offset: int = 0,
    ) -> BangumiPage:
        if sort not in {"rank", "date"}:
            raise ValueError(f"unsupported Bangumi browse sort: {sort!r}")
        page_limit, page_offset = _clamp_page(limit, offset)
        payload = await self._request_json(
            "GET",
            "/v0/subjects",
            params={
                "type": subject_type_id(subject_type),
                "sort": sort,
                "limit": page_limit,
                "offset": page_offset,
            },
        )
        return self._page(payload, limit=page_limit, offset=page_offset)

    async def get_user_collections(
        self,
        username: str,
        *,
        collection_type: int | None = None,
        subject_type: str | None = None,
        limit: int,
        offset: int = 0,
    ) -> BangumiPage:
        normalized_username = validate_bangumi_username(username)
        if not normalized_username:
            raise ValueError("Bangumi username is required")
        if collection_type is not None and int(collection_type) not in {1, 2, 3, 4, 5}:
            raise ValueError("Bangumi collection type must be between 1 and 5")
        page_limit, page_offset = _clamp_page(limit, offset)
        params: dict[str, object] = {"limit": page_limit, "offset": page_offset}
        if collection_type is not None:
            params["type"] = int(collection_type)
        if subject_type is not None:
            params["subject_type"] = subject_type_id(subject_type)
        payload = await self._request_json(
            "GET",
            f"/v0/users/{quote(normalized_username, safe='')}/collections",
            params=params,
        )
        return self._page(payload, limit=page_limit, offset=page_offset)

    async def _pace(self) -> None:
        if self._request_interval_seconds <= 0:
            return
        now = time.monotonic()
        remaining = self._request_interval_seconds - (now - self._last_request_started_at)
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._last_request_started_at = time.monotonic()

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "User-Agent": BANGUMI_USER_AGENT,
            "Accept": "application/json",
        }
        if self._access_token is not None:
            headers["Authorization"] = f"Bearer {self._access_token}"
        response: httpx.Response | None = None
        for attempt in range(2):
            async with self._request_lock:
                await self._pace()
                try:
                    response = await self._http.request(
                        method,
                        path,
                        params=params,
                        json=json_body,
                        headers=headers,
                        timeout=15.0,
                    )
                except httpx.TimeoutException as exc:
                    if attempt == 0:
                        response = None
                    else:
                        raise BangumiAPIError(
                            "timeout",
                            self._network_failure_message("Bangumi API request timed out"),
                        ) from exc
                except httpx.HTTPError as exc:
                    raise BangumiAPIError(
                        "network_error",
                        self._network_failure_message("Bangumi API network request failed"),
                    ) from exc
            if response is None or (response.status_code >= 500 and attempt == 0):
                if self._transient_retry_delay_seconds:
                    await asyncio.sleep(self._transient_retry_delay_seconds)
                continue
            break
        if response is None:
            raise BangumiAPIError(
                "timeout", self._network_failure_message("Bangumi API request timed out")
            )
        status = int(response.status_code)
        if status == 429:
            raise BangumiAPIError(
                "rate_limited",
                "Bangumi API rate limited this client",
                status_code=status,
                retry_after_seconds=_retry_after_seconds(response.headers.get("Retry-After")),
            )
        if status in (401, 403):
            raise BangumiAPIError(
                "unauthorized",
                "Bangumi rejected the access token (missing, invalid, or expired)",
                status_code=status,
            )
        if status == 404:
            raise BangumiAPIError("not_found", "Bangumi resource was not found", status_code=status)
        if status == 400:
            raise BangumiAPIError(
                "invalid_request", "Bangumi API rejected the request", status_code=status
            )
        if status >= 500:
            raise BangumiAPIError(
                "upstream_error", "Bangumi API is temporarily unavailable", status_code=status
            )
        if status < 200 or status >= 300:
            raise BangumiAPIError(
                "http_error", f"Bangumi API returned HTTP {status}", status_code=status
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise BangumiAPIError(
                "schema_changed", "Bangumi API returned invalid JSON", status_code=status
            ) from exc
        if not isinstance(payload, dict):
            raise BangumiAPIError(
                "schema_changed", "Bangumi API response shape changed", status_code=status
            )
        return payload

    @staticmethod
    def _page(payload: Mapping[str, Any], *, limit: int, offset: int) -> BangumiPage:
        rows = payload.get("data")
        total = payload.get("total")
        if not isinstance(rows, list) or isinstance(total, bool) or not isinstance(total, int):
            raise BangumiAPIError("schema_changed", "Bangumi API page response shape changed")
        if not all(isinstance(row, dict) for row in rows):
            raise BangumiAPIError("schema_changed", "Bangumi API returned a malformed row")
        return BangumiPage(list(rows), max(0, total), limit, offset)


async def resolve_access_token_identity(
    token: object,
    *,
    request_interval_seconds: float = 1.0,
) -> str:
    """Validate a personal access token via ``/v0/me`` and return its username.

    Raises :class:`ValueError` for a structurally empty/invalid token (before
    any network) and :class:`BangumiAPIError` (code ``unauthorized``) when the
    live token is rejected. Callers surface the real cause rather than silently
    persisting an unusable token.
    """

    normalized = validate_bangumi_access_token(token)
    if not normalized:
        raise ValueError("Bangumi access token is required")
    async with BangumiClient(
        access_token=normalized,
        request_interval_seconds=request_interval_seconds,
    ) as client:
        return me_username(await client.get_me())
