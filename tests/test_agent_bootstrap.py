"""Current installer contract tests; legacy provider/init coverage was removed in vNext."""

from __future__ import annotations

import importlib.util
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def _load_bootstrap_module():
    root = Path(__file__).resolve().parent.parent
    path = root / "scripts/agent_bootstrap.py"
    spec = importlib.util.spec_from_file_location("openbiliclaw_agent_bootstrap", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bootstrap = _load_bootstrap_module()


def _values(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )


def test_docker_secrets_are_private_idempotent_and_preserve_unrelated_values(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("UNRELATED=keep\n", encoding="utf-8")

    first = bootstrap.ensure_docker_infrastructure_secrets(tmp_path)
    first_bytes = env_path.read_bytes()
    second = bootstrap.ensure_docker_infrastructure_secrets(tmp_path)

    assert first == second
    assert env_path.read_bytes() == first_bytes
    values = _values(env_path)
    assert values["UNRELATED"] == "keep"
    assert len(values["LITELLM_POSTGRES_PASSWORD"]) == 64
    assert values["LITELLM_MASTER_KEY"].startswith("sk-")
    assert len(values["OPENBILICLAW_SECRET_KEY"]) == 64
    assert len(values["OPENBILICLAW_ACCESS_TOKEN"]) >= 48
    assert len(values["OPENBILICLAW_SESSION_SECRET"]) >= 48
    if os.name != "nt":
        assert env_path.stat().st_mode & 0o777 == 0o600


def test_docker_secret_updates_are_serialized(tmp_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda _index: bootstrap.ensure_docker_infrastructure_secrets(tmp_path),
                range(24),
            )
        )
    lines = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    for key in (
        "LITELLM_POSTGRES_PASSWORD",
        "LITELLM_MASTER_KEY",
        "OPENBILICLAW_SECRET_KEY",
        "OPENBILICLAW_ACCESS_TOKEN",
        "OPENBILICLAW_SESSION_SECRET",
    ):
        assert sum(line.startswith(f"{key}=") for line in lines) == 1
    assert not list(tmp_path.glob(".env.tmp-*"))


@pytest.mark.parametrize("name", (".env", ".env.lock"))
def test_bootstrap_rejects_secret_or_lock_symlink(tmp_path: Path, name: str) -> None:
    target = tmp_path / "outside"
    target.write_text("DO_NOT_TOUCH=1\n", encoding="utf-8")
    (tmp_path / name).symlink_to(target)

    with pytest.raises(RuntimeError, match="symlink|safely"):
        bootstrap.ensure_docker_infrastructure_secrets(tmp_path)
    assert target.read_text(encoding="utf-8") == "DO_NOT_TOUCH=1\n"


def test_parser_exposes_only_vnext_runtime_inputs() -> None:
    parser = bootstrap.build_parser()
    actions = {option for action in parser._actions for option in action.option_strings}

    assert "--litellm-base-url" in actions
    assert "--skip-start" in actions
    assert "--skip-install" in actions
    assert "--litellm-api-key" not in actions
    assert "--provider" not in actions
    assert "--skip-init" not in actions


def test_main_failure_is_nonzero_and_does_not_print_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sentinel = "sentinel-secret"
    monkeypatch.setenv("OPENBILICLAW_LITELLM_API_KEY", sentinel)
    monkeypatch.setattr(
        bootstrap,
        "run",
        lambda _args: (_ for _ in ()).throw(RuntimeError(sentinel)),
    )
    monkeypatch.setattr(sys, "argv", ["agent_bootstrap.py", "--mode", "local"])

    assert bootstrap.main() == 1
    output = capsys.readouterr()
    assert sentinel not in output.out
    assert sentinel not in output.err
    assert "bootstrap_failed" in output.out


def test_bootstrap_source_never_constructs_removed_commands() -> None:
    source = Path(bootstrap.__file__).read_text(encoding="utf-8")
    for command in ('"init"', '"models"', '"recommend"', '"profile"'):
        assert command not in source
