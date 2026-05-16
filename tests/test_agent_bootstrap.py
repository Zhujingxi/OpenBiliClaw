from __future__ import annotations

import importlib.util
import json
import os
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


def test_bootstrap_extends_no_proxy_for_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "example.com")
    monkeypatch.delenv("no_proxy", raising=False)

    bootstrap.ensure_local_no_proxy()

    assert os.environ["NO_PROXY"] == "example.com,localhost,127.0.0.1,::1"
    assert os.environ["no_proxy"] == "example.com,localhost,127.0.0.1,::1"


def _write_minimal_config(
    tmp_path: Path,
    *,
    embedding_provider: str = "",
    embedding_model: str = "",
) -> None:
    (tmp_path / "config.toml").write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "openai"',
                "",
                "[llm.openai]",
                'api_key = "sk-test"',
                "",
                "[llm.embedding]",
                f'provider = "{embedding_provider}"',
                f'model = "{embedding_model}"',
                "",
                "[bilibili]",
                'cookie = "SESSDATA=test; bili_jct=test; DedeUserID=1"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_init_decisions_required_when_source_and_embedding_were_not_explicit(
    tmp_path: Path,
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=False)

    assert decisions["missing"] == ["embedding", "xhs", "douyin"]
    assert decisions["xhs"]["policy"] == "pending"
    assert decisions["douyin"]["policy"] == "pending"
    assert decisions["embedding"]["source"] == "missing"


def test_init_decisions_accept_explicit_source_and_embedding_choices(tmp_path: Path) -> None:
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
            "--yes-douyin",
        ]
    )

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=True)

    assert decisions["missing"] == []
    assert decisions["xhs"]["policy"] == "disabled"
    assert decisions["douyin"]["policy"] == "enabled"
    assert decisions["embedding"]["source"] == "flags"


def test_init_decisions_accept_existing_embedding_but_still_require_sources(tmp_path: Path) -> None:
    _write_minimal_config(tmp_path, embedding_provider="ollama", embedding_model="bge-m3")
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=False)

    assert decisions["missing"] == ["xhs", "douyin"]
    assert decisions["embedding"]["source"] == "config"


def test_build_init_command_appends_explicit_source_flags_for_docker(tmp_path: Path) -> None:
    command = bootstrap.build_init_command("docker", tmp_path, "--yes-xhs", "--yes-douyin")

    assert command == [
        "docker",
        "exec",
        "-i",
        "openbiliclaw-backend",
        "openbiliclaw",
        "init",
        "--yes-xhs",
        "--yes-douyin",
    ]


def test_run_init_streaming_emits_machine_readable_progress(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command = [
        sys.executable,
        "-c",
        "\n".join(
            [
                "print('1/4 拉取数据', flush=True)",
                "print('  · 分析偏好: 已用 20s / 预计还需 ~50s', flush=True)",
                "print('阶段完成: 当前池子 0/15，本轮发现 20 条', flush=True)",
            ]
        ),
    ]

    returncode = bootstrap.run_init_streaming(command, cwd=None, check=True)

    output = capsys.readouterr().out
    status_lines = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS: "))
        for line in output.splitlines()
        if line.startswith("BOOTSTRAP_STATUS: ")
    ]
    progress_events = [event for event in status_lines if event["message"] == "init_progress"]
    assert returncode == 0
    assert "1/4 拉取数据" in output
    assert any(event["details"]["phase"] == "1/4" for event in progress_events)
    assert any("分析偏好" in event["details"]["line"] for event in progress_events)
    assert any("阶段完成" in event["details"]["line"] for event in progress_events)


def test_parser_rejects_conflicting_xhs_flags(tmp_path: Path) -> None:
    parser = bootstrap.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--project-dir", str(tmp_path), "--yes-xhs", "--no-xhs"])


def test_parser_rejects_conflicting_douyin_flags(tmp_path: Path) -> None:
    parser = bootstrap.build_arg_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--project-dir", str(tmp_path), "--yes-douyin", "--no-douyin"])
