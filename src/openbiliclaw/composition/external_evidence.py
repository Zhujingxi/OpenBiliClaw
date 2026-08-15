"""Provider-owned archive adapters for the external-evidence workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.application.external_evidence import ExternalImportBatch, ExternalImportItem
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.providers.youtube.takeout import TakeoutEventKind, parse_takeout

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path


def youtube_takeout_import(path: Path, fallback_time: datetime) -> ExternalImportBatch:
    """Adapt Google's real YouTube Takeout format to provider-neutral evidence."""

    parsed = parse_takeout(path)
    supported = tuple(event for event in parsed.events if event.kind is TakeoutEventKind.VIEW)
    return ExternalImportBatch(
        items=tuple(
            ExternalImportItem(
                event_type="external_history_view",
                ref=ContentRef(
                    provider_id=ProviderId(value="youtube"),
                    content_kind=ContentKind(value="video"),
                    provider_content_id=event.provider_content_id,
                    canonical_url=event.canonical_url,
                ),
                title=event.title,
                creator_label=event.creator_label,
                occurred_at=event.occurred_at or fallback_time,
            )
            for event in supported
        ),
        ignored=len(parsed.events) - len(supported),
        warnings=parsed.warnings,
    )
