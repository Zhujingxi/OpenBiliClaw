from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

import pytest
from pydantic import ValidationError
from pydantic_ai.models.test import TestModel

from openbiliclaw.ai.providers.models.config import ModelInstanceConfig, ModelOptions, ProviderKind
from openbiliclaw.ai.providers.models.factory import ModelFactory
from openbiliclaw.ai.providers.verification import (
    CapabilityProbe,
    CapabilityStatus,
    CapabilityVerificationStore,
    UnsupportedCapabilityError,
    verification_key,
)
from openbiliclaw.ai.runtime.capabilities import ModelCapabilities

T = TypeVar("T")


def test_model_options_map_every_reviewed_setting() -> None:
    assert ModelOptions().to_settings() == {}
    assert ModelOptions(temperature=0.2, max_tokens=100, top_p=0.9).to_settings() == {
        "temperature": 0.2,
        "max_tokens": 100,
        "top_p": 0.9,
    }


@dataclass
class FakeVault:
    value: bytes = b"secret-canary"
    resolved: int = 0

    def resolve(self, secret_id: str, callback: Callable[[memoryview], T]) -> T:
        assert secret_id == "cred_" + "a" * 32
        self.resolved += 1
        return callback(memoryview(self.value))


@pytest.mark.parametrize("provider", list(ProviderKind))
def test_every_provider_config_rejects_unknown_options(provider: ProviderKind) -> None:
    kwargs = {"provider": provider, "model_name": "m", "options": {"temperature": 0.2}}
    if provider is not ProviderKind.OLLAMA:
        kwargs["secret_ref"] = "cred_" + "a" * 32
    config = ModelInstanceConfig.model_validate(kwargs)
    assert config.options.temperature == 0.2
    with pytest.raises(ValidationError):
        ModelInstanceConfig.model_validate({**kwargs, "options": {"mystery": True}})


def test_factory_resolves_secret_only_during_builder_and_returns_stable_metadata() -> None:
    vault = FakeVault()
    seen: list[str] = []
    config = ModelInstanceConfig(
        provider=ProviderKind.OPENAI,
        model_name="gpt-test",
        secret_ref="cred_" + "a" * 32,
        owner="recommendation",
        capabilities=ModelCapabilities(tools=True),
    )

    def builder(config: ModelInstanceConfig, key: str) -> TestModel:
        seen.append(key)
        return TestModel()

    factory = ModelFactory(vault, builders={ProviderKind.OPENAI: builder})
    first = factory.build(config)
    second = factory.build(config)
    assert vault.resolved == 2
    assert seen == ["secret-canary", "secret-canary"]
    assert first.instance_id == second.instance_id
    assert first.owner == "recommendation"
    assert first.declared_capabilities.tools
    assert all(
        result.status is CapabilityStatus.UNVERIFIED for result in first.verification.results
    )
    assert "secret-canary" not in repr(first)


def test_remote_provider_requires_secret_and_ollama_does_not() -> None:
    with pytest.raises(ValidationError):
        ModelInstanceConfig(provider=ProviderKind.GOOGLE, model_name="gemini")
    local = ModelInstanceConfig(provider=ProviderKind.OLLAMA, model_name="qwen")
    assert local.secret_ref is None


def test_dashscope_is_explicitly_unsupported_for_chat() -> None:
    config = ModelInstanceConfig(
        provider=ProviderKind.DASHSCOPE,
        model_name="qwen",
        secret_ref="cred_" + "a" * 32,
    )
    with pytest.raises(UnsupportedCapabilityError, match="dashscope"):
        ModelFactory(FakeVault()).build(config)


def test_verification_key_excludes_secret_and_local_tools_are_unsupported() -> None:
    config = ModelInstanceConfig(provider=ProviderKind.OLLAMA, model_name="qwen")
    key = verification_key(config)
    assert len(key) == 64
    assert "cred" not in key
    result = CapabilityProbe.local_support(config, "tools")
    assert result.status is CapabilityStatus.UNSUPPORTED


async def test_capability_probe_records_success_failure_and_rejects_unknown() -> None:
    config = ModelInstanceConfig(provider=ProviderKind.OLLAMA, model_name="qwen")

    async def supported() -> bool:
        return True

    async def broken() -> bool:
        raise RuntimeError("unsafe provider body")

    streaming = await CapabilityProbe.streaming(config, supported)
    assert streaming.status is CapabilityStatus.VERIFIED
    assert (await CapabilityProbe.vision(config, broken)).status is CapabilityStatus.FAILED
    assert (await CapabilityProbe.native_tools(config, supported)).capability == "tools"
    structured = await CapabilityProbe.structured_output(config, supported)
    assert structured.capability == "structured_output"

    record = ModelFactory(FakeVault()).build(config).verification
    store = CapabilityVerificationStore()
    store.put(record)
    assert store.get(config) == record

    with pytest.raises(ValueError, match="unknown"):
        await CapabilityProbe.run(config, "reasoning", supported)


@pytest.mark.integration
@pytest.mark.skip(reason="opt-in real capability probes require explicit provider credentials")
async def test_real_provider_capability_contract_probe() -> None:
    """Integration entrypoint intentionally skipped in normal network-free tests."""


@pytest.mark.parametrize(
    "provider",
    [
        ProviderKind.OPENAI,
        ProviderKind.ANTHROPIC,
        ProviderKind.GOOGLE,
        ProviderKind.OLLAMA,
        ProviderKind.OPENROUTER,
    ],
)
def test_native_factory_constructs_every_supported_provider_without_network(
    provider: ProviderKind,
) -> None:
    config = ModelInstanceConfig(
        provider=provider,
        model_name="test-model",
        endpoint=("http://127.0.0.1:11434/v1" if provider is ProviderKind.OLLAMA else None),
        secret_ref=None if provider is ProviderKind.OLLAMA else "cred_" + "a" * 32,
    )
    built = ModelFactory(FakeVault()).build(config)
    assert built.provider == provider.value


def test_openrouter_rejects_endpoint_override() -> None:
    from openbiliclaw.ai.providers.models.openrouter import build

    config = ModelInstanceConfig(
        provider=ProviderKind.OPENROUTER,
        model_name="some-model",
        endpoint="https://proxy.example.com",
        secret_ref="cred_" + "0" * 32,
    )
    with pytest.raises(ValueError, match="endpoint override"):
        build(config, "key")
