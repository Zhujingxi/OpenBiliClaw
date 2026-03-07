"""Bilibili package — API and browser access layer."""

from .api import (
    BilibiliAPIClient,
    BilibiliAPIError,
    CommentInfo,
    FavoriteFolder,
    FavoriteFolderWithItems,
    FollowingUser,
    NavInfo,
    VideoInfo,
)
from .auth import AuthManager, AuthStatus

__all__ = [
    "AuthManager",
    "AuthStatus",
    "BilibiliAPIClient",
    "BilibiliAPIError",
    "CommentInfo",
    "FavoriteFolder",
    "FavoriteFolderWithItems",
    "FollowingUser",
    "NavInfo",
    "VideoInfo",
]
