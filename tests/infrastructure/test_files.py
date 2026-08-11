from pathlib import Path

import pytest

from openbiliclaw.infrastructure.files import BoundedFiles


def test_atomic_write_read_and_bounds(tmp_path: Path) -> None:
    files = BoundedFiles(tmp_path, max_bytes=5)
    files.write("nested/value.bin", b"hello")
    assert files.read("nested/value.bin") == b"hello"
    assert not list(tmp_path.rglob(".value.bin.*"))
    with pytest.raises(ValueError):
        files.write("large", b"123456")
    with pytest.raises(ValueError):
        files.read("../outside")


def test_invalid_file_configuration_and_oversized_read(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        BoundedFiles(tmp_path, max_bytes=0)
    (tmp_path / "large").write_bytes(b"123456")
    files = BoundedFiles(tmp_path, max_bytes=5)
    with pytest.raises(ValueError, match="size"):
        files.read("large")
    with pytest.raises(ValueError, match="relative"):
        files.read("/absolute")


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    files = BoundedFiles(tmp_path)
    with pytest.raises(ValueError):
        files.write("link/value", b"no")
