from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "e2e.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_layer_argument_is_strict() -> None:
    script = load_script()
    assert script.parse_args(["l1a"]).layer == "l1a"
    with pytest.raises(SystemExit):
        script.parse_args(["unknown"])


def test_report_is_machine_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = load_script()
    monkeypatch.setattr(script, "REPORT_DIR", tmp_path)
    report = script.Report("l0", 3, 1, 1.25, ("one failure",))
    path = script.write_report(report)
    assert path.read_text(encoding="utf-8") == (
        '{\n  "layer": "l0",\n  "passed": 3,\n  "failed": 1,\n'
        '  "duration_seconds": 1.25,\n  "failures": [\n    "one failure"\n  ]\n}\n'
    )


def test_seed_profile_regenerates_config_while_preserving_live_vault_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = load_script()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    key_path = data_dir / "key"
    key_path.write_bytes(b"test-key")
    config_path = data_dir / "config.toml"
    config_path.write_text(
        '[model]\nsecret_ref = "vault:cred_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n',
        encoding="utf-8",
    )
    template_path = tmp_path / "template.toml"
    template_path.write_text(
        '[model]\nsecret_ref = "vault:E2E_SECRET_REF"\n[model.options]\ndisable_thinking = true\n',
        encoding="utf-8",
    )

    class Vault:
        def __init__(self, _backend: object) -> None:
            pass

        def resolve(self, secret_ref: str, callback):  # type: ignore[no-untyped-def]
            assert secret_ref == "cred_" + "a" * 32
            return callback(memoryview(b"test-key"))

        def store(self, _secret: bytes) -> str:
            pytest.fail("valid existing reference must be preserved")

    monkeypatch.setattr(script, "DATA_DIR", data_dir)
    monkeypatch.setattr(script, "REPORT_DIR", data_dir / "reports")
    monkeypatch.setattr(script, "CONFIG_PATH", config_path)
    monkeypatch.setattr(script, "TEMPLATE_PATH", template_path)
    monkeypatch.setattr(script, "KEY_PATH", key_path)
    monkeypatch.setattr(script, "ProtectedFileBackend", lambda _path: object())
    monkeypatch.setattr(script, "CredentialVault", Vault)

    script.seed_profile()

    generated = config_path.read_text(encoding="utf-8")
    assert 'secret_ref = "vault:cred_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"' in generated
    assert "disable_thinking = true" in generated


def test_pytest_summary_counts_are_extracted() -> None:
    script = load_script()
    count = cast("int", script._pytest_count("2 failed, 4 passed in 1.00s", "passed"))
    assert count == 4
    assert script._pytest_count("2 failed, 4 passed in 1.00s", "failed") == 2
    assert script._pytest_count(".... [100%]\n4 passed in 5.64s\n", "passed") == 4
    assert script._pytest_count("collection interrupted", "failed") == 0
