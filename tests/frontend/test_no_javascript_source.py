from pathlib import Path

from scripts.check_frontend_sources import offenders


def test_new_frontend_workspace_contains_no_handwritten_javascript() -> None:
    root = Path(__file__).parents[2] / "frontend"
    assert offenders(root) == ()


def test_frontend_build_output_is_ignored() -> None:
    ignore = (Path(__file__).parents[2] / ".gitignore").read_text()
    assert "frontend/**/dist/" in ignore


def test_javascript_gate_reports_source_but_ignores_generated_output(tmp_path: Path) -> None:
    source = tmp_path / "src" / "bad.js"
    generated = tmp_path / "dist" / "built.js"
    source.parent.mkdir()
    generated.parent.mkdir()
    source.touch()
    generated.touch()
    assert offenders(tmp_path) == (source,)
