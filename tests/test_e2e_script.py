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


def test_seed_profiles_regenerate_configs_while_preserving_live_vault_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = load_script()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    kimi_key_path = data_dir / "kimi-key"
    kimi_key_path.write_bytes(b"test-kimi-key")
    deepseek_key_path = data_dir / "deepseek-key"
    deepseek_key_path.write_bytes(b"test-deepseek-key")
    kimi_config_path = data_dir / "kimi.toml"
    kimi_config_path.write_text(
        '[model]\nsecret_ref = "vault:cred_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n',
        encoding="utf-8",
    )
    deepseek_config_path = data_dir / "deepseek.toml"
    deepseek_config_path.write_text(
        '[model]\nsecret_ref = "vault:cred_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"\n',
        encoding="utf-8",
    )
    kimi_template_path = tmp_path / "kimi-template.toml"
    kimi_template_path.write_text(
        '[model]\nsecret_ref = "vault:E2E_SECRET_REF"\n[model.options]\ndisable_thinking = true\n',
        encoding="utf-8",
    )
    deepseek_template_path = tmp_path / "deepseek-template.toml"
    deepseek_template_path.write_text(
        '[model]\nsecret_ref = "vault:E2E_SECRET_REF"\n', encoding="utf-8"
    )
    resolved: list[str] = []

    class Vault:
        def __init__(self, _backend: object) -> None:
            pass

        def resolve(self, secret_ref: str, callback):  # type: ignore[no-untyped-def]
            resolved.append(secret_ref)
            key = b"test-kimi-key" if secret_ref.endswith("a") else b"test-deepseek-key"
            return callback(memoryview(key))

        def store(self, _secret: bytes) -> str:
            pytest.fail("valid existing references must be preserved")

    monkeypatch.setattr(script, "DATA_DIR", data_dir)
    monkeypatch.setattr(script, "REPORT_DIR", data_dir / "reports")
    monkeypatch.setattr(script, "CONFIG_PATH", kimi_config_path)
    monkeypatch.setattr(script, "TEMPLATE_PATH", kimi_template_path)
    monkeypatch.setattr(script, "KEY_PATH", kimi_key_path)
    monkeypatch.setattr(script, "DEEPSEEK_CONFIG_PATH", deepseek_config_path)
    monkeypatch.setattr(script, "DEEPSEEK_TEMPLATE_PATH", deepseek_template_path)
    monkeypatch.setattr(script, "DEEPSEEK_KEY_PATH", deepseek_key_path)
    monkeypatch.setattr(script, "ProtectedFileBackend", lambda _path: object())
    monkeypatch.setattr(script, "CredentialVault", Vault)

    script.seed_profile()

    kimi_config = kimi_config_path.read_text(encoding="utf-8")
    deepseek_config = deepseek_config_path.read_text(encoding="utf-8")
    assert 'secret_ref = "vault:cred_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"' in kimi_config
    assert "disable_thinking = true" in kimi_config
    assert 'secret_ref = "vault:cred_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"' in deepseek_config
    assert "disable_thinking" not in deepseek_config
    assert resolved == ["cred_" + "a" * 32, "cred_" + "b" * 32]


