"""Focused contracts for direct source clients retained by the vNext composition."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from openbiliclaw.infrastructure.sources.bilibili_client import (
    BilibiliAPIClient,
    BilibiliAPIError,
)
from openbiliclaw.infrastructure.sources.douyin_client import (
    DouyinDirectAuthError,
    DouyinDirectClient,
    parse_cookie_header,
)
from openbiliclaw.infrastructure.sources.twitter_client import (
    XClient,
    XMissingCookieError,
)
from openbiliclaw.infrastructure.sources.youtube_client import (
    YtScraperClient,
    _channel_uploads_url,
)

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "src" / "openbiliclaw"


def test_retained_direct_client_types_are_infrastructure_owned() -> None:
    assert BilibiliAPIClient.__module__ == "openbiliclaw.infrastructure.sources.bilibili_client"
    assert BilibiliAPIError.__module__ == "openbiliclaw.infrastructure.sources.bilibili_client"
    assert DouyinDirectClient.__module__ == "openbiliclaw.infrastructure.sources.douyin_client"
    assert XClient.__module__ == "openbiliclaw.infrastructure.sources.twitter_client"
    assert YtScraperClient.__module__ == "openbiliclaw.infrastructure.sources.youtube_client"


def test_vnext_source_composition_has_no_legacy_source_graph_imports() -> None:
    forbidden = (
        "openbiliclaw.discovery",
        "openbiliclaw.saved_sync",
        "openbiliclaw.sources",
        "openbiliclaw.storage",
        "openbiliclaw.youtube",
    )
    paths = (
        PACKAGE / "infrastructure" / "jobs" / "source_composition.py",
        *(PACKAGE / "infrastructure" / "sources").glob("*.py"),
    )
    violations: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported = ""
            if isinstance(node, ast.ImportFrom):
                imported = node.module or ""
            elif isinstance(node, ast.Import):
                imported = node.names[0].name
            if imported.startswith(forbidden):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {imported}")
    assert violations == []


def test_reddit_has_no_orphaned_cli_backend_or_dependency() -> None:
    assert not (PACKAGE / "infrastructure" / "sources" / "reddit_cli.py").exists()
    assert "rdt-cli" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_douyin_direct_client_rejects_empty_credentials_without_network() -> None:
    assert parse_cookie_header(" msToken = abc ; ttwid=tw ; invalid ; empty= ") == {
        "msToken": "abc",
        "ttwid": "tw",
    }
    with pytest.raises(DouyinDirectAuthError):
        DouyinDirectClient(cookie="")


def test_twitter_client_rejects_incomplete_cookie_before_network() -> None:
    with pytest.raises(XMissingCookieError):
        XClient(cookie="auth_token=only-token")._auth_pair()


def test_youtube_client_keeps_supported_creator_reference_shapes() -> None:
    assert _channel_uploads_url("@creator") == "https://www.youtube.com/@creator/videos"
    assert _channel_uploads_url("UC123") == "https://www.youtube.com/channel/UC123/videos"
    assert _channel_uploads_url("") == ""
