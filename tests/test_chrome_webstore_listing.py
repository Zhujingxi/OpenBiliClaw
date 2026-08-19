from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LISTING = ROOT / "docs/chrome-webstore-listing.md"
ASSET_DIR = ROOT / "docs/images/chrome-web-store"
EXPECTED = [
    "01-seven-platform-recommendations.png",
    "02-three-surfaces.png",
    "03-truthful-status-local-data.png",
]


def test_store_listing_names_all_first_party_providers_and_local_backend() -> None:
    text = LISTING.read_text(encoding="utf-8")
    for label in (
        "B站",
        "小红书",
        "抖音",
        "YouTube",
        "X",
        "知乎",
        "Reddit",
        "Linux.do",
        "Bangumi",
        "V2EX",
        "Hacker News",
        "微博",
    ):
        assert label in text
    assert "local backend" in text
    assert "User data remains in the configured local backend data directory" in text
    assert "only the provider credential values named by a code-shipped recipe" in text
    assert "only to the user's loopback backend" in text
    assert "No browsing-history or arbitrary page-content collection" in text
    assert "No remote provider task code or background browsing automation" in text


def test_store_listing_assets_have_stable_order_dimensions_and_visual_detail() -> None:
    assert [path.name for path in sorted(ASSET_DIR.glob("*.png"))] == EXPECTED
    for name in EXPECTED:
        with Image.open(ASSET_DIR / name) as image:
            assert image.size == (1280, 800)
            assert image.mode in {"RGB", "RGBA"}
            colors = image.convert("RGB").resize((64, 40)).getcolors(maxcolors=2560) or []
            assert len(colors) > 80


def test_listing_document_declares_dashboard_upload_order() -> None:
    text = LISTING.read_text(encoding="utf-8")
    offsets = [text.index(name) for name in EXPECTED]
    assert offsets == sorted(offsets)
