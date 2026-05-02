"""Tests for configuration management."""

from pathlib import Path

import pytest

from openbiliclaw import config as config_module
from openbiliclaw.config import (
    BilibiliConfig,
    Config,
    ConfigError,
    ConfigIssue,
    LLMConfig,
    LLMProviderConfig,
    SchedulerConfig,
    _build_config,
    load_config,
    load_config_with_diagnostics,
    save_config,
    validate_runtime_config,
)


def _write_example_config(project_root: Path) -> None:
    (project_root / "config.example.toml").write_text(
        """
[general]
language = "zh"
data_dir = "data"

[llm]
default_provider = "openai"

[llm.openai]
api_key = ""
model = "gpt-4o"
base_url = ""

[llm.claude]
api_key = ""
model = "claude-sonnet-4-20250514"

[llm.deepseek]
api_key = ""
model = "deepseek-chat"
base_url = "https://api.deepseek.com"

[llm.ollama]
model = "llama3"
base_url = "http://localhost:11434"

[bilibili]
auth_method = "cookie"
cookie = ""

[bilibili.browser]
executable = ""
headed = false

[scheduler]
enabled = true
discovery_cron = "0 */4 * * *"
account_sync_interval_hours = 6

[storage]
db_path = "data/openbiliclaw.db"
""".strip(),
        encoding="utf-8",
    )


class TestConfigDefaults:
    """Test default configuration values."""

    def test_default_config(self) -> None:
        config = Config()
        assert config.language == "zh"
        assert config.llm.default_provider == "openai"
        assert config.bilibili.auth_method == "cookie"
        assert config.scheduler.enabled is True
        assert config.scheduler.pool_target_count == 600

    def test_config_defaults_pool_target_count_to_600(self) -> None:
        config = Config()

        assert config.scheduler.pool_target_count == 600

    def test_build_from_empty_dict(self) -> None:
        config = _build_config({})
        assert config.language == "zh"
        assert config.llm.default_provider == "openai"

    def test_build_from_partial_dict(self) -> None:
        raw = {
            "general": {"language": "en"},
            "llm": {"default_provider": "claude"},
        }
        config = _build_config(raw)
        assert config.language == "en"
        assert config.llm.default_provider == "claude"
        # Other defaults should remain
        assert config.bilibili.auth_method == "cookie"

    def test_data_path_relative(self) -> None:
        config = Config(data_dir="data")
        # Should resolve to an absolute path
        assert config.data_path.is_absolute()

    def test_data_path_absolute(self) -> None:
        config = Config(data_dir="/tmp/openbiliclaw_test")
        assert config.data_path == Path("/tmp/openbiliclaw_test")

    def test_load_config_missing_file(self) -> None:
        """Should return defaults when no config file exists."""
        config = load_config("/nonexistent/path/config.toml")
        assert config.language == "zh"

    def test_build_logging_config(self) -> None:
        raw = {
            "logging": {
                "level": "WARNING",
                "file_level": "DEBUG",
                "directory": "runtime_logs",
                "filename": "app.log",
            }
        }

        config = _build_config(raw)

        assert config.logging.level == "WARNING"
        assert config.logging.file_level == "DEBUG"
        assert config.logging.directory == "runtime_logs"
        assert config.logging.filename == "app.log"

    def test_logging_rotation_defaults(self) -> None:
        config = Config()

        # v0.3.30+: lowered max_file_size_mb default 1024 → 100. Long-running
        # daemon previously accumulated 1 GB before rotating, which is too
        # large per-active-log; 100 MB × 2 backups = 200 MB cap is plenty
        # for 1-2 weeks of INFO traffic.
        assert config.logging.max_file_size_mb == 100
        assert config.logging.backup_count == 1
        # v0.3.30+: aggregate-budget + unmanaged-file cleanup defaults
        assert config.logging.aggregate_budget_mb == 500
        assert config.logging.unmanaged_truncate_mb == 200
        assert config.logging.unmanaged_max_age_days == 30

    def test_build_logging_config_parses_rotation_fields(self) -> None:
        raw = {
            "logging": {
                "max_file_size_mb": 256,
                "backup_count": 3,
            }
        }

        config = _build_config(raw)

        assert config.logging.max_file_size_mb == 256
        assert config.logging.backup_count == 3


def test_load_config_with_diagnostics_creates_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", tmp_path)
    _write_example_config(tmp_path)

    config, diagnostics = load_config_with_diagnostics()

    assert config.language == "zh"
    assert (tmp_path / "config.toml").exists()
    assert diagnostics.created_default_config is True
    assert diagnostics.config_path == tmp_path / "config.toml"
    assert ConfigIssue(
        field="llm.openai.api_key",
        message="默认 provider `openai` 缺少 `api_key`，请在 config.toml 中填写。",
    ) in diagnostics.issues


def test_load_config_prefers_current_working_directory_for_default_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", Path("/usr/local/lib/python3.11"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(
        """
[general]
language = "en"
data_dir = "runtime-data"

[llm]
default_provider = "ollama"

[llm.ollama]
model = "llama3"
base_url = "http://localhost:11434"
""".strip(),
        encoding="utf-8",
    )

    config, diagnostics = load_config_with_diagnostics(ensure_default_file=False)

    assert config.language == "en"
    assert config.data_path == tmp_path / "runtime-data"
    assert diagnostics.config_path == tmp_path / "config.toml"


