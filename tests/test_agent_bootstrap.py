from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_bootstrap_module():
    project_root = Path(__file__).resolve().parent.parent
    module_path = project_root / "scripts" / "agent_bootstrap.py"
    spec = importlib.util.spec_from_file_location("openbiliclaw_agent_bootstrap", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bootstrap = _load_bootstrap_module()


def _write_minimal_config(
    tmp_path: Path,
    *,
    embedding_provider: str = "",
    embedding_model: str = "",
) -> None:
    (tmp_path / "config.toml").write_text(
        "\n".join(
            [
                '[llm]',
                'default_provider = "openai"',
                '',
                '[llm.openai]',
                'api_key = "sk-test"',
                '',
                '[llm.embedding]',
                f'provider = "{embedding_provider}"',
                f'model = "{embedding_model}"',
                '',
                '[bilibili]',
                'cookie = "SESSDATA=test; bili_jct=test; DedeUserID=1"',
                '',
            ]
        ),
        encoding="utf-8",
    )


def test_init_decisions_required_when_xhs_and_embedding_were_not_explicit(tmp_path: Path) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=False)

    assert decisions["missing"] == ["embedding", "xhs"]
    assert decisions["xhs"]["policy"] == "pending"
    assert decisions["embedding"]["source"] == "missing"


def test_init_decisions_accept_explicit_no_xhs_and_embedding_choice(tmp_path: Path) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--embedding-provider",
            "ollama",
            "--embedding-model",
            "bge-m3",
            "--no-xhs",
        ]
    )

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=True)

    assert decisions["missing"] == []
    assert decisions["xhs"]["policy"] == "disabled"
    assert decisions["embedding"]["source"] == "flags"


def test_init_decisions_accept_existing_embedding_but_still_require_xhs(tmp_path: Path) -> None:
    _write_minimal_config(tmp_path, embedding_provider="ollama", embedding_model="bge-m3")
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=False)

    assert decisions["missing"] == ["xhs"]
    assert decisions["embedding"]["source"] == "config"


def test_build_init_command_appends_explicit_xhs_flag_for_docker(tmp_path: Path) -> None:
    command = bootstrap.build_init_command("docker", tmp_path, "--yes-xhs")

    assert command == [
        "docker",
        "exec",
        "-i",
        "openbiliclaw-backend",
        "openbiliclaw",
        "init",
        "--yes-xhs",
    ]


def test_parser_rejects_conflicting_xhs_flags(tmp_path: Path) -> None:
    parser = bootstrap.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--project-dir", str(tmp_path), "--yes-xhs", "--no-xhs"])
