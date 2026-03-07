"""Bilibili API Client.

Primary interface for interacting with Bilibili, prioritizing the official
and reverse-engineered API for speed and efficiency.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)


class BilibiliAPIError(RuntimeError):
    """Raised when a Bilibili API request returns an application error."""


def _json_object(value: Any) -> dict[str, Any]:
    """Coerce a JSON value into an object for strict typing."""
    return cast("dict[str, Any]", value)


def _json_list(value: Any) -> list[dict[str, Any]]:
    """Coerce a JSON value into a list of objects for strict typing."""
    return cast("list[dict[str, Any]]", value)


@dataclass
class VideoInfo:
    """Basic video information from Bilibili."""

    bvid: str = ""
    aid: int = 0
    title: str = ""
    description: str = ""
    duration: int = 0  # seconds
    cover_url: str = ""
    up_name: str = ""
    up_mid: int = 0
    view_count: int = 0
    like_count: int = 0
    coin_count: int = 0
    favorite_count: int = 0
    share_count: int = 0
    danmaku_count: int = 0
    tags: list[str] | None = None
    pub_date: str = ""


@dataclass
class NavInfo:
    """Basic authenticated user info from the nav endpoint."""

    is_login: bool = False
    uname: str = ""
    mid: int = 0


@dataclass
class FavoriteFolder:
    """Favorite folder metadata."""

    media_id: int
    title: str
    media_count: int = 0


@dataclass
class FavoriteFolderWithItems:
    """Favorite folder plus fetched items."""

    folder: FavoriteFolder
    items: list[dict[str, Any]]
    truncated: bool = False


@dataclass
class FollowingUser:
    """Basic followed user info."""

    mid: int
    uname: str
    sign: str = ""


@dataclass
class CommentInfo:
    """Basic comment info."""

    mid: int
    uname: str
    message: str
    like_count: int = 0


class BilibiliAPIClient:
    """Client for Bilibili's web API.

    This is the primary data access layer (API-first approach).
    For operations not supported by the API, use BilibiliBrowser.
    """

    _BASE_URL = "https://api.bilibili.com"

    def __init__(self, cookie: str = "", *, min_request_interval: float = 0.2) -> None:
        self._cookie = cookie
        self._min_request_interval = min_request_interval
        self._last_request_at = 0.0
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com",
            },
            timeout=30.0,
        )
        if cookie:
            self._client.headers["Cookie"] = cookie

    @property
    def is_authenticated(self) -> bool:
        """Whether we have a valid authentication cookie."""
        return bool(self._cookie)

    async def _respect_rate_limit(self) -> None:
        """Wait to keep a minimum interval between requests."""
        elapsed = time.monotonic() - self._last_request_at
        remaining = self._min_request_interval - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._last_request_at = time.monotonic()

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform a GET request and return the decoded `data` payload."""
        await self._respect_rate_limit()
        try:
            resp = await self._client.get(f"{self._BASE_URL}{path}", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BilibiliAPIError(str(exc)) from exc

        payload = _json_object(resp.json())
        code = int(payload.get("code", 0))
        if code != 0:
            message = str(payload.get("message", "Bilibili API request failed"))
            raise BilibiliAPIError(message)
        return _json_object(payload.get("data", {}))

    async def get_nav_info(self) -> NavInfo:
        """Get the current login state from Bilibili nav API."""
        data = await self._get_json("/x/web-interface/nav")
        return NavInfo(
            is_login=bool(data.get("isLogin", False)),
            uname=str(data.get("uname", "")),
            mid=int(data.get("mid", 0)),
        )

    async def get_video_info(self, bvid: str) -> VideoInfo:
        """Get video information by BV ID.

        Args:
            bvid: Bilibili video BV ID.

        Returns:
            VideoInfo dataclass.
        """
        resp = await self._client.get(
            f"{self._BASE_URL}/x/web-interface/view",
            params={"bvid": bvid},
        )
        resp.raise_for_status()
        payload = _json_object(resp.json())
        data = _json_object(payload["data"])
        stat = _json_object(data.get("stat", {}))
        owner = _json_object(data.get("owner", {}))

        return VideoInfo(
            bvid=data.get("bvid", bvid),
            aid=data.get("aid", 0),
            title=data.get("title", ""),
            description=data.get("desc", ""),
            duration=data.get("duration", 0),
            cover_url=data.get("pic", ""),
            up_name=owner.get("name", ""),
            up_mid=owner.get("mid", 0),
            view_count=stat.get("view", 0),
            like_count=stat.get("like", 0),
            coin_count=stat.get("coin", 0),
            favorite_count=stat.get("favorite", 0),
            share_count=stat.get("share", 0),
            danmaku_count=stat.get("danmaku", 0),
            pub_date=data.get("pubdate", ""),
        )

    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
    ) -> list[dict[str, Any]]:
        """Search for videos by keyword.

        Args:
            keyword: Search query.
            page: Page number.
            page_size: Results per page.

        Returns:
            List of search result dicts.
        """
        data = await self._get_json(
            "/x/web-interface/search/type",
            params={
                "keyword": keyword,
                "search_type": "video",
                "page": page,
                "page_size": page_size,
                "order": order,
            },
        )
        return _json_list(data.get("result", []))

    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]:
        """Get the authenticated user's watch history.

        Requires valid authentication cookie.

        Args:
            max_items: Maximum number of history items to fetch.

        Returns:
            List of history item dicts.
        """
        if not self.is_authenticated:
            logger.warning("Cannot fetch history without authentication.")
            return []

        items: list[dict[str, Any]] = []
        cursor_params: dict[str, Any] = {"type": "archive"}
        while len(items) < max_items:
            data = await self._get_json(
                "/x/web-interface/history/cursor",
                params=cursor_params,
            )
            batch = _json_list(data.get("list", []))
            if not batch:
                break
            items.extend(batch)
            cursor = _json_object(data.get("cursor", {}))
            next_max = cursor.get("max")
            next_view_at = cursor.get("view_at")
            if not next_max or not next_view_at:
                break
            cursor_params = {
                "type": "archive",
                "max": next_max,
                "view_at": next_view_at,
            }
        return items[:max_items]

    async def get_favorites(self, media_id: int) -> list[dict[str, Any]]:
        """Get content from a favorites folder.

        Args:
            media_id: Favorites folder media ID.

        Returns:
            List of favorite item dicts.
        """
        data = await self._get_json(
            "/x/v3/fav/resource/list",
            params={"media_id": media_id, "pn": 1, "ps": 20},
        )
        return _json_list(data.get("medias", []))

    async def get_favorite_folders(self) -> list[FavoriteFolder]:
        """Get the authenticated user's favorite folder metadata."""
        nav = await self.get_nav_info()
        data = await self._get_json(
            "/x/v3/fav/folder/created/list-all",
            params={"up_mid": nav.mid},
        )
        folders = _json_list(data.get("list", []))
        return [
            FavoriteFolder(
                media_id=int(folder.get("id", 0)),
                title=str(folder.get("title", "")),
                media_count=int(folder.get("media_count", 0)),
            )
            for folder in folders
        ]

    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
    ) -> list[FavoriteFolderWithItems]:
        """Get favorite folders and fetch each folder's items within budget."""
        folders = await self.get_favorite_folders()
        aggregated: list[FavoriteFolderWithItems] = []
        for folder in folders[:max_folders]:
            items = await self.get_favorites(folder.media_id)
            limited_items = items[:max_items_per_folder]
            aggregated.append(
                FavoriteFolderWithItems(
                    folder=folder,
                    items=limited_items,
                    truncated=(
                        len(items) > len(limited_items)
                        or folder.media_count > len(limited_items)
                    ),
                )
            )
        return aggregated

    async def get_following(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> list[FollowingUser]:
        """Get the authenticated user's following list."""
        nav = await self.get_nav_info()
        data = await self._get_json(
            "/x/relation/followings",
            params={"vmid": nav.mid, "pn": page, "ps": page_size},
        )
        users = _json_list(data.get("list", []))
        return [
            FollowingUser(
                mid=int(user.get("mid", 0)),
                uname=str(user.get("uname", "")),
                sign=str(user.get("sign", "")),
            )
            for user in users
        ]

    async def get_related_videos(self, bvid: str) -> list[dict[str, Any]]:
        """Get related/recommended videos for a given video.

        Args:
            bvid: Source video BV ID.

        Returns:
            List of related video dicts.
        """
        resp = await self._client.get(
            f"{self._BASE_URL}/x/web-interface/archive/related",
            params={"bvid": bvid},
        )
        resp.raise_for_status()
        payload = _json_object(resp.json())
        return _json_list(payload.get("data", []))

    async def get_ranking(self, rid: int = 0) -> list[dict[str, Any]]:
        """Get ranking/trending videos.

        Args:
            rid: Region ID (0 for all).

        Returns:
            List of ranking item dicts.
        """
        resp = await self._client.get(
            f"{self._BASE_URL}/x/web-interface/ranking/v2",
            params={"rid": rid, "type": "all"},
        )
        resp.raise_for_status()
        payload = _json_object(resp.json())
        data = _json_object(payload.get("data", {}))
        return _json_list(data.get("list", []))

    async def get_video_comments(self, bvid: str, limit: int = 20) -> list[CommentInfo]:
        """Get the top comments for a video."""
        video = await self.get_video_info(bvid)
        data = await self._get_json(
            "/x/v2/reply/main",
            params={"oid": video.aid, "type": 1, "mode": 3, "ps": limit},
        )
        replies = _json_list(data.get("replies", []))
        comments = [
            CommentInfo(
                mid=int(reply.get("mid", 0)),
                uname=str(_json_object(reply.get("member", {})).get("uname", "")),
                message=str(_json_object(reply.get("content", {})).get("message", "")),
                like_count=int(reply.get("like", 0)),
            )
            for reply in replies
        ]
        return comments[:limit]

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
