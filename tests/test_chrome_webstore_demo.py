from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

from PIL import Image
from scripts.chrome_webstore_demo import (
    DEMO_COVER_HOST,
    DemoServer,
    demo_payload,
)


def test_demo_recommendations_cover_multiple_platforms_without_private_data() -> None:
    status, payload = demo_payload("/api/recommendations")
    assert status == 200
    assert {item["source_platform"] for item in payload["items"]} >= {
        "bilibili",
        "xiaohongshu",
        "zhihu",
        "reddit",
    }
    serialized = repr(payload)
    assert "cookie" not in serialized.lower()
    assert "token" not in serialized.lower()


def test_demo_source_status_uses_truthful_login_states() -> None:
    status, payload = demo_payload("/api/sources/status")
    assert status == 200
    assert payload["xiaohongshu"]["state"] == "ready"
    assert payload["douyin"]["state"] == "unverified"
    assert payload["reddit"]["detail"].endswith("未实时访问 Reddit 验证）。")


def test_demo_recommendations_and_delight_use_local_generated_covers() -> None:
    status, recommendations = demo_payload("/api/recommendations")
    assert status == 200
    assert len(recommendations["items"]) == 7
    for item in recommendations["items"]:
        parsed = urlsplit(item["cover_url"])
        assert parsed.scheme == "https"
        assert parsed.hostname == DEMO_COVER_HOST

    status, delight = demo_payload("/api/delight/pending-batch")
    assert status == 200
    assert len(delight["items"]) == 1
    assert urlsplit(delight["items"][0]["cover_url"]).hostname == DEMO_COVER_HOST


def test_demo_cover_files_are_complete_16_by_9_images() -> None:
    cover_dir = Path("docs/images/chrome-web-store/demo-covers")
    covers = sorted(cover_dir.glob("*.png"))
    assert len(covers) == 8
    for cover in covers:
        with Image.open(cover) as image:
            assert image.size == (640, 360)
            assert image.mode in {"RGB", "RGBA"}


def test_demo_image_proxy_serves_only_known_local_cover_hosts() -> None:
    _, recommendations = demo_payload("/api/recommendations")
    cover_url = recommendations["items"][0]["cover_url"]
    with (
        DemoServer() as origin,
        urlopen(f"{origin}/api/image-proxy?url={quote(cover_url, safe='')}") as response,
    ):
        assert response.headers.get_content_type() == "image/png"
        assert response.read(8) == b"\x89PNG\r\n\x1a\n"
