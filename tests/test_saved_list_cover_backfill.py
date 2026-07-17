"""Issue #111 follow-up: saved-list rows backfill empty cover/title/author.

Bilibili/Reddit items were saved into watch_later/favorite with an empty
cover_url even though content_cache holds the cover, so saved cards rendered a
blank thumbnail. `list_saved_memberships` now backfills empty cover_url / title
/ author_name from content_cache (keyed by content_id or bvid). Backfill only
fills EMPTY fields — an item's own value is preserved.
"""

from pathlib import Path

from openbiliclaw.saved_sync.models import SavedItemInput
from openbiliclaw.storage.database import Database


def _insert_cache(db: Database, **cols: str) -> None:
    conn = db.open_connection()
    keys = ", ".join(cols)
    placeholders = ", ".join("?" * len(cols))
    conn.execute(
        f"INSERT INTO content_cache ({keys}) VALUES ({placeholders})",
        tuple(cols.values()),
    )
    conn.commit()


def test_saved_list_backfills_empty_cover_title_author_from_content_cache(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "backfill.db")
    db.initialize()
    # Saved without cover / title / author (a save path that dropped them).
    db.upsert_saved_membership("watch_later", SavedItemInput("bilibili", "BV1COVER"))
    _insert_cache(
        db,
        bvid="BV1COVER",
        content_id="BV1COVER",
        source_platform="bilibili",
        title="真·标题",
        up_name="落锦墨",
        cover_url="https://i1.hdslb.com/x.jpg",
    )

    row = db.list_saved_memberships("watch_later")[0]
    assert row["cover_url"] == "https://i1.hdslb.com/x.jpg"
    assert row["title"] == "真·标题"
    assert row["author_name"] == "落锦墨"


def test_saved_list_preserves_items_own_cover(tmp_path: Path) -> None:
    db = Database(tmp_path / "backfill_own.db")
    db.initialize()
    db.upsert_saved_membership(
        "favorite", SavedItemInput("bilibili", "BV1HAS", cover_url="https://own.jpg")
    )
    _insert_cache(
        db,
        bvid="BV1HAS",
        content_id="BV1HAS",
        source_platform="bilibili",
        cover_url="https://cache.jpg",
    )

    assert db.list_saved_memberships("favorite")[0]["cover_url"] == "https://own.jpg"


def test_saved_list_without_content_cache_row_is_safe(tmp_path: Path) -> None:
    db = Database(tmp_path / "backfill_none.db")
    db.initialize()
    db.upsert_saved_membership("watch_later", SavedItemInput("bilibili", "BV1NONE"))
    # No matching content_cache row → cover stays empty, no crash.
    assert db.list_saved_memberships("watch_later")[0]["cover_url"] == ""
