"""CLI logging integration tests."""

import pytest
from typer.testing import CliRunner

from openbiliclaw import cli as cli_module
from openbiliclaw.cli import app
from openbiliclaw.soul.profile import CoreLayer, OnionProfile, RoleLayer, ValuesLayer


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_cli_log_level_option_overrides_config(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeSoulEngine:
        async def get_profile(self) -> OnionProfile:
            return OnionProfile(
                personality_portrait="已初始化画像",
                core=CoreLayer(core_traits=["理性"], deep_needs=["被理解"]),
                values_layer=ValuesLayer(values=["成长"]),
                role=RoleLayer(life_stage="探索阶段"),
            )

    captured: dict[str, str | None] = {"level": None}

    def fake_init_logging(log_level_override: str | None = None) -> None:
        captured["level"] = log_level_override

    monkeypatch.setattr(cli_module, "_initialize_logging", fake_init_logging)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)

    result = runner.invoke(app, ["--log-level", "DEBUG", "profile"])

    assert result.exit_code == 0
    assert captured["level"] == "DEBUG"
