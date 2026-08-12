from pathlib import Path

from scripts.check_frontend_sources import offenders, repository_offenders


def test_repository_contains_no_handwritten_javascript() -> None:
    root = Path(__file__).parents[2]
    assert repository_offenders(root) == ()


def test_generated_and_third_party_javascript_is_ignored(tmp_path: Path) -> None:
    source = tmp_path / "src" / "bad.js"
    generated = tmp_path / "frontend" / "dist" / "generated.js"
    dependency = tmp_path / "frontend" / "node_modules" / "dependency.cjs"
    source.parent.mkdir()
    generated.parent.mkdir(parents=True)
    dependency.parent.mkdir(parents=True)
    source.touch()
    generated.touch()
    dependency.touch()
    assert offenders(tmp_path) == (source,)


def test_production_python_does_not_reference_deleted_frontend_sources() -> None:
    root = Path(__file__).parents[2]
    offenders = []
    for base in (root / "src", root / "packaging"):
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".spec"}:
                continue
            text = path.read_text(encoding="utf-8")
            if (
                "src/openbiliclaw/web" in text
                or "extension/popup" in text
                or "extension/scripts" in text
            ):
                offenders.append(path.relative_to(root))
    assert offenders == []
