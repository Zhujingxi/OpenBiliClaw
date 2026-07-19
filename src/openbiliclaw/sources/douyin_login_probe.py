"""Live Douyin login probe.

Historically Douyin was the one cookie-bearing source with *no* login check
at all. The reason recorded in ``api/app.py`` was:

    "Douyin direct-cookie discovery currently has no stable nav endpoint that
    cleanly distinguishes 'logged out' from 'soft anti-bot returned HTTP 200
    with empty data'."

That claim was refuted on 2026-07-18 by a strip-down control experiment (see
``docs/plans/2026-07-18-source-auth-contract-spec.md`` D11). Treatment group =
the machine's real cookie (57 pairs incl. ``sessionid`` / ``sessionid_ss`` /
``sid_tt``); control group = the same cookie with the 12 login-bearing pairs
removed, i.e. a guest session. Same signer, same UA, same minute — the only
variable was the login cookies:

    endpoint                              treatment          control
    /aweme/v1/web/user/profile/self/      status_code=0      status_code=8
                                          + non-empty uid    "用户未登录"
    /aweme/v1/web/collects/list/          status_code=0      status_code=8

The discriminator is an explicit error code, not the "HTTP 200 with empty
data" ambiguity the docstring feared. Measured latency over 3 consecutive
calls: 260 / 299 / 428 ms — cheap enough to sit behind a short TTL.

**Do not "simplify" this to ``/aweme/v1/web/query/user/``.** That endpoint
returns a 12-digit ``user_uid`` that is *identical* for the treatment and
control groups: it is a device-scoped identifier driven by ``ttwid`` /
``odin_tt``, not an account one. Probing it and checking "did we get a uid"
looks like it works and silently always reports logged-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openbiliclaw.sources.douyin_direct import (
    DouyinDirectAuthError,
    DouyinDirectClient,
    DouyinDirectError,
)

if TYPE_CHECKING:
    import httpx

# Account-scoped endpoint: reading one's own profile. Read-only, no side
# effects on the account, and the lightest of the three verified options.
LOGIN_PROBE_PATH = "/aweme/v1/web/user/profile/self/"

# Douyin's own status codes on that endpoint (observed 2026-07-18).
_STATUS_OK = 0
_STATUS_NOT_LOGGED_IN = 8


@dataclass(frozen=True)
class DouyinAuthStatus:
    """Structured Douyin auth status.

    Mirrors ``openbiliclaw.bilibili.auth.AuthStatus`` on purpose: both are
    live probes, so callers can treat them uniformly when deriving the
    source-auth contract.
    """

    has_cookie: bool
    authenticated: bool
    user_id: str = ""
    username: str = ""
    message: str = ""
    # True when the probe failed at the transport layer (proxy, risk control,
    # timeout, DNS) rather than because the cookie is logged out. Callers must
    # not downgrade a source to "logged out" on a transport failure.
    network_error: bool = False


def _extract_user(payload: dict[str, Any]) -> tuple[str, str]:
    """Pull ``(user_id, nickname)`` out of a profile/self response."""
    user = payload.get("user")
    if not isinstance(user, dict):
        return "", ""
    uid = str(user.get("uid") or user.get("short_id") or "").strip()
    nickname = str(user.get("nickname") or "").strip()
    return uid, nickname


async def probe_douyin_login(
    cookie: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> DouyinAuthStatus:
    """Probe whether *cookie* is a logged-in Douyin session.

    Network failures are reported via ``network_error`` rather than as a
    logged-out verdict, so a flaky proxy never looks like an expired cookie.
    """
    normalized = cookie.strip()
    if not normalized:
        return DouyinAuthStatus(
            has_cookie=False,
            authenticated=False,
            message="尚未同步抖音 Cookie。",
        )

    try:
        client = DouyinDirectClient(cookie=normalized, http_client=http_client)
    except DouyinDirectAuthError:
        return DouyinAuthStatus(
            has_cookie=False,
            authenticated=False,
            message="抖音 Cookie 无法解析。",
        )

    try:
        payload = await client.request_json(LOGIN_PROBE_PATH, {})
    except DouyinDirectError as exc:
        return DouyinAuthStatus(
            has_cookie=True,
            authenticated=False,
            message=f"抖音登录态探测失败：{exc}",
            network_error=True,
        )
    except Exception as exc:  # noqa: BLE001 - transport seam, cause is preserved in message
        return DouyinAuthStatus(
            has_cookie=True,
            authenticated=False,
            message=f"抖音登录态探测失败：{type(exc).__name__}: {exc}",
            network_error=True,
        )
    finally:
        if http_client is None:
            await client.aclose()

    # ``DouyinDirectClient._request_json`` swallows transport failures (HTTPError,
    # non-200, non-JSON) and returns ``{}``. A successful Douyin response always
    # carries ``status_code``, so an empty payload means the request never landed
    # — that is a transport problem, never a logged-out verdict.
    if not payload:
        return DouyinAuthStatus(
            has_cookie=True,
            authenticated=False,
            message="抖音登录态探测失败：请求未返回数据（可能是网络、代理或风控拦截）。",
            network_error=True,
        )

    status_code = payload.get("status_code")
    if status_code == _STATUS_NOT_LOGGED_IN:
        return DouyinAuthStatus(
            has_cookie=True,
            authenticated=False,
            message="抖音 Cookie 已失效，请在浏览器重新登录后等待插件同步。",
        )

    if status_code != _STATUS_OK:
        # Unknown code: report as indeterminate rather than logged out, so an
        # upstream change to Douyin's error surface never silently flips a
        # working source to "expired".
        return DouyinAuthStatus(
            has_cookie=True,
            authenticated=False,
            message=f"抖音返回未知状态码 {status_code!r}，无法判定登录态。",
            network_error=True,
        )

    user_id, username = _extract_user(payload)
    if not user_id:
        return DouyinAuthStatus(
            has_cookie=True,
            authenticated=False,
            message="抖音返回成功但缺少账号信息，无法确认登录态。",
            network_error=True,
        )

    who = f"（{username}）" if username else ""
    return DouyinAuthStatus(
        has_cookie=True,
        authenticated=True,
        user_id=user_id,
        username=username,
        message=f"已登录抖音{who}。",
    )
