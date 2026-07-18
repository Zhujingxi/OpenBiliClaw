"""Explicit production composition for the seven retained source connectors."""

from __future__ import annotations

import json
from threading import Lock
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from cryptography.fernet import InvalidToken
from pydantic import BaseModel
from sqlalchemy import select

from openbiliclaw.features.sources.registry import SourceRegistry, build_source_registry
from openbiliclaw.features.sources.service import SourceTaskService
from openbiliclaw.infrastructure.database.models import SettingModel, SourceAccountModel
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.jobs.tasks import PermanentJobError
from openbiliclaw.infrastructure.security.credentials import (
    CredentialCipher,
    MissingCredentialKeyError,
)
from openbiliclaw.infrastructure.sources.bilibili import BilibiliSettings, build_bilibili_connector
from openbiliclaw.infrastructure.sources.bilibili_client import BilibiliAPIClient
from openbiliclaw.infrastructure.sources.douyin import DouyinSettings, build_douyin_connector
from openbiliclaw.infrastructure.sources.douyin_client import DouyinDirectClient
from openbiliclaw.infrastructure.sources.reddit import RedditSettings, build_reddit_connector
from openbiliclaw.infrastructure.sources.twitter import TwitterSettings, build_twitter_connector
from openbiliclaw.infrastructure.sources.twitter_client import XClient
from openbiliclaw.infrastructure.sources.xiaohongshu import (
    XiaohongshuSettings,
    build_xiaohongshu_connector,
)
from openbiliclaw.infrastructure.sources.youtube import YouTubeSettings, build_youtube_connector
from openbiliclaw.infrastructure.sources.youtube_client import YtScraperClient
from openbiliclaw.infrastructure.sources.zhihu import ZhihuSettings, build_zhihu_connector

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from sqlalchemy.orm import Session, sessionmaker

    from openbiliclaw.features.sources.service import SourceTaskUnitOfWork


class MissingSourceConfigurationError(PermanentJobError):
    """An enabled direct source is missing usable, encrypted authentication."""


