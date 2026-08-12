from __future__ import annotations

from pathlib import Path

from scripts.export_openapi import export


def test_openapi_export_is_reproducible_and_matches_snapshot(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    export(first)
    export(second)
    assert first.read_bytes() == second.read_bytes()
    snapshot = Path("tests/hosts/openapi.snapshot.json")
    assert first.read_bytes() == snapshot.read_bytes()
