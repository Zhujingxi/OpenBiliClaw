"""Transactional model-configuration service contract tests."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import tomllib
from dataclasses import asdict, replace
from pathlib import Path

import pytest

import openbiliclaw.model_config._service_storage as storage_module
import openbiliclaw.model_config.service as service_module
from openbiliclaw.config import ConfigError, render_model_config_document
from openbiliclaw.model_config import (
    ChatConnection,
    ChatRouteConfig,
    CredentialConfig,
    EmbeddingModelSettings,
    EmbeddingRouteConfig,
    MigrationResolution,
    ModelConfig,
    migrate_legacy_llm,
    render_model_config,
)
from openbiliclaw.model_config.service import (
    CredentialAction,
    ModelConfigSaveRequest,
    ModelConfigService,
    ModelConfigValidationError,
)


class FakeCoordinator:
    """Identity-preserving coordinator with no network side effects."""

    def __init__(self) -> None:
        self._current_model_candidate: object = object()
        self.allow_current_reads = True
        self.build_calls = 0
        self.swap_calls = 0
        self.restore_calls = 0
        self.fail_build = False
        self.fail_swap = False
        self.fail_restore = False
        self.build_entered: asyncio.Event | None = None
        self.build_release: asyncio.Event | None = None
        self.probe_calls: list[tuple[str, EmbeddingModelSettings | None]] = []

    @property
    def current_model_candidate(self) -> object:
        if not self.allow_current_reads:
            raise RuntimeError("late current-candidate read")
        return self._current_model_candidate

    async def build_model_candidate(self, models: ModelConfig, revision: str) -> object:
        self.build_calls += 1
        if self.build_entered is not None:
            self.build_entered.set()
        if self.build_release is not None:
            await self.build_release.wait()
        if self.fail_build:
            raise RuntimeError("candidate build failed")
        return (models, revision)

    async def swap_model_candidate(self, candidate: object) -> object:
        self.swap_calls += 1
        previous = self._current_model_candidate
        self._current_model_candidate = candidate
        if self.fail_swap:
            raise RuntimeError("candidate swap failed")
        return previous

    async def restore_model_candidate(self, candidate: object) -> None:
        self.restore_calls += 1
        if self.fail_restore:
            raise RuntimeError("candidate restore failed")
        self._current_model_candidate = candidate

    async def probe_model_draft(
        self,
        draft: ChatConnection,
        settings: EmbeddingModelSettings | None = None,
    ) -> object:
        self.probe_calls.append((draft.id, settings))
        return (draft.id, settings)


class BlockingSwapCoordinator(FakeCoordinator):
    """Expose the interval after the runtime pointer has changed."""

    def __init__(self) -> None:
        super().__init__()
        self.swap_entered = asyncio.Event()
        self.swap_release = asyncio.Event()

    async def swap_model_candidate(self, candidate: object) -> object:
        self.swap_calls += 1
        previous = self._current_model_candidate
        self._current_model_candidate = candidate
        self.swap_entered.set()
        await self.swap_release.wait()
        return previous


def _models(*, credential: CredentialConfig | None = None) -> ModelConfig:
    return ModelConfig(
        chat=ChatRouteConfig(
            connections=(
                ChatConnection(
                    id="primary",
                    name="Primary",
                    type="openai_compatible",
                    preset="custom",
                    model="chat-model",
                    base_url="https://gateway.example.test/v1",
                    credential=credential
                    or CredentialConfig(source="inline", value="test-token-original"),
                    api_mode="chat_completions",
                ),
            ),
            concurrency=4,
            timeout_seconds=300,
        ),
        embedding=EmbeddingRouteConfig(
            enabled=False,
            settings=EmbeddingModelSettings(model="embedding-model"),
        ),
    )


def _write_native(path: Path, models: ModelConfig) -> None:
    path.write_text("\n".join(render_model_config(models)) + "\n", encoding="utf-8")


def _second_connection(connection_id: str = "secondary") -> ChatConnection:
    return ChatConnection(
        id=connection_id,
        name="Secondary",
        type="openai_compatible",
        preset="custom",
        model="secondary-model",
        base_url="https://secondary.example.test/v1",
        credential=CredentialConfig(source="env", value="SECONDARY_API_KEY"),
        api_mode="chat_completions",
    )


@pytest.mark.parametrize(
    ("action", "value", "expected_source"),
    [
        ("keep", None, "inline"),
        ("set", "test-token-replacement", "inline"),
        ("env", "MODEL_API_KEY", "env"),
    ],
)
async def test_secret_actions_keep_set_and_env_are_explicit_and_publicly_redacted(
    tmp_path: Path,
    action: str,
    value: str | None,
    expected_source: str,
) -> None:
    path = tmp_path / "config.toml"
    _write_native(path, _models())
    service = ModelConfigService(path, FakeCoordinator())
    before = service.read()

    result = await service.save(
        ModelConfigSaveRequest(
            revision=before.revision,
            models=_models(credential=CredentialConfig()),
            credential_actions={"primary": CredentialAction(action=action, value=value)},
        )
    )

    assert result.ok is True
    public = service.read().public
    credential = public.models.chat.connections[0].credential
    assert credential.source == expected_source
    assert credential.configured is True
    assert not hasattr(credential, "value")
    rendered_public = json.dumps(asdict(public), sort_keys=True)
    assert "test-token-original" not in rendered_public
    assert "test-token-replacement" not in rendered_public


async def test_clear_explicitly_removes_a_credential_when_the_new_type_allows_none(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    original = _models()
    _write_native(path, original)
    service = ModelConfigService(path, FakeCoordinator())
    snapshot = service.read()
    local = replace(
        original.chat.connections[0],
        type="ollama",
        preset="",
        base_url="http://127.0.0.1:11434/v1",
        credential=CredentialConfig(),
        api_mode="",
    )
    candidate = replace(original, chat=replace(original.chat, connections=(local,)))

    result = await service.save(
        ModelConfigSaveRequest(
            revision=snapshot.revision,
            models=candidate,
            credential_actions={"primary": CredentialAction(action="clear")},
        )
    )

    assert result.ok is True
    credential = service.read().models.chat.connections[0].credential
    assert credential.source == "none"
    assert credential.configured is False


@pytest.mark.parametrize(
    "value",
    ["", "   ", "********", "sk-abcd****wxyz", "line\nbreak"],
)
async def test_set_rejects_empty_masked_whitespace_and_control_input(
    tmp_path: Path,
    value: str,
) -> None:
    path = tmp_path / "config.toml"
    _write_native(path, _models())
    service = ModelConfigService(path, FakeCoordinator())

    with pytest.raises(ModelConfigValidationError):
        await service.save(
            ModelConfigSaveRequest(
                revision=service.read().revision,
                models=_models(credential=CredentialConfig()),
                credential_actions={"primary": CredentialAction(action="set", value=value)},
            )
        )


async def test_env_action_persists_only_the_variable_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.toml"
    _write_native(path, _models())
    monkeypatch.setenv("MODEL_API_KEY", "test-token-from-process-environment")
    service = ModelConfigService(path, FakeCoordinator())

    result = await service.save(
        ModelConfigSaveRequest(
            revision=service.read().revision,
            models=_models(credential=CredentialConfig()),
            credential_actions={"primary": CredentialAction(action="env", value="MODEL_API_KEY")},
        )
    )

    assert result.ok is True
    text = path.read_text(encoding="utf-8")
    assert 'api_key_env = "MODEL_API_KEY"' in text
    assert "test-token-from-process-environment" not in text


async def test_candidate_error_and_result_repr_are_secret_safe(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class SecretBearingFailureCoordinator(FakeCoordinator):
        async def build_model_candidate(self, models: ModelConfig, revision: str) -> object:
            raise RuntimeError("test-token-build-error")

    path = tmp_path / "config.toml"
    _write_native(path, _models())
    service = ModelConfigService(path, SecretBearingFailureCoordinator())

    result = await service.save(
        ModelConfigSaveRequest(revision=service.read().revision, models=_models())
    )

    assert result.ok is False
    assert "test-token-build-error" not in repr(result)
    assert "test-token-build-error" not in caplog.text


async def test_stale_revision_returns_conflict_before_candidate_build(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    models = _models()
    _write_native(path, models)
    coordinator = FakeCoordinator()
    service = ModelConfigService(path, coordinator)

    result = await service.save(
        ModelConfigSaveRequest(
            revision="stale-revision",
            models=replace(models, chat=replace(models.chat, timeout_seconds=120)),
        )
    )

    assert result.ok is False
    assert result.conflict is True
    assert result.latest_revision == service.read().revision
    assert coordinator.build_calls == 0


async def test_stale_revision_wins_before_credential_action_validation(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    models = _models()
    _write_native(path, models)
    coordinator = FakeCoordinator()
    service = ModelConfigService(path, coordinator)

    result = await service.save(
        ModelConfigSaveRequest(
            revision="stale-revision",
            credential_actions={"primary": CredentialAction("set", "********")},
        )
    )

    assert result.conflict is True
    assert coordinator.build_calls == 0


async def test_candidate_failure_changes_neither_disk_nor_runtime(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    models = _models()
    _write_native(path, models)
    coordinator = FakeCoordinator()
    coordinator.fail_build = True
    service = ModelConfigService(path, coordinator)
    before_file = path.read_bytes()
    before_bundle = coordinator.current_model_candidate

    result = await service.save(
        ModelConfigSaveRequest(
            revision=service.read().revision,
            models=replace(models, chat=replace(models.chat, timeout_seconds=120)),
        )
    )

    assert result.ok is False
    assert path.read_bytes() == before_file
    assert coordinator.current_model_candidate is before_bundle
    assert coordinator.swap_calls == 0


async def test_swap_failure_atomically_restores_file_mode_and_exact_bundle(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    models = _models()
    _write_native(path, models)
    path.chmod(0o640)
    coordinator = FakeCoordinator()
    coordinator.fail_swap = True
    service = ModelConfigService(path, coordinator)
    before_file = path.read_bytes()
    before_bundle = coordinator.current_model_candidate

    result = await service.save(
        ModelConfigSaveRequest(
            revision=service.read().revision,
            models=replace(models, chat=replace(models.chat, timeout_seconds=120)),
        )
    )

    assert result.ok is False
    assert result.rollback_applied is True
    assert path.read_bytes() == before_file
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert coordinator.current_model_candidate is before_bundle
    assert coordinator.restore_calls == 1


async def test_swap_cancellation_restores_disk_and_runtime_then_propagates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    models = _models()
    _write_native(path, models)
    before = path.read_bytes()
    coordinator = BlockingSwapCoordinator()
    before_bundle = coordinator.current_model_candidate
    service = ModelConfigService(path, coordinator)

    save_task = asyncio.create_task(
        service.save(
            ModelConfigSaveRequest(
                revision=service.read().revision,
                models=replace(models, chat=replace(models.chat, timeout_seconds=120)),
            )
        )
    )
    await coordinator.swap_entered.wait()
    save_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await save_task

    assert path.read_bytes() == before
    assert coordinator.current_model_candidate is before_bundle
    assert coordinator.restore_calls == 1


async def test_swap_failure_restores_absent_original_as_absent(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    coordinator = FakeCoordinator()
    coordinator.fail_swap = True
    service = ModelConfigService(path, coordinator)
    editable = _models(credential=CredentialConfig())
    local = replace(
        editable.chat.connections[0],
        type="ollama",
        preset="",
        base_url="http://127.0.0.1:11434/v1",
        api_mode="",
    )
    editable = replace(editable, chat=replace(editable.chat, connections=(local,)))

    result = await service.save(
        ModelConfigSaveRequest(revision=service.read().revision, models=editable)
    )

    assert result.rollback_applied is True
    assert not path.exists()


async def test_directory_fsync_failure_after_replace_restores_prior_disk_and_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.toml"
    models = _models()
    _write_native(path, models)
    before = path.read_bytes()
    coordinator = FakeCoordinator()
    service = ModelConfigService(path, coordinator)
    real_fsync_directory = storage_module._fsync_directory
    calls = 0

    def fail_first_directory_fsync(directory: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("directory fsync failed")
        real_fsync_directory(directory)

    monkeypatch.setattr(storage_module, "_fsync_directory", fail_first_directory_fsync)

    result = await service.save(
        ModelConfigSaveRequest(
            revision=service.read().revision,
            models=replace(models, chat=replace(models.chat, timeout_seconds=120)),
        )
    )

    assert result.ok is False
    assert result.rollback_applied is True
    assert path.read_bytes() == before
    assert coordinator.swap_calls == 0


async def test_prior_runtime_identity_is_captured_before_the_disk_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.toml"
    models = _models()
    _write_native(path, models)
    coordinator = FakeCoordinator()
    coordinator.fail_swap = True
    before_bundle = coordinator.current_model_candidate
    service = ModelConfigService(path, coordinator)
    real_atomic_write = service_module._atomic_write

    def write_then_forbid_current_read(target: Path, payload: bytes, mode: int) -> None:
        real_atomic_write(target, payload, mode)
        coordinator.allow_current_reads = False

    monkeypatch.setattr(service_module, "_atomic_write", write_then_forbid_current_read)

    result = await service.save(
        ModelConfigSaveRequest(
            revision=service.read().revision,
            models=replace(models, chat=replace(models.chat, timeout_seconds=120)),
        )
    )

    assert result.rollback_applied is True
    assert coordinator._current_model_candidate is before_bundle


async def test_failed_rollback_is_reported_without_exposing_exception_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "config.toml"
    models = _models()
    _write_native(path, models)
    coordinator = FakeCoordinator()
    coordinator.fail_swap = True
    coordinator.fail_restore = True
    service = ModelConfigService(path, coordinator)
    real_replace = storage_module.os.replace
    replace_calls = 0

    def fail_restore_replace(source: str | bytes | Path, target: str | bytes | Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("test-token-must-not-leak")
        real_replace(source, target)

    monkeypatch.setattr(storage_module.os, "replace", fail_restore_replace)

    result = await service.save(
        ModelConfigSaveRequest(
            revision=service.read().revision,
            models=replace(models, chat=replace(models.chat, timeout_seconds=120)),
        )
    )

    assert result.ok is False
    assert result.rollback_applied is False
    assert "test-token-must-not-leak" not in repr(result)
    assert "test-token-must-not-leak" not in caplog.text


async def test_global_per_resolved_path_lock_serializes_services_and_stales_second(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    models = _models()
    _write_native(path, models)
    first_coordinator = FakeCoordinator()
    first_coordinator.build_entered = asyncio.Event()
    first_coordinator.build_release = asyncio.Event()
    second_coordinator = FakeCoordinator()
    first_service = ModelConfigService(path, first_coordinator)
    second_service = ModelConfigService(path.parent / "." / path.name, second_coordinator)
    revision = first_service.read().revision
    first_task = asyncio.create_task(
        first_service.save(
            ModelConfigSaveRequest(
                revision=revision,
                models=replace(models, chat=replace(models.chat, timeout_seconds=120)),
            )
        )
    )
    await first_coordinator.build_entered.wait()
    second_task = asyncio.create_task(
        second_service.save(
            ModelConfigSaveRequest(
                revision=revision,
                models=replace(models, chat=replace(models.chat, timeout_seconds=180)),
            )
        )
    )
    await asyncio.sleep(0)

    assert second_coordinator.build_calls == 0
    first_coordinator.build_release.set()
    first, second = await asyncio.gather(first_task, second_task)
    assert first.ok is True
    assert second.conflict is True
    assert second_coordinator.build_calls == 0


async def test_pending_migration_requires_closed_resolutions_before_build(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[llm]
default_provider = "deepseek"
concurrency = 4
timeout = 300

[llm.deepseek]
api_key = "test-token-legacy"
model = "deepseek-chat"
base_url = "https://api.deepseek.com"

[llm.soul]
provider = "deepseek"
model = "special-model"
""".lstrip(),
        encoding="utf-8",
    )
    coordinator = FakeCoordinator()
    service = ModelConfigService(path, coordinator)
    snapshot = service.read()
    assert snapshot.migration is not None
    assert snapshot.migration.has_pending_decisions is True

    with pytest.raises(ModelConfigValidationError):
        await service.save(ModelConfigSaveRequest(revision=snapshot.revision))

    assert coordinator.build_calls == 0