class _CredentialProvider:
    """Decrypt one enabled account only when its connector first needs authentication."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def cookie(self, source_id: str) -> str:
        with self._session_factory() as session:
            row = session.scalar(
                select(SourceAccountModel)
                .where(
                    SourceAccountModel.source_id == source_id,
                    SourceAccountModel.enabled.is_(True),
                )
                .order_by(SourceAccountModel.account_key, SourceAccountModel.id)
                .limit(1)
            )
        if row is None:
            raise MissingSourceConfigurationError(
                f"enabled source '{source_id}' has no enabled account; configure its credentials"
            )
        try:
            plaintext = CredentialCipher.from_environment().decrypt(row.encrypted_credentials)
        except (MissingCredentialKeyError, InvalidToken) as exc:
            raise MissingSourceConfigurationError(
                f"enabled source '{source_id}' credentials cannot be decrypted; "
                "configure OPENBILICLAW_SECRET_KEY"
            ) from exc
        cookie = _credential_cookie(plaintext)
        if not cookie:
            raise MissingSourceConfigurationError(
                f"enabled source '{source_id}' credentials do not contain a cookie"
            )
        return cookie


def _credential_cookie(plaintext: str) -> str:
    """Accept an opaque cookie or the future JSON credential envelope without exposing it."""

    try:
        value = json.loads(plaintext)
    except json.JSONDecodeError:
        return plaintext.strip()
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        cookie = value.get("cookie")
        return cookie.strip() if isinstance(cookie, str) else ""
    return ""


ClientT = TypeVar("ClientT")
SettingsT = TypeVar("SettingsT", bound=BaseModel)


class _LazyClient(Generic[ClientT]):
    def __init__(self, factory: Callable[[], ClientT]) -> None:
        self._factory = factory
        self._client: ClientT | None = None
        self._lock = Lock()

    def get(self) -> ClientT:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = self._factory()
        return self._client


def _source_settings(
    session_factory: sessionmaker[Session],
    source_id: str,
    model: type[SettingsT],
    *,
    default: SettingsT | None = None,
) -> SettingsT:
    with session_factory() as session:
        row = session.get(SettingModel, f"source-config:{source_id}")
    if row is None:
        return default if default is not None else model.model_validate({})
    if not isinstance(row.value, dict):
        raise ValueError(f"persisted {source_id} settings must be an object")
    return model.model_validate_json(json.dumps(row.value))


def _resolved_source_settings(
    session_factory: sessionmaker[Session],
    source_id: str,
    model: type[SettingsT],
    overrides: Mapping[str, Mapping[str, object]],
    *,
    default: SettingsT | None = None,
) -> SettingsT:
    """Read one persisted model, substituting a validated pre-commit candidate when supplied."""

    if source_id in overrides:
        return model.model_validate(dict(overrides[source_id]), strict=True)
    return _source_settings(session_factory, source_id, model, default=default)


class _LazyBilibiliClient:
    def __init__(self, credentials: _CredentialProvider) -> None:
        self._public_client: _LazyClient[BilibiliAPIClient] = _LazyClient(BilibiliAPIClient)
        self._authenticated_client: _LazyClient[BilibiliAPIClient] = _LazyClient(
            lambda: BilibiliAPIClient(cookie=credentials.cookie("bilibili"))
        )

    async def search(
        self, keyword: str, *, page: int = 1, page_size: int = 20, order: str = "totalrank"
    ) -> list[dict[str, Any]]:
        return await self._public_client.get().search(
            keyword, page=page, page_size=page_size, order=order
        )

    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]:
        return await self._authenticated_client.get().get_user_history(max_items=max_items)

    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
        max_total_items: int | None = None,
    ) -> list[Any]:
        return await self._authenticated_client.get().get_all_favorites(
            max_folders=max_folders,
            max_items_per_folder=max_items_per_folder,
            max_total_items=max_total_items,
        )

    async def get_following(self, *, page: int = 1, page_size: int = 50) -> list[Any]:
        return await self._authenticated_client.get().get_following(page=page, page_size=page_size)

    async def get_related_videos(self, bvid: str) -> list[dict[str, Any]]:
        return await self._public_client.get().get_related_videos(bvid)

    async def get_ranking(self, rid: int = 0) -> list[dict[str, Any]]:
        return await self._public_client.get().get_ranking(rid)

    @classmethod
    def search_cooldown_remaining(cls) -> float:
        return BilibiliAPIClient.search_cooldown_remaining()

    @classmethod
    def search_dom_fallback_remaining(cls) -> float:
        return BilibiliAPIClient.search_dom_fallback_remaining()


class _LazyDouyinClient:
    def __init__(self, credentials: _CredentialProvider) -> None:
        self._client: _LazyClient[DouyinDirectClient] = _LazyClient(
            lambda: DouyinDirectClient(cookie=credentials.cookie("douyin"))
        )

    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, Any]]:
        return await self._client.get().search_aweme(keyword, limit=limit)

    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, Any]]:
        return await self._client.get().get_hot_board(limit=limit)

    async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, Any]]:
        return await self._client.get().get_recommend_feed(limit=limit)


class _LazyTwitterClient:
    def __init__(self, credentials: _CredentialProvider) -> None:
        self._client: _LazyClient[XClient] = _LazyClient(
            lambda: XClient(credentials.cookie("twitter"))
        )

    async def search(self, query: str, *, limit: int, product: str = "Top") -> list[dict[str, Any]]:
        return await self._client.get().search(query, limit=limit, product=product)

    async def for_you(self, *, limit: int) -> list[dict[str, Any]]:
        return await self._client.get().for_you(limit=limit)

    async def user_tweets(self, handle: str, *, limit: int) -> list[dict[str, Any]]:
        return await self._client.get().user_tweets(handle, limit=limit)

    async def likes(self, *, limit: int) -> list[dict[str, Any]]:
        return await self._client.get().likes(limit=limit)

    async def bookmarks(self, *, limit: int) -> list[dict[str, Any]]:
        return await self._client.get().bookmarks(limit=limit)


def build_default_source_registry(
    session_factory: sessionmaker[Session],
    *,
    settings_overrides: Mapping[str, Mapping[str, object]] | None = None,
) -> SourceRegistry:
    """Register every built-in connector without importing plugins or making live calls."""

    overrides = settings_overrides or {}
    registry_box: dict[str, SourceRegistry] = {}

    def uow_factory() -> SourceTaskUnitOfWork:
        return cast("SourceTaskUnitOfWork", UnitOfWork(session_factory))

    task_service = SourceTaskService(uow_factory, lambda: registry_box["registry"])
    credentials = _CredentialProvider(session_factory)
    bilibili_settings = _resolved_source_settings(
        session_factory, "bilibili", BilibiliSettings, overrides
    )
    xiaohongshu_settings = _resolved_source_settings(
        session_factory, "xiaohongshu", XiaohongshuSettings, overrides
    )
    douyin_settings = _resolved_source_settings(
        session_factory, "douyin", DouyinSettings, overrides
    )
    youtube_settings = _resolved_source_settings(
        session_factory, "youtube", YouTubeSettings, overrides
    )
    twitter_settings = _resolved_source_settings(
        session_factory, "twitter", TwitterSettings, overrides
    )
    zhihu_settings = _resolved_source_settings(session_factory, "zhihu", ZhihuSettings, overrides)
    reddit_settings = _resolved_source_settings(
        session_factory,
        "reddit",
        RedditSettings,
        overrides,
    )
    registry = build_source_registry(
        bilibili=build_bilibili_connector(
            _LazyBilibiliClient(credentials), task_service, bilibili_settings
        ),
        xiaohongshu=build_xiaohongshu_connector(task_service, xiaohongshu_settings),
        douyin=build_douyin_connector(
            task_service=task_service,
            direct_client=_LazyDouyinClient(credentials),
            settings=douyin_settings,
        ),
        youtube=build_youtube_connector(YtScraperClient(), task_service, youtube_settings),
        twitter=build_twitter_connector(_LazyTwitterClient(credentials), twitter_settings),
        zhihu=build_zhihu_connector(task_service, zhihu_settings),
        reddit=build_reddit_connector(
            task_service=task_service,
            settings=reddit_settings,
        ),
    )
    registry_box["registry"] = registry
    return registry


__all__ = ["MissingSourceConfigurationError", "build_default_source_registry"]
