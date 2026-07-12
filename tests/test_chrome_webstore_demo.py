from scripts.chrome_webstore_demo import demo_payload


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
