from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .identity import canonical_source_platform, make_item_key

SavedListKind = Literal["favorite", "watch_later"]
NativeSaveAction = Literal["favorite", "watch_later"]
NativeSaveStatus = Literal[
    "pending",
    "syncing",
    "synced",
    "already_synced",
    "login_required",
    "unsupported",
    "rate_limited",
    "extension_required",
    "failed",
]


@dataclass(frozen=True, slots=True)
class SavedItemInput:
    source_platform: str
    content_id: str
    content_url: str = ""
    content_type: str = "video"
    title: str = ""
    author_name: str = ""
    cover_url: str = ""

    @property
    def item_key(self) -> str:
        return make_item_key(self.source_platform, self.content_id, self.content_url)

    @property
    def platform(self) -> str:
        return canonical_source_platform(self.source_platform)


@dataclass(frozen=True, slots=True)
class SavedMembership:
    list_kind: SavedListKind
    item: SavedItemInput
    note: str = ""


@dataclass(frozen=True, slots=True)
class NativeSaveCapability:
    platform: str
    supports_favorite: bool
    supports_watch_later: bool
    supports_named_collection: bool
    requires_extension: bool = False


@dataclass(frozen=True, slots=True)
class NativeSaveRoute:
    requested_action: NativeSaveAction
    resolved_action: NativeSaveAction
    resolved_target: str


@dataclass(frozen=True, slots=True)
class NativeSaveResult:
    item_key: str
    status: NativeSaveStatus
    resolved_action: NativeSaveAction
    resolved_target: str
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class SavedSyncBatchResult:
    task_id: str
    items: tuple[NativeSaveResult, ...]


@dataclass(frozen=True, slots=True)
class SavedMembershipResult:
    saved: bool
    item_key: str
    sync_status: NativeSaveStatus
    sync_task_id: str = ""
