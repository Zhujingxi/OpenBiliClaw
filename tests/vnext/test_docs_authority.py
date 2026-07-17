from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VNEXT_MODULE_DOCS = tuple(
    path.relative_to(ROOT).as_posix() for path in sorted((ROOT / "docs/modules").glob("vnext-*.md"))
)


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _section(name: str, start: str, end: str) -> str:
    source = _read(name)
    return source.split(start, 1)[1].split(end, 1)[0]


@pytest.mark.parametrize("name", VNEXT_MODULE_DOCS)
def test_vnext_module_docs_do_not_describe_task21_as_future(name: str) -> None:
    source = _read(name)
    stale_claims = (
        r"Task 21",
        r"尚未接入公开 API",
        r"等待 Task 21 HTTP 接线",
        r"尚未新增 `/api/v1/source-tasks`",
        r"尚未在生产 composition root 构造",
        r"当前生产 API、legacy runtime、CLI 和前端尚未切换",
        r"legacy storage/runtime 仍是公开请求权威",
        r"当前安装器尚未接线该 secret",
        r"v0\.3 legacy 模型路由仍是现有用户请求的权威",
        r"为后续 application use case",
        r"后续 cutover 任务",
    )
    for claim in stale_claims:
        assert re.search(claim, source) is None, f"stale authority claim in {name}: {claim}"


def test_current_architecture_and_spec_do_not_defer_authoritative_backend() -> None:
    current_architecture = _read("docs/architecture.md").split("## 已停止作为入口的 v0.3 实现", 1)[
        0
    ]
    current_spec = _section(
        "docs/spec.md",
        "### 3.1 vNext",
        "### 3.2",
    )
    stale_claims = (
        "chat adapter 等待 HTTP 接线",
        "v0.3 API、legacy runtime、扩展 dispatcher 和四端客户端尚未切换",
        "Deferred: HTTP/extension composition",
        "installer source-secret lifecycle",
        "v0.3 legacy storage/runtime for public requests",
    )
    for source in (current_architecture, current_spec):
        for claim in stale_claims:
            assert claim not in source


def test_current_docs_name_the_runtime_authorities() -> None:
    expected = {
        "docs/modules/vnext-domain.md": ("/api/v1", "权威"),
        "docs/modules/vnext-persistence.md": ("openbiliclaw db backup", "OPENBILICLAW_SECRET_KEY"),
        "docs/modules/vnext-ai.md": ("/api/v1/chat", "SSE"),
        "docs/modules/vnext-sources.md": ("/api/v1/source-tasks", "claim"),
        "docs/modules/vnext-api.md": ("only public API namespace", "generated clients"),
        "docs/modules/cli.md": ("operations interface only", "openbiliclaw doctor"),
        "docs/modules/config.md": ("GET/PATCH /api/v1/settings", "generated API client"),
        "docs/modules/api-auth.md": ("/api/v1/auth", "generated clients"),
    }
    for name, markers in expected.items():
        source = _read(name)
        for marker in markers:
            assert marker in source, f"missing authority marker in {name}: {marker}"


@pytest.mark.parametrize(
    ("name", "authority_marker"),
    (("README.md", "权威运行时"), ("README_EN.md", "authoritative runtime")),
)
def test_readmes_name_vnext_backend_as_authoritative(name: str, authority_marker: str) -> None:
    source = _read(name)
    for marker in (
        authority_marker,
        "/api/v1/source-tasks",
        "TaskRunner",
        "SSE",
        "openbiliclaw doctor",
        "generated",
    ):
        assert marker in source


