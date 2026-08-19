import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_docs_homepage_matches_current_provider_and_extension_boundaries() -> None:
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
    assert "V2EX 推荐" in html
    assert "sourceV2exTitle" in html
    assert "sourceV2exText" in html
    assert "sourceHnTitle" in html
    assert "sourceHnText" in html
    assert "sourceWeiboTitle" in html
    assert "sourceWeiboText" in html
    assert "按后端声明的配方读取指定 Cookie/站点存储" in html
    assert "只回传到本机" in html
    assert "不采集浏览行为" in html
    # The extension uses declarative credential recipes, not behavior capture or task bridges.
    for stale in ("行为采集", "采集行为", "登录态任务桥", "插件任务桥", "同步登录态", "rdt-cli"):
        assert stale not in html
    assert "/m/" not in html
    assert f'"softwareVersion": "{project_version}"' in html


def test_maintained_markdown_links_resolve() -> None:
    documents = [ROOT / name for name in ("README.md", "README_EN.md", "AGENTS.md")]
    documents.extend(
        path
        for path in (ROOT / "docs").rglob("*.md")
        if path.name
        != "changelog.md"  # release ledger intentionally names removed historical files
    )
    markdown_link = re.compile(r"\[[^]]*\]\(([^) ]+)(?:\s+\"[^\"]*\")?\)")

    dangling: list[str] = []
    for document in documents:
        for target in markdown_link.findall(document.read_text(encoding="utf-8")):
            path = target.split("#", 1)[0]
            if not path or "://" in path or path.startswith(("mailto:", "data:")):
                continue
            if not (document.parent / path).resolve().exists():
                dangling.append(f"{document.relative_to(ROOT)} -> {path}")

    assert dangling == []


def test_superseded_document_archives_are_absent() -> None:
    for name in ("refactor", "specs", "superpowers", "testing"):
        assert not (ROOT / "docs" / name).exists()

    assert not list((ROOT / "docs" / "plans").glob("*.md"))


def test_docs_homepage_mentions_macos_first_launch_security_bypass() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")

    assert "OpenBiliClaw-macos-v*-arm64.dmg" in html
    assert "Control-click" in html
    assert "隐私与安全性" in html
    assert "已损坏" in html
    assert "xattr -dr com.apple.quarantine /Applications/OpenBiliClaw.app" in html
    assert "README bypass steps" not in html


def test_docs_homepage_does_not_call_github_rest_from_the_browser() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")

    assert "api.github.com" not in html
    assert "stargazers_count" not in html
    assert "https://github.com/whiteguo233/OpenBiliClaw" in html
