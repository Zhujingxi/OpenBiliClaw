"""Authentication and cookie management for Bilibili."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .api import NavInfo

logger = logging.getLogger(__name__)


class SupportsNavClient(Protocol):
    """Protocol for API clients used by AuthManager."""

    async def get_nav_info(self) -> NavInfo: ...

    async def close(self) -> None: ...


@dataclass
class AuthStatus:
    """Structured authentication status for CLI and services."""

    has_cookie: bool
    authenticated: bool
    cookie_path: Path
    username: str = ""
    user_id: int = 0
    message: str = ""
    # True when validation failed at the transport layer (proxy, risk
    # control, timeout, DNS) rather than because the cookie itself is
    # invalid — callers use this to give network-specific guidance.
    network_error: bool = False


class AuthManager:
    """Manages Bilibili authentication state.

    Supports:
    - Cookie-based authentication (from browser)
    - No-login mode (limited functionality)
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        api_client_factory: Callable[[str], SupportsNavClient] | None = None,
        proxy: str | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._cookie_path = data_dir / "bilibili_cookie.json"
        self._cookie: str = ""
        self._proxy = proxy or None
        self._api_client_factory = api_client_factory or self._default_api_client_factory

    @property
    def is_authenticated(self) -> bool:
        """Whether we have a valid authentication cookie."""
        return bool(self._cookie)

    @property
    def cookie(self) -> str:
        """Current cookie string."""
        return self._cookie

    def set_cookie(self, cookie: str) -> None:
        """Set and persist the authentication cookie.

        Args:
            cookie: Cookie string from browser.
        """
        self._cookie = cookie.strip()
        self._save_cookie()
        logger.info("Cookie set and saved.")

    def load_cookie(self) -> str:
        """Load persisted cookie from disk.

        Returns:
            Cookie string, or empty string if not found.
        """
        if self._cookie_path.exists():
            # encoding="utf-8" matches save_cookie() — Bilibili cookies
            # are ASCII so the bug never triggers in prod, but a
            # platform-default encoding still risks UnicodeDecodeError
            # on Windows GBK if a future cookie field carries non-ASCII.
            with open(self._cookie_path, encoding="utf-8") as f:
                data = json.load(f)
                self._cookie = data.get("cookie", "")
                logger.info("Cookie loaded from disk.")
        return self._cookie

    async def validate_cookie(self, cookie: str) -> AuthStatus:
        """Validate a cookie string against the Bilibili nav endpoint."""
        normalized_cookie = cookie.strip()
        if not normalized_cookie:
            return AuthStatus(
                has_cookie=False,
                authenticated=False,
                cookie_path=self._cookie_path,
                message="未提供有效的 Cookie。",
            )

        from .api import BilibiliAuthExpiredError

        client = self._api_client_factory(normalized_cookie)
        try:
            nav = await client.get_nav_info()
        except BilibiliAuthExpiredError as exc:
            # nav answered with -101: the request went through fine, the
            # cookie itself is expired — must NOT be flagged network_error
            # or the UI would send the user chasing proxy problems.
            logger.warning("Cookie validation failed: %s", exc)
            return AuthStatus(
                has_cookie=True,
                authenticated=False,
                cookie_path=self._cookie_path,
                message="当前 Cookie 未登录或已失效。",
            )
        except Exception as exc:
            logger.warning("Cookie validation failed: %s", exc)
            return AuthStatus(
                has_cookie=True,
                authenticated=False,
                cookie_path=self._cookie_path,
                message=str(exc),
                network_error=True,
            )
        finally:
            await client.close()

        if not nav.is_login:
            return AuthStatus(
                has_cookie=True,
                authenticated=False,
                cookie_path=self._cookie_path,
                message="当前 Cookie 未登录或已失效。",
            )

        return AuthStatus(
            has_cookie=True,
            authenticated=True,
            cookie_path=self._cookie_path,
            username=nav.uname,
            user_id=nav.mid,
            message="Cookie 验证成功。",
        )

    async def get_status(self) -> AuthStatus:
        """Get current persisted authentication status."""
        cookie = self.load_cookie()
        if not cookie.strip():
            return AuthStatus(
                has_cookie=False,
                authenticated=False,
                cookie_path=self._cookie_path,
                message="未配置 B 站 Cookie。",
            )
        return await self.validate_cookie(cookie)

    def _save_cookie(self) -> None:
        """Persist cookie to disk. Always UTF-8 — see load_cookie()."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        with open(self._cookie_path, "w", encoding="utf-8") as f:
            json.dump({"cookie": self._cookie}, f)

    def clear_cookie(self) -> None:
        """Clear stored cookie."""
        self._cookie = ""
        if self._cookie_path.exists():
            self._cookie_path.unlink()
        logger.info("Cookie cleared.")

    def _default_api_client_factory(self, cookie: str) -> SupportsNavClient:
        """Create the default Bilibili API client."""
        from .api import BilibiliAPIClient

        return BilibiliAPIClient(cookie=cookie, proxy=self._proxy)


def resolve_runtime_cookie(*, data_dir: Path, configured_cookie: str) -> str:
    """Resolve the cookie used by runtime commands.

    Commands should prefer an explicitly configured cookie, but transparently
    fall back to the cookie previously saved by `auth login`.
    """
    normalized_cookie = configured_cookie.strip()
    if normalized_cookie:
        return normalized_cookie
    return AuthManager(data_dir).load_cookie().strip()