def test_task22_references_are_only_historical_or_client_scoped() -> None:
    active_surfaces = {
        name: _read(name)
        for name in (
            "README.md",
            "README_EN.md",
            "docs/index.md",
            "docs/modules/vnext-api.md",
            "docs/modules/config.md",
            "docs/modules/api-auth.md",
            "docs/agent-install.md",
            "docs/agent-deployment.md",
            "docs/docker-deployment.md",
            "scripts/install.sh",
            "scripts/install.ps1",
            "docker-compose.yml",
            "docker-compose.prebuilt.yml",
            *VNEXT_MODULE_DOCS,
        )
    }
    active_surfaces["docs/architecture.md"] = _read("docs/architecture.md").split(
        "## 已停止作为入口的 v0.3 实现", 1
    )[0]
    active_surfaces["docs/spec.md"] = _section("docs/spec.md", "### 3.1 vNext", "### 3.2")
    active_surfaces["docs/changelog.md"] = _read("docs/changelog.md").split(
        "### Historical delivery sequence", 1
    )[0]
    active_surfaces["docs/platform-source-integration.md"] = _read(
        "docs/platform-source-integration.md"
    ).split("## Historical v0.3 archive", 1)[0]

    for name, source in active_surfaces.items():
        assert "Task 21" not in source
        for match in re.finditer("Task 22", source):
            context = source[max(0, match.start() - 160) : match.end() + 160]
            assert re.search(
                r"web|extension|ui|client|dispatcher|前端|扩展", context, re.IGNORECASE
            ), name


def test_synchronized_current_docs_keep_core_authority_markers() -> None:
    current = {
        "README.md": _read("README.md"),
        "README_EN.md": _read("README_EN.md"),
        "docs/architecture.md": _read("docs/architecture.md").split(
            "## 已停止作为入口的 v0.3 实现", 1
        )[0],
        "docs/spec.md": _section("docs/spec.md", "### 3.1 vNext", "### 3.2"),
        "docs/changelog.md": _read("docs/changelog.md").split(
            "### Historical delivery sequence", 1
        )[0],
    }
    for name, source in current.items():
        for marker in ("/api/v1", "source-task", "TaskRunner", "chat"):
            assert marker.lower() in source.lower(), f"missing {marker} in {name}"
        assert re.search(r"secret|密钥|凭据", source, re.IGNORECASE), name


def test_historical_v03_material_is_explicitly_archived() -> None:
    spec = _read("docs/spec.md")
    architecture = _read("docs/architecture.md")
    changelog = _read("docs/changelog.md")

    assert "Historical v0.3 archive" in spec
    assert "已停止作为入口的 v0.3 实现" in architecture
    assert "Historical delivery sequence" in changelog
    source_guide = _read("docs/platform-source-integration.md")
    assert "当前权威合同" in source_guide
    assert "Historical v0.3 archive" in source_guide


def test_config_doc_matches_current_strict_user_settings_schema() -> None:
    source = _read("docs/modules/config.md")
    for group in (
        "`sources`",
        "`schedules`",
        "`feed`",
        "`profile`",
        "`tasks.<task-name>`",
        "`network`",
        "`logging`",
        "`access_control`",
        "`jobs`",
    ):
        assert group in source
    for field in (
        "enabled",
        "weights",
        "source_sync_interval_minutes",
        "low_watermark",
        "high_watermark",
        "minimum_evidence_confidence",
        "model_alias",
        "semantic_retry_limit",
        "timeout_seconds",
        "request_limit",
        "total_tokens_limit",
        "proxy_url",
        "console_level",
        "file_level",
        "web_password_enabled",
        "trust_loopback",
        "session_ttl_hours",
        "extension_access_enabled",
        "extension_session_ttl_hours",
        "retention_days",
    ):
        assert field in source
    for read_only_field in (
        "onboarding_complete",
        "directory",
        "worker_concurrency",
        "installer_bearer_configured",
        "password_configured",
    ):
        assert read_only_field in source
    assert "read-only" in source


