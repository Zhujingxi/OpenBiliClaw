"""Tests for typed database settings and encrypted-at-rest credentials."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import InvalidToken
from pydantic import ValidationError
from sqlalchemy import select, text

from openbiliclaw.features.system.domain import UserSettings
from openbiliclaw.features.system.service import SettingsService
from openbiliclaw.infrastructure.database.base import DatabaseSettings, create_engine_and_session
from openbiliclaw.infrastructure.database.models import SettingModel, SourceAccountModel
from openbiliclaw.infrastructure.database.repositories import SQLAlchemySettingsRepository
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.security.credentials import (
    CredentialCipher,
    MissingCredentialKeyError,
)

if TYPE_CHECKING:
    from pathlib import Path


def _session_factory(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "settings.db"
    url = f"sqlite:///{path}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine, factory = create_engine_and_session(DatabaseSettings(url=url))
    return engine, factory


def test_database_settings_default_to_fresh_vnext_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENBILICLAW_DATABASE_URL", raising=False)
    settings = DatabaseSettings()

    assert settings.url == "sqlite:///data/vnext/openbiliclaw.db"


def test_database_settings_read_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = f"sqlite:///{tmp_path / 'configured.db'}"
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", url)

    assert DatabaseSettings().url == url


def test_sqlite_busy_timeout_is_explicit_and_configurable(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'busy-timeout.db'}"
    engine, _ = create_engine_and_session(DatabaseSettings(url=url, busy_timeout_seconds=0.123))
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA busy_timeout")) == 123
    engine.dispose()


def test_settings_service_persists_and_validates_typed_values(tmp_path: Path) -> None:
    engine, session_factory = _session_factory(tmp_path)
    service = SettingsService(lambda: UnitOfWork(session_factory))

    defaults = service.get()
    assert defaults == UserSettings()

    updated = service.update(
        {
            "feed": {"low_watermark": 12, "high_watermark": 36},
            "schedules": {"source_sync_interval_minutes": 45},
        }
    )
    assert updated.feed.high_watermark == 36
    assert updated.onboarding_complete is False
    assert service.get() == updated

    with pytest.raises(ValidationError):
        service.update({"feed": {"low_watermark": 40, "high_watermark": 20}})
    with pytest.raises(ValidationError):
        service.update({"schedules": {"source_sync_interval_minutes": "45"}})

    with session_factory() as session:
        rows = session.scalars(select(SettingModel).order_by(SettingModel.key)).all()
    assert {row.key for row in rows} == set(UserSettings.model_fields)
    engine.dispose()


def test_settings_update_is_atomic_on_validation_failure(tmp_path: Path) -> None:
    engine, session_factory = _session_factory(tmp_path)
    service = SettingsService(lambda: UnitOfWork(session_factory))
    original = service.update({"feed": {"low_watermark": 10, "high_watermark": 30}})

    with pytest.raises(ValidationError):
        service.update({"feed": {"low_watermark": 50, "high_watermark": 20}})

    assert service.get() == original
    engine.dispose()


def _synchronize_first_two_settings_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    reads = 0
    original = SQLAlchemySettingsRepository.get_all

    def synchronized(repository: SQLAlchemySettingsRepository):  # type: ignore[no-untyped-def]
        nonlocal reads
        values = original(repository)
        with lock:
            should_wait = reads < 2
            reads += 1
        if should_wait:
            barrier.wait(timeout=3)
        return values

    monkeypatch.setattr(SQLAlchemySettingsRepository, "get_all", synchronized)


def test_cross_session_disjoint_settings_updates_are_retained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine, session_factory = _session_factory(tmp_path)
    first = SettingsService(lambda: UnitOfWork(session_factory))
    second = SettingsService(lambda: UnitOfWork(session_factory))
    _synchronize_first_two_settings_reads(monkeypatch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        feed = executor.submit(
            first.update,
            {"feed": {"low_watermark": 11, "high_watermark": 31}},
        )
        schedule = executor.submit(
            second.update,
            {"schedules": {"feed_replenishment_interval_minutes": 17}},
        )
        feed.result(timeout=5)
        schedule.result(timeout=5)

    persisted = SettingsService(lambda: UnitOfWork(session_factory)).get()
    assert persisted.feed.low_watermark == 11
    assert persisted.feed.high_watermark == 31
    assert persisted.schedules.feed_replenishment_interval_minutes == 17
    engine.dispose()


def test_cross_session_onboarding_completion_preserves_unrelated_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine, session_factory = _session_factory(tmp_path)
    api_settings = SettingsService(lambda: UnitOfWork(session_factory))
    worker_settings = SettingsService(lambda: UnitOfWork(session_factory))
    _synchronize_first_two_settings_reads(monkeypatch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        public_update = executor.submit(
            api_settings.update,
            {"logging": {"console_level": "WARNING"}},
        )
        completion = executor.submit(worker_settings.complete_onboarding)
        public_update.result(timeout=5)
        completion.result(timeout=5)

    persisted = SettingsService(lambda: UnitOfWork(session_factory)).get()
    assert persisted.logging.console_level == "WARNING"
    assert persisted.onboarding_complete is True
    engine.dispose()


def test_settings_partial_source_maps_merge_without_resetting_other_sources(tmp_path: Path) -> None:
    engine, session_factory = _session_factory(tmp_path)
    service = SettingsService(lambda: UnitOfWork(session_factory))

    updated = service.update(
        {
            "sources": {"enabled": {"bilibili": True}, "weights": {"youtube": 2.5}},
        }
    )

    assert len(updated.sources.enabled) == 7
    assert len(updated.sources.weights) == 7
    assert updated.sources.enabled["bilibili"] is True
    assert updated.sources.enabled["youtube"] is False
    assert updated.sources.weights["youtube"] == 2.5
    assert updated.sources.weights["bilibili"] == 1.0
    assert service.get() == updated
    engine.dispose()


@pytest.mark.parametrize(
    "patch",
    [
        {"sources": {"enabled": {"unknown": True}}},
        {"sources": {"enabled": {"bilibili": "yes"}}},
        {"sources": {"weights": {"bilibili": float("nan")}}},
        {"sources": {"weights": {"bilibili": float("inf")}}},
        {"sources": {"weights": {"bilibili": -0.1}}},
    ],
)
def test_settings_partial_source_maps_reject_invalid_values_atomically(
    tmp_path: Path, patch: dict[str, object]
) -> None:
    engine, session_factory = _session_factory(tmp_path)
    service = SettingsService(lambda: UnitOfWork(session_factory))
    original = service.get()

    with pytest.raises(ValidationError):
        service.update(patch)

    assert service.get() == original
    engine.dispose()


def test_credential_cipher_requires_installer_generated_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENBILICLAW_SECRET_KEY", raising=False)

    with pytest.raises(MissingCredentialKeyError):
        CredentialCipher.from_environment()


def test_source_credentials_are_encrypted_at_rest_and_bound_to_secret(tmp_path: Path) -> None:
    engine, session_factory = _session_factory(tmp_path)
    cipher = CredentialCipher("test-only-generated-secret-A")
    other_cipher = CredentialCipher("test-only-generated-secret-B")
    plaintext = '{"cookie":"not-a-real-provider-credential"}'
    ciphertext = cipher.encrypt(plaintext)

    assert plaintext not in ciphertext
    assert cipher.decrypt(ciphertext) == plaintext
    with pytest.raises(InvalidToken):
        other_cipher.decrypt(ciphertext)

    with UnitOfWork(session_factory) as uow:
        uow.source_accounts.upsert_credentials(
            source_id="bilibili", account_key="primary", encrypted_credentials=ciphertext
        )
        uow.commit()

    raw_bytes = (tmp_path / "settings.db").read_bytes()
    assert plaintext.encode() not in raw_bytes
    with session_factory() as session:
        stored = session.scalar(select(SourceAccountModel))
        assert stored is not None
        assert stored.encrypted_credentials == ciphertext
        assert cipher.decrypt(stored.encrypted_credentials) == plaintext
    engine.dispose()


def test_source_account_repository_rejects_forged_ciphertext_prefix(tmp_path: Path) -> None:
    engine, session_factory = _session_factory(tmp_path)

    with pytest.raises(TypeError), UnitOfWork(session_factory) as uow:
        uow.source_accounts.upsert_credentials(
            source_id="bilibili",
            account_key="forged",
            encrypted_credentials="gAAAA-PLAINTEXT-cookie=fake-secret",
        )

    with session_factory() as session:
        assert session.scalar(select(SourceAccountModel)) is None
    engine.dispose()