async def test_closed_migration_creates_non_overwriting_mode_preserving_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[llm]
default_provider = "deepseek"
concurrency = 4
timeout = 300

[llm.deepseek]
api_key = "test-token-legacy"
model = "deepseek-chat"
base_url = "https://api.deepseek.com"

[llm.soul]
provider = "deepseek"
model = "special-model"
""".lstrip(),
        encoding="utf-8",
    )
    path.chmod(0o640)
    original = path.read_bytes()
    reserved = tmp_path / "config.toml.pre-model-refactor.bak"
    reserved.write_bytes(b"existing-backup")
    service = ModelConfigService(path, FakeCoordinator())
    snapshot = service.read()
    assert snapshot.migration is not None
    resolutions = {
        issue.id: MigrationResolution(action="accept_global_route")
        for issue in snapshot.migration.issues
        if issue.severity == "blocking"
    }

    result = await service.save(
        ModelConfigSaveRequest(
            revision=snapshot.revision,
            migration_resolutions=resolutions,
        )
    )

    assert result.ok is True
    assert result.backup_path is not None
    assert result.backup_path != reserved
    assert result.backup_path.read_bytes() == original
    assert stat.S_IMODE(result.backup_path.stat().st_mode) == 0o640
    assert reserved.read_bytes() == b"existing-backup"
    assert result.snapshot.migration is None
    assert not hasattr(result.snapshot, "backup_path")
    rendered = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "models" in rendered
    assert "llm" not in rendered


async def test_migration_resolutions_apply_after_keep_merge_without_losing_draft_edits(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[llm]
default_provider = "deepseek"
concurrency = 4

[llm.deepseek]
api_key = "test-token-routed"
model = "deepseek-chat"

[llm.openai]
api_key = "test-token-unrouted"
model = "gpt-4o-mini"
""".lstrip(),
        encoding="utf-8",
    )
    raw_llm = tomllib.loads(path.read_text(encoding="utf-8"))["llm"]
    migration = migrate_legacy_llm(raw_llm, {})
    issue = next(item for item in migration.report.issues if item.code == "unrouted_credential")
    draft = replace(
        migration.models,
        chat=replace(migration.models.chat, concurrency=7),
    )
    service = ModelConfigService(path, FakeCoordinator(), environment={})

    result = await service.save(
        ModelConfigSaveRequest(
            revision=service.read().revision,
            models=draft,
            migration_resolutions={
                issue.id: MigrationResolution(action="add_to_chat_route", position=2)
            },
        )
    )

    assert result.ok is True
    rendered_models = tomllib.loads(path.read_text(encoding="utf-8"))["models"]
    assert rendered_models["chat"]["concurrency"] == 7
    assert len(rendered_models["chat"]["connections"]) == 2


