import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_docs_homepage_mentions_reddit_and_bangumi_sources() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    project_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]

    assert "Reddit 推荐" in html
    assert "sourceRedditTitle" in html
    assert "sourceRedditText" in html
    assert "Bangumi 推荐" in html
    assert "sourceBangumiTitle" in html
    assert "sourceBangumiText" in html
    assert "知乎 / Reddit 登录态任务桥" in html
    assert "Zhihu, Reddit, Bangumi, and Web sources" in html
    # Bangumi host permission IS requested now, for identity-only recognition.
    # The homepage must describe it truthfully: no cookies, no browsing capture.
    assert "在 `bgm.tv` / `bangumi.tv` 上它仅做身份识别" in html
    assert "不读 Cookie、不采集浏览行为" in html
    assert f'"softwareVersion": "{project_version}"' in html


def test_docs_homepage_mentions_macos_first_launch_security_bypass() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")

    assert "OpenBiliClaw-macos-v*-arm64.dmg" in html
    assert "Control-click" in html
    assert "隐私与安全性" in html
    assert "已损坏" in html
    assert "xattr -dr com.apple.quarantine /Applications/OpenBiliClaw.app" in html
    assert "README bypass steps" not in html
