from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ...bilibili.api import BilibiliAPIError, BilibiliAuthExpiredError
from ..models import (
    NativeSaveAction,
    NativeSaveCapability,
    NativeSaveResult,
    NativeSaveStatus,
)

if TYPE_CHECKING:
    from ...bilibili.api import BilibiliAPIClient
    from ..models import NativeSaveRoute, SavedItemInput


class BilibiliNativeSaveAdapter:
    """Write Bilibili saved items to the authenticated user's account."""

    _FAVORITE_DUPLICATE_CODES: ClassVar[frozenset[int]] = frozenset({11201})
    _RATE_LIMIT_CODES: ClassVar[frozenset[int]] = frozenset({-352, -412, -429, -509})
    _CAPABILITY: ClassVar[NativeSaveCapability] = NativeSaveCapability(
        platform="bilibili",
        supports_favorite=True,
        supports_watch_later=True,
        supports_named_collection=True,
    )

    def __init__(self, client: BilibiliAPIClient) -> None:
        self._client = client

    @property
    def capability(self) -> NativeSaveCapability:
        return self._CAPABILITY

    def target_label(self, action: NativeSaveAction) -> str:
        if action == "watch_later":
            return "B站稍后再看"
        return "B站 OpenBiliClaw 收藏夹"

    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        try:
            if route.resolved_action == "favorite":
                folder = await self._client.ensure_favorite_folder("OpenBiliClaw")
                await self._client.add_video_to_favorite(item.content_id, folder.media_id)
            else:
                await self._client.add_video_to_watch_later(item.content_id)
        except BilibiliAuthExpiredError as exc:
            return self._failure_result(item, route, "login_required", exc.code)
        except BilibiliAPIError as exc:
            if exc.code == -101:
                return self._failure_result(item, route, "login_required", exc.code)
            if route.resolved_action == "watch_later" and exc.code == 90003:
                return NativeSaveResult(
                    item_key=item.item_key,
                    status="failed",
                    resolved_action=route.resolved_action,
                    resolved_target=route.resolved_target,
                    error_code="bilibili_video_unavailable",
                    error_message="Bilibili video is unavailable for watch later",
                )
            if route.resolved_action == "favorite" and exc.code in self._FAVORITE_DUPLICATE_CODES:
                status: NativeSaveStatus = "already_synced"
            elif exc.code in self._RATE_LIMIT_CODES:
                status = "rate_limited"
            else:
                status = "failed"
            return self._failure_result(item, route, status, exc.code)
        except Exception:
            return NativeSaveResult(
                item_key=item.item_key,
                status="failed",
                resolved_action=route.resolved_action,
                resolved_target=route.resolved_target,
                error_code="bilibili_native_save_failed",
                error_message="Bilibili native save failed",
            )

        return NativeSaveResult(
            item_key=item.item_key,
            status="synced",
            resolved_action=route.resolved_action,
            resolved_target=route.resolved_target,
        )

    @staticmethod
    def _failure_result(
        item: SavedItemInput,
        route: NativeSaveRoute,
        status: NativeSaveStatus,
        code: int | None,
    ) -> NativeSaveResult:
        safe_code = f"bilibili_{code}" if code is not None else "bilibili_api_error"
        if status == "login_required":
            message = "Bilibili login required"
        elif status == "already_synced":
            message = f"Bilibili item is already saved (code {code})"
        elif status == "rate_limited":
            message = f"Bilibili native save rate limited (code {code})"
        elif code is not None:
            message = f"Bilibili native save failed (code {code})"
        else:
            message = "Bilibili native save failed"
        return NativeSaveResult(
            item_key=item.item_key,
            status=status,
            resolved_action=route.resolved_action,
            resolved_target=route.resolved_target,
            error_code=safe_code,
            error_message=message,
        )