def test_validate_runtime_config_requires_api_key_for_default_provider() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key=""),
        )
    )

    with pytest.raises(ConfigError, match="llm.openai.api_key"):
        validate_runtime_config(config)


def test_validate_runtime_config_allows_ollama_without_api_key() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
        )
    )

    validate_runtime_config(config)


def test_build_config_supports_openrouter_provider() -> None:
    config = _build_config(
        {
            "llm": {
                "default_provider": "openrouter",
                "openrouter": {
                    "api_key": "test-key",
                    "model": "openai/gpt-4o-mini",
                    "base_url": "https://openrouter.ai/api/v1",
                    "http_referer": "https://example.com",
                    "x_title": "OpenBiliClaw",
                },
            }
        }
    )

    assert config.llm.default_provider == "openrouter"
    assert config.llm.openrouter.api_key == "test-key"
    assert config.llm.openrouter.model == "openai/gpt-4o-mini"
    assert config.llm.openrouter.base_url == "https://openrouter.ai/api/v1"
    assert config.llm.openrouter.http_referer == "https://example.com"
    assert config.llm.openrouter.x_title == "OpenBiliClaw"


def test_validate_runtime_config_requires_openrouter_api_key() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openrouter",
            openrouter=LLMProviderConfig(api_key="", model="openai/gpt-4o-mini"),
        )
    )

    with pytest.raises(ConfigError, match="llm.openrouter.api_key"):
        validate_runtime_config(config)


def test_build_config_supports_gemini_provider() -> None:
    config = _build_config(
        {
            "llm": {
                "default_provider": "gemini",
                "gemini": {
                    "api_key": "test-key",
                    "model": "gemini-2.5-flash",
                },
            }
        }
    )

    assert config.llm.default_provider == "gemini"
    assert config.llm.gemini.api_key == "test-key"
    assert config.llm.gemini.model == "gemini-2.5-flash"


def test_validate_runtime_config_allows_gemini_env_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    config = Config(
        llm=LLMConfig(
            default_provider="gemini",
            gemini=LLMProviderConfig(api_key="", model="gemini-2.5-flash"),
        )
    )

    validate_runtime_config(config)


def test_validate_runtime_config_requires_gemini_api_key() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="gemini",
            gemini=LLMProviderConfig(api_key="", model="gemini-2.5-flash"),
        )
    )

    with pytest.raises(ConfigError, match="llm.gemini.api_key"):
        validate_runtime_config(config)


def test_validate_runtime_config_rejects_invalid_auth_method() -> None:
    config = Config(bilibili=BilibiliConfig(auth_method="invalid"))

    with pytest.raises(ConfigError, match="bilibili.auth_method"):
        validate_runtime_config(config)


def test_validate_runtime_config_rejects_pool_target_count_above_cap() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
        ),
        scheduler=SchedulerConfig(
            enabled=True,
            discovery_cron="0 */4 * * *",
            pool_target_count=601,
            account_sync_interval_hours=6,
        )
    )

    with pytest.raises(ConfigError, match="scheduler.pool_target_count"):
        validate_runtime_config(config)


def test_build_config_supports_account_sync_interval() -> None:
    config = _build_config(
        {
            "scheduler": {
                "enabled": True,
                "discovery_cron": "0 */4 * * *",
                "pool_target_count": 30,
                "account_sync_interval_hours": 12,
            }
        }
    )

    assert config.scheduler.account_sync_interval_hours == 12


def test_build_config_supports_sources_browser_cdp_url() -> None:
    config = _build_config(
        {
            "sources": {
                "browser": {
                    "cdp_url": "http://127.0.0.1:9222",
                    "headed": True,
                }
            }
        }
    )

    assert config.sources.browser_cdp_url == "http://127.0.0.1:9222"
    assert config.sources.browser_headed is True


def test_sources_browser_defaults_are_empty() -> None:
    config = _build_config({})

    assert config.sources.browser_cdp_url == ""
    assert config.sources.browser_headed is False


def test_sources_xiaohongshu_defaults() -> None:
    config = _build_config({})

    assert config.sources.xiaohongshu.daily_search_budget == 30
    assert config.sources.xiaohongshu.daily_creator_budget == 10
    assert config.sources.xiaohongshu.task_interval_seconds == 45


def test_build_config_supports_sources_xiaohongshu(tmp_path: Path) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        """
[sources.xiaohongshu]
daily_search_budget = 30
daily_creator_budget = 5
task_interval_seconds = 60
""".strip(),
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.sources.xiaohongshu.daily_search_budget == 30
    assert config.sources.xiaohongshu.daily_creator_budget == 5
    assert config.sources.xiaohongshu.task_interval_seconds == 60


def test_save_config_round_trips_sources_browser_cdp_url(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.sources.browser_cdp_url = "http://127.0.0.1:9222"

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.sources.browser_cdp_url == "http://127.0.0.1:9222"


def test_save_config_round_trips_runtime_changes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.language = "en"
    config.data_dir = "runtime-data"
    config.llm.default_provider = "gemini"
    config.llm.gemini.api_key = "gemini-test-key"
    config.llm.gemini.model = "gemini-2.5-flash"

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.language == "en"
    assert loaded.data_dir == "runtime-data"
    assert loaded.llm.default_provider == "gemini"
    assert loaded.llm.gemini.api_key == "gemini-test-key"
    assert loaded.llm.gemini.model == "gemini-2.5-flash"
