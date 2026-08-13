from __future__ import annotations

import math
import subprocess
from pathlib import Path

import pytest
from pydantic_ai import Agent

from openbiliclaw.ai.providers.catalog import ModelCatalog, resolve_model
from openbiliclaw.ai.providers.embeddings.protocol import EmbeddingModelInfo
from openbiliclaw.ai.providers.embeddings.providers import build_embedding_transport
from openbiliclaw.ai.providers.embeddings.service import EmbeddingService
from openbiliclaw.ai.providers.models import ModelFactory, ModelInstanceConfig
from openbiliclaw.ai.runtime.budgets import RunPolicy
from openbiliclaw.ai.runtime.capabilities import AgentId, ModelRequirements
from openbiliclaw.ai.runtime.execution import AgentRunRequest, AIRuntime
from openbiliclaw.ai.runtime.routes import ConfiguredModel, ModelRoute, RouteTable
from openbiliclaw.composition.build import validated_settings
from openbiliclaw.core.resources import ResourceBudget
from openbiliclaw.infrastructure.credentials.keyring import ProtectedFileBackend
from openbiliclaw.infrastructure.credentials.vault import CredentialVault

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_l0]
ROOT = Path(__file__).parents[2]
DATA_DIR = ROOT / "data-e2e"
CONFIG = DATA_DIR / "config.e2e.toml"


def _vault() -> CredentialVault:
    return CredentialVault(ProtectedFileBackend(DATA_DIR / "credentials.json"))


def _model_config() -> ModelInstanceConfig:
    settings = validated_settings(CONFIG)
    assert settings.model.secret_ref is not None
    resolved = resolve_model(
        ModelCatalog(DATA_DIR / "models.dev.json").load(),
        provider_id=settings.model.provider,
        model_name=settings.model.model_name,
        endpoint=settings.model.endpoint,
        protocol=settings.model.protocol,
        capabilities=None,
    )
    return ModelInstanceConfig(
        provider=resolved.provider,
        protocol=resolved.protocol,
        model_name=settings.model.model_name,
        endpoint=resolved.endpoint,
        secret_ref=settings.model.secret_ref.removeprefix("vault:"),
        capabilities=resolved.capabilities,
        owner="e2e-l0",
    )


def _embedding_config() -> ModelInstanceConfig:
    settings = validated_settings(CONFIG)
    assert settings.embedding.secret_ref is not None
    return ModelInstanceConfig(
        provider=settings.embedding.provider,
        protocol=settings.embedding.protocol or "openai",
        model_name=settings.embedding.model_name,
        endpoint=settings.embedding.endpoint,
        secret_ref=settings.embedding.secret_ref.removeprefix("vault:"),
        owner="e2e-l0",
    )


def test_real_profile_loads_and_vault_reference_resolves() -> None:
    settings = validated_settings(CONFIG)
    assert settings.model.model_name == "kimi-for-coding"
    assert settings.embedding.output_dimensions == 512
    assert settings.content.enabled == ("bilibili",)
    assert settings.host.api_port == 8430
    assert settings.model.secret_ref is not None
    secret_id = settings.model.secret_ref.removeprefix("vault:")
    assert _vault().resolve(secret_id, lambda secret: len(secret) > 0)


async def test_real_kimi_chat_round_trip_through_ai_runtime() -> None:
    built = ModelFactory(_vault()).build(_model_config())
    agent_id = AgentId("e2e.l0.chat")
    requirements = ModelRequirements()
    configured = ConfiguredModel(
        built.instance_id, built.provider, built.model, built.declared_capabilities
    )
    runtime = AIRuntime(
        RouteTable((ModelRoute(agent_id, requirements, (configured,)),)),
        ResourceBudget("e2e-model", 1),
    )
    result = await runtime.run(
        AgentRunRequest(
            agent_id=agent_id,
            agent=Agent(output_type=str),
            deps=None,
            user_input="Reply with one short word confirming connectivity.",
            history=(),
            context=(),
            requirements=requirements,
            policy=RunPolicy(timeout_seconds=120, retries=0),
            workflow="e2e-l0",
        )
    )
    assert result.output.strip()
    assert result.provider == "openai"


async def test_real_embedding_single_batch_and_semantic_difference() -> None:
    settings = validated_settings(CONFIG)
    transport = build_embedding_transport(
        _embedding_config(), _vault(), output_dimensions=settings.embedding.output_dimensions
    )
    service = EmbeddingService(
        transport,
        EmbeddingModelInfo(
            provider="openai",
            model=settings.embedding.model_name,
            dimensions=512,
            normalized=True,
            version="1",
        ),
        ResourceBudget("e2e-embedding", 1),
    )
    single = await service.embed_query("哔哩哔哩视频推荐")
    batch = await service.embed_documents(("我喜欢编程视频", "今天的天气很好"))
    assert len(single) == 512
    assert len(batch.vectors) == 2
    assert all(len(vector) == 512 for vector in batch.vectors)
    left, right = batch.vectors
    similarity = sum(a * b for a, b in zip(left, right, strict=True)) / (
        math.sqrt(sum(value * value for value in left))
        * math.sqrt(sum(value * value for value in right))
    )
    assert similarity < 0.999


def test_check_starts_and_stops_real_profile() -> None:
    result = subprocess.run(
        [
            str(ROOT / ".venv" / "bin" / "openbiliclaw"),
            "check",
            "--config",
            str(CONFIG),
            "--data-dir",
            str(DATA_DIR),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert (DATA_DIR / "openbiliclaw.db").is_file()