def test_task21b_contract_docs_name_safe_browser_boundaries() -> None:
    expected = {
        "docs/modules/api-auth.md": (
            "HttpOnly",
            "X-OBC-Auth",
            "extension-token",
            "auth_state.session_epoch",
            "never receive `trust_loopback`",
            "bounded per-peer failure limiters",
            "environment-only",
            "explicit `disabled` sentinel",
            "cannot survive removal or revive after re-enable",
            "installer-generated random `OPENBILICLAW_SESSION_SECRET`",
        ),
        "docs/modules/vnext-api.md": (
            "LibraryItem",
            "ProfileEdit",
            "ChatHistoryPage",
            "ErrorEnvelope",
            "`405`",
            "deliberate exception",
            "finite extension bearer",
            "installer/infrastructure/provider bearer",
        ),
        "docs/modules/vnext-sources.md": (
            "settings_schema",
            "credential_schema",
            "request_schema",
            "result_schema",
            "idempotent",
            "source-config:<source_id>",
            "zero-I/O deferred registry holder",
            "require_schema_at_head()",
            "`x-consumer`",
            "Douyin 仅保留 `mode=direct|extension`",
            "Reddit 仅保留 `backend=rdt|extension`",
        ),
        "docs/modules/vnext-ai.md": ("OPENBILICLAW_LITELLM_ADMIN_URL", "admin_url"),
        "docs/modules/vnext-persistence.md": (
            "0002_auth_state",
            "auth_state",
            "password fingerprint",
            "`disabled` sentinel",
            "aware UTC `created_at`",
        ),
        "docs/modules/vnext-use-cases-jobs.md": (
            "读取 persisted",
            "network proxy",
            "OpenBiliClaw-owned console",
            "rotating-file sinks",
            "SSL_CERT_FILE",
            "CURL_CA_BUNDLE",
            "source-config:*",
        ),
    }
    for name, markers in expected.items():
        source = _read(name)
        for marker in markers:
            assert marker in source, f"missing Task 21b marker in {name}: {marker}"


def test_deployment_docs_name_compose_browser_auth_forwarding_without_plaintext_keys() -> None:
    for name in (
        "docs/docker-deployment.md",
        "docs/agent-install.md",
        "docs/agent-deployment.md",
    ):
        source = _read(name)
        for marker in (
            "OPENBILICLAW_WEB_PASSWORD_HASH",
            "OPENBILICLAW_SESSION_SECRET",
            "OPENBILICLAW_EXTENSION_ACCESS_KEYS",
            "OPENBILICLAW_LITELLM_ADMIN_URL",
            "worker",
        ):
            assert marker in source, f"missing Compose auth marker in {name}: {marker}"
        assert "OPENBILICLAW_EXTENSION_DEVICE_KEY" not in source
        assert re.search(r"obc_ext_[A-Za-z0-9._-]{16,}", source) is None
    assert "create the session secret" in _read("docs/agent-install.md")
    assert "生成独立 random" in _read("docs/agent-deployment.md")
    assert "Compose render" in _read("docs/docker-deployment.md")


def test_active_docs_use_nested_settings_names_and_only_consumed_source_fields() -> None:
    active = {
        "docs/modules/vnext-sources.md": _read("docs/modules/vnext-sources.md"),
        "docs/modules/vnext-use-cases-jobs.md": _read("docs/modules/vnext-use-cases-jobs.md"),
        "docs/modules/config.md": _read("docs/modules/config.md"),
        "docs/architecture.md": _read("docs/architecture.md").split(
            "## 已停止作为入口的 v0.3 实现", 1
        )[0],
        "docs/spec.md": _section("docs/spec.md", "### 3.1 vNext", "### 3.2"),
    }
    stale_patterns = (
        r"UserSettings\.source_enabled\b",
        r"UserSettings\.source_weights\b",
        r"UserSettings\.source_sync_interval_minutes\b",
        r"UserSettings\.feed_[a-z_]+\b",
        r"\bsource_enabled\b",
        r"\bsource_weights\b",
    )
    for name, source in active.items():
        for pattern in stale_patterns:
            assert re.search(pattern, source) is None, f"stale flat setting in {name}: {pattern}"

    source_doc = active["docs/modules/vnext-sources.md"]
    for removed in ("daily_search_budget", "daily_hot_budget", "request_interval_seconds"):
        assert removed not in source_doc
    assert "`UserSettings.sources.enabled`" in source_doc
    assert (
        "`UserSettings.schedules.source_sync_interval_minutes`"
        in active["docs/modules/vnext-use-cases-jobs.md"]
    )


def test_current_changelog_summary_does_not_repeat_superseded_authority() -> None:
    current = _read("docs/changelog.md").split("### Historical delivery sequence", 1)[0]
    for claim in (
        "公开 HTTP API 与前端切换仍由后续任务完成",
        "尚未切换现有运行时或公开 API",
        "当前 legacy storage/runtime 继续作为唯一运行时权威",
        "typed AI 尚未接入生产 composition/use case",
        "尚未新增 HTTP route",
    ):
        assert claim not in current