def test_seed_config_replaces_resolvable_but_mismatched_vault_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolvable ref pointing at the WRONG key must be re-stored, not trusted."""
    script = load_script()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    kimi_key_path = data_dir / "kimi-key"
    kimi_key_path.write_bytes(b"test-kimi-key")
    kimi_config_path = data_dir / "kimi.toml"
    kimi_config_path.write_text(
        '[model]\nsecret_ref = "vault:cred_cccccccccccccccccccccccccccccccc"\n',
        encoding="utf-8",
    )
    kimi_template_path = tmp_path / "kimi-template.toml"
    kimi_template_path.write_text(
        '[model]\nsecret_ref = "vault:E2E_SECRET_REF"\n', encoding="utf-8"
    )
    stored: list[bytes] = []

    class Vault:
        def __init__(self, _backend: object) -> None:
            pass

        def resolve(self, secret_ref: str, callback):  # type: ignore[no-untyped-def]
            return callback(memoryview(b"test-deepseek-key"))  # wrong key, but resolves

        def store(self, secret: bytes) -> str:
            stored.append(secret)
            return "cred_" + "d" * 32

    monkeypatch.setattr(script, "DATA_DIR", data_dir)
    monkeypatch.setattr(script, "REPORT_DIR", data_dir / "reports")
    monkeypatch.setattr(script, "CONFIG_PATH", kimi_config_path)
    monkeypatch.setattr(script, "TEMPLATE_PATH", kimi_template_path)
    monkeypatch.setattr(script, "KEY_PATH", kimi_key_path)
    monkeypatch.setattr(script, "DEEPSEEK_KEY_PATH", data_dir / "missing")
    monkeypatch.setattr(script, "ProtectedFileBackend", lambda _path: object())
    monkeypatch.setattr(script, "CredentialVault", Vault)

    script.seed_profile()

    assert stored == [b"test-kimi-key"]
    assert 'secret_ref = "vault:cred_' + "d" * 32 + '"' in kimi_config_path.read_text(
        encoding="utf-8"
    )


def test_seed_profile_stores_both_fake_keys_when_references_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = load_script()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    kimi_key_path = data_dir / "kimi-key"
    kimi_key_path.write_bytes(b"fake-kimi-key")
    deepseek_key_path = data_dir / "deepseek-key"
    deepseek_key_path.write_bytes(b"fake-deepseek-key")
    kimi_template_path = tmp_path / "kimi-template.toml"
    kimi_template_path.write_text('secret_ref = "vault:E2E_SECRET_REF"\n', encoding="utf-8")
    deepseek_template_path = tmp_path / "deepseek-template.toml"
    deepseek_template_path.write_text('secret_ref = "vault:E2E_SECRET_REF"\n', encoding="utf-8")
    stored: list[bytes] = []

    class Vault:
        def __init__(self, _backend: object) -> None:
            pass

        def store(self, secret: bytes) -> str:
            stored.append(secret)
            return "cred_" + ("a" if len(stored) == 1 else "b") * 32

    monkeypatch.setattr(script, "DATA_DIR", data_dir)
    monkeypatch.setattr(script, "REPORT_DIR", data_dir / "reports")
    monkeypatch.setattr(script, "CONFIG_PATH", data_dir / "kimi.toml")
    monkeypatch.setattr(script, "TEMPLATE_PATH", kimi_template_path)
    monkeypatch.setattr(script, "KEY_PATH", kimi_key_path)
    monkeypatch.setattr(script, "DEEPSEEK_CONFIG_PATH", data_dir / "deepseek.toml")
    monkeypatch.setattr(script, "DEEPSEEK_TEMPLATE_PATH", deepseek_template_path)
    monkeypatch.setattr(script, "DEEPSEEK_KEY_PATH", deepseek_key_path)
    monkeypatch.setattr(script, "ProtectedFileBackend", lambda _path: object())
    monkeypatch.setattr(script, "CredentialVault", Vault)

    script.seed_profile()

    assert stored == [b"fake-kimi-key", b"fake-deepseek-key"]
    assert "cred_" + "a" * 32 in (data_dir / "kimi.toml").read_text(encoding="utf-8")
    assert "cred_" + "b" * 32 in (data_dir / "deepseek.toml").read_text(encoding="utf-8")


def test_pytest_summary_counts_are_extracted() -> None:
    script = load_script()
    count = cast("int", script._pytest_count("2 failed, 4 passed in 1.00s", "passed"))
    assert count == 4
    assert script._pytest_count("2 failed, 4 passed in 1.00s", "failed") == 2
    assert script._pytest_count(".... [100%]\n4 passed in 5.64s\n", "passed") == 4
    assert script._pytest_count("collection interrupted", "failed") == 0