async def test_failed_legacy_swap_restores_legacy_bytes_and_removes_new_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[llm]
default_provider = "deepseek"

[llm.deepseek]
api_key = "test-token-legacy"
model = "deepseek-chat"
base_url = "https://api.deepseek.com"
""".lstrip(),
        encoding="utf-8",
    )
    original = path.read_bytes()
    coordinator = FakeCoordinator()
    coordinator.fail_swap = True
    service = ModelConfigService(path, coordinator)

    result = await service.save(ModelConfigSaveRequest(revision=service.read().revision))

    assert result.rollback_applied is True
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("config.toml.pre-model-refactor*.bak"))


async def test_write_failure_before_replace_changes_neither_disk_nor_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.toml"
    models = _models()
    _write_native(path, models)
    before = path.read_bytes()
    coordinator = FakeCoordinator()
    before_bundle = coordinator.current_model_candidate
    service = ModelConfigService(path, coordinator)

    def reject_replace(_source: object, _target: object) -> None:
        raise OSError("test-token-write-error")

    monkeypatch.setattr(storage_module.os, "replace", reject_replace)

    result = await service.save(
        ModelConfigSaveRequest(
            revision=service.read().revision,
            models=replace(models, chat=replace(models.chat, timeout_seconds=120)),
        )
    )

    assert result.ok is False
    assert result.rollback_applied is False
    assert path.read_bytes() == before
    assert coordinator.current_model_candidate is before_bundle
    assert coordinator.swap_calls == 0
    assert "test-token-write-error" not in repr(result)


async def test_local_override_blocks_shadowed_field_without_value_disclosure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    local_path = tmp_path / "config.local.toml"
    base = _models()
    _write_native(path, base)
    local_path.write_text("[models.chat]\nconcurrency = 9\n", encoding="utf-8")
    service = ModelConfigService(path, FakeCoordinator())
    snapshot = service.read()
    assert snapshot.models.chat.concurrency == 9
    assert snapshot.overrides[0].source == str(local_path.resolve())
    effective = replace(base, chat=replace(base.chat, concurrency=9))

    with pytest.raises(ModelConfigValidationError) as raised:
        await service.save(
            ModelConfigSaveRequest(
                revision=snapshot.revision,
                models=replace(effective, chat=replace(effective.chat, concurrency=10)),
            )
        )

    error = raised.value.errors[0]
    assert error.path == "models.chat.concurrency"
    assert error.source == str(local_path.resolve())
    assert "9" not in str(raised.value)
    assert "10" not in str(raised.value)


async def test_edit_outside_local_override_persists_base_without_baking_local(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    local_path = tmp_path / "config.local.toml"
    base = _models()
    _write_native(path, base)
    local_path.write_text("[models.chat]\nconcurrency = 9\n", encoding="utf-8")
    service = ModelConfigService(path, FakeCoordinator())
    snapshot = service.read()
    effective = replace(base, chat=replace(base.chat, concurrency=9, timeout_seconds=120))

    result = await service.save(
        ModelConfigSaveRequest(revision=snapshot.revision, models=effective)
    )

    assert result.ok is True
    on_disk = tomllib.loads(path.read_text(encoding="utf-8"))["models"]["chat"]
    assert on_disk["concurrency"] == 4
    assert on_disk["timeout_seconds"] == 120
    assert service.read().models.chat.concurrency == 9


def test_stable_id_mutations_are_one_based_and_preserve_move_identity(tmp_path: Path) -> None:
    service = ModelConfigService(tmp_path / "config.toml", FakeCoordinator())
    first = _models()
    second = _second_connection()
    models = replace(first, chat=replace(first.chat, connections=(*first.chat.connections, second)))

    moved = service.move(models, "secondary", 1)

    assert [item.id for item in moved.chat.connections] == ["secondary", "primary"]
    assert moved.chat.connections[0] is second
    assert moved.chat.connections[0].credential is second.credential
    with pytest.raises(ModelConfigValidationError):
        service.move(models, "missing", 1)
    with pytest.raises(ModelConfigValidationError):
        service.move(models, "secondary", 0)
    with pytest.raises(ModelConfigValidationError):
        service.move(models, "secondary", 3)


def test_add_edit_remove_reject_duplicates_missing_ids_limits_and_final_chat(
    tmp_path: Path,
) -> None:
    service = ModelConfigService(tmp_path / "config.toml", FakeCoordinator())
    models = _models()
    with pytest.raises(ModelConfigValidationError):
        service.add(models, replace(_second_connection(), id="primary"))
    with pytest.raises(ModelConfigValidationError):
        service.edit(models, "missing", _second_connection())
    with pytest.raises(ModelConfigValidationError):
        service.edit(models, "primary", replace(models.chat.connections[0], id="changed"))
    with pytest.raises(ModelConfigValidationError):
        service.remove(models, "primary")

    ten = replace(
        models,
        chat=replace(
            models.chat,
            connections=tuple(
                replace(
                    models.chat.connections[0],
                    id=f"connection-{index}",
                    name=f"Connection {index}",
                )
                for index in range(10)
            ),
        ),
    )
    with pytest.raises(ModelConfigValidationError):
        service.add(ten, _second_connection("eleventh"))


def test_valid_add_edit_remove_return_full_candidates_at_one_based_positions(
    tmp_path: Path,
) -> None:
    service = ModelConfigService(tmp_path / "config.toml", FakeCoordinator())
    models = _models()
    secondary = _second_connection()

    added = service.add(models, secondary, position=1)
    edited_record = replace(secondary, name="Edited Secondary")
    edited = service.edit(added, "secondary", edited_record)
    removed = service.remove(edited, "primary")

    assert isinstance(added, ModelConfig)
    assert [item.id for item in added.chat.connections] == ["secondary", "primary"]
    assert edited.chat.connections[0] is edited_record
    assert [item.id for item in removed.chat.connections] == ["secondary"]
    with pytest.raises(ModelConfigValidationError):
        service.add(models, secondary, position=True)


async def test_probe_delegates_exact_draft_without_persistence(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    models = _models()
    _write_native(path, models)
    before = path.read_bytes()
    coordinator = FakeCoordinator()
    service = ModelConfigService(path, coordinator)
    draft = models.chat.connections[0]

    result = await service.probe(draft)

    assert result == ("primary", None)
    assert coordinator.probe_calls == [("primary", None)]
    assert path.read_bytes() == before


def test_document_renderer_preserves_unrelated_bytes_crlf_and_table_boundaries() -> None:
    original = (
        b"# keep-leading\r\n"
        b"[unrelated]\r\n"
        b'value = "keep" # keep-inline\r\n'
        b"\r\n"
        b"[llm]\r\n"
        b'default_provider = "deepseek"\r\n'
        b"\r\n"
        b"[other.nested]\r\n"
        b"answer = 42 # keep-nested\r\n"
        b"\r\n"
        b"[llm.deepseek]\r\n"
        b'api_key = "test-token-old"\r\n'
    )
    kept_first = b'# keep-leading\r\n[unrelated]\r\nvalue = "keep" # keep-inline\r\n\r\n'
    kept_second = b"[other.nested]\r\nanswer = 42 # keep-nested\r\n\r\n"

    rendered = render_model_config_document(original, _models())

    expected_models = ("\r\n".join(render_model_config(_models())) + "\r\n").encode()
    assert rendered == kept_first + expected_models + b"\r\n" + kept_second
    assert b"[llm" not in rendered
    assert rendered.count(b"[models]") == 1
    assert b"\n" not in rendered.replace(b"\r\n", b"")


def test_document_renderer_inserts_when_absent_and_rejects_ambiguous_dotted_authority() -> None:
    original = b"# untouched\n[unrelated]\nvalue = 1 # exact\n"
    rendered = render_model_config_document(original, _models())
    assert rendered.startswith(original)
    assert rendered.count(b"[models]") == 1

    ambiguous = b'models.schema_version = 1\nmodels.vendor = "unknown"\n'
    with pytest.raises(ConfigError):
        render_model_config_document(ambiguous, _models())


def test_document_renderer_removes_native_and_legacy_authority_without_duplicates() -> None:
    native = ("\n".join(render_model_config(_models())) + "\n").encode()
    original = (
        b"# before\n"
        + native
        + b'\n[unrelated]\nvalue = "exact" # preserve\n\n'
        + b'[llm]\ndefault_provider = "deepseek"\n'
        + b'[llm.deepseek]\napi_key = "test-token-old"\n'
    )

    rendered = render_model_config_document(original, _models())
    parsed = tomllib.loads(rendered.decode())

    assert rendered.count(b"[models]") == 1
    assert b"[llm" not in rendered
    assert parsed["unrelated"] == {"value": "exact"}
    assert b'[unrelated]\nvalue = "exact" # preserve\n\n' in rendered


def test_document_renderer_preserves_boundary_trivia_and_unusual_non_model_root() -> None:
    native = ("\n".join(render_model_config(_models())) + "\n").encode()
    boundary = (
        b"# keep this heading for the unrelated table\n"
        b"\n"
        b'["__openbiliclaw_model_header_sentinel_6d498b80__"]\n'
        b'value = "exact" # keep inline\n'
    )

    rendered = render_model_config_document(native + boundary, _models())

    assert rendered == native + boundary


async def test_atomic_write_orders_file_fsync_replace_and_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.toml"
    models = _models()
    _write_native(path, models)
    path.chmod(0o640)
    service = ModelConfigService(path, FakeCoordinator())
    events: list[tuple[str, object]] = []
    real_fsync = storage_module.os.fsync
    real_replace = storage_module.os.replace

    def record_fsync(fd: int) -> None:
        events.append(("fsync", stat.S_ISDIR(os.fstat(fd).st_mode)))
        real_fsync(fd)

    def record_replace(source: str | bytes | Path, target: str | bytes | Path) -> None:
        events.append(("replace", (Path(source).parent, Path(target).parent)))
        real_replace(source, target)

    monkeypatch.setattr(storage_module.os, "fsync", record_fsync)
    monkeypatch.setattr(storage_module.os, "replace", record_replace)

    result = await service.save(
        ModelConfigSaveRequest(
            revision=service.read().revision,
            models=replace(models, chat=replace(models.chat, timeout_seconds=120)),
        )
    )

    assert result.ok is True
    replace_index = next(index for index, event in enumerate(events) if event[0] == "replace")
    assert ("fsync", False) in events[:replace_index]
    assert ("fsync", True) in events[replace_index + 1 :]
    assert events[replace_index][1] == (path.parent, path.parent)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
