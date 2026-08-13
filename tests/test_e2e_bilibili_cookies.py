from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from tests.e2e.bilibili_chrome import (
    BrowserCookies,
    chrome_cookie_database,
    connect_command,
    read_selected_cookies,
    snapshot_database,
)

if TYPE_CHECKING:
    from pathlib import Path


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE cookies (
                host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, expires_utc INTEGER
            )"""
        )
        connection.executemany(
            "INSERT INTO cookies VALUES (?, ?, ?, ?, ?)",
            [
                (".bilibili.com", "SESSDATA", "fake-session", b"", 2),
                (".bilibili.com", "bili_jct", "", b"encrypted-csrf", 2),
                (".bilibili.com", "ignored", "fake-ignored", b"", 2),
                (".example.com", "buvid3", "fake-wrong-host", b"", 2),
            ],
        )


def test_database_detection_and_wal_safe_snapshot(tmp_path: Path) -> None:
    source = tmp_path / ".config/google-chrome/Default/Cookies"
    source.parent.mkdir(parents=True)
    _database(source)
    assert chrome_cookie_database(tmp_path) == source

    snapshot = tmp_path / "snapshot.sqlite"
    snapshot_database(source, snapshot)
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT count(*) FROM cookies").fetchone() == (4,)


def test_selected_names_and_submission_shape_use_fake_values(tmp_path: Path) -> None:
    source = tmp_path / "Cookies"
    _database(source)
    cookies = read_selected_cookies(
        source, lambda encrypted: "fake-csrf" if encrypted == b"encrypted-csrf" else ""
    )
    assert cookies.structural_summary() == ("SESSDATA (12 bytes)", "bili_jct (9 bytes)")

    command = connect_command(cookies, "e2e:test:connect")
    assert command.submission == {"cookie": "SESSDATA=fake-session; bili_jct=fake-csrf"}
    assert command.allowed_method_ids == {"builtin.manual"}


def test_browser_cookies_repr_and_str_never_expose_values() -> None:
    cookies = BrowserCookies({"SESSDATA": "fake-session-secret", "bili_jct": "fake-csrf-secret"})
    simulated_assertion_dump = f"cookies = {cookies!r}; rendered = {cookies!s}"
    assert "fake-session-secret" not in simulated_assertion_dump
    assert "fake-csrf-secret" not in simulated_assertion_dump
    assert simulated_assertion_dump == (
        "cookies = BrowserCookies(<redacted>); rendered = BrowserCookies(<redacted>)"
    )


def test_browser_cookies_requires_both_session_fields() -> None:
    try:
        BrowserCookies({"SESSDATA": "fake-session"})
    except RuntimeError as error:
        assert "bili_jct" in str(error)
    else:
        raise AssertionError("missing required name was accepted")
