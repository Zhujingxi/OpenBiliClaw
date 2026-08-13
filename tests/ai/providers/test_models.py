from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic_ai.profiles.openai import OpenAIModelProfile

import pytest
from pydantic import ValidationError
from pydantic_ai.models.test import TestModel

from openbiliclaw.ai.providers.models import (
    ModelFactory,
    ModelInstanceConfig,
    ModelOptions,
    ProviderKind,
    anthropic,
    deepseek,
    google,
    openai,
    openrouter,
)
from openbiliclaw.ai.providers.verification import (
    CapabilityProbe,
    CapabilityStatus,
    CapabilityVerificationStore,
    UnsupportedCapabilityError,
    verification_key,
)
from openbiliclaw.ai.runtime.capabilities import ModelCapabilities

T = TypeVar("T")


@dataclass
class FakeVault:
    value: bytes = b"secret-canary"
    resolved: int = 0

    def resolve(self, secret_id: str, callback: Callable[[memoryview], T]) -> T:
        assert secret_id == "cred_" + "a" * 32
        self.resolved += 1
        return callback(memoryview(self.value))


def config(provider: ProviderKind = ProviderKind.OPENAI, **updates: object) -> ModelInstanceConfig:
    values: dict[str, object] = {
        "provider": provider,
        "model_name": "test-model",
        "secret_ref": "cred_" + "a" * 32,
    }
    values.update(updates)
    return ModelInstanceConfig.model_validate(values)


def test_options_are_reviewed_and_unknown_fields_fail() -> None:
    assert ModelOptions(temperature=0.2, max_tokens=100, top_p=0.9).to_settings() == {
        "temperature": 0.2,
        "max_tokens": 100,
        "top_p": 0.9,
    }
    assert "extra_body" not in ModelOptions().to_settings()
    with pytest.raises(ValidationError):
        config(options={"mystery": True})
    with pytest.raises(ValidationError, match="provider"):
        ModelInstanceConfig.model_validate(
            {"provider": "unknown", "model_name": "m", "secret_ref": "cred_" + "a" * 32}
        )


def test_disable_thinking_only_changes_openai_model_settings() -> None:
    assert openai.build(config(options={"temperature": 0.2}), "key").settings == {
        "temperature": 0.2
    }
    model_config = config(options={"temperature": 0.2, "disable_thinking": True})

    assert openai.build(model_config, "key").settings == {
        "temperature": 0.2,
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    assert anthropic.build(model_config, "key").settings == {"temperature": 0.2}
    assert deepseek.build(model_config, "key").settings == {"temperature": 0.2}
    assert google.build(model_config, "key").settings == {"temperature": 0.2}
    assert openrouter.build(model_config, "key").settings == {"temperature": 0.2}


def test_deepseek_uses_native_profile_and_supports_endpoint_override() -> None:
    default = deepseek.build(config(ProviderKind.DEEPSEEK, model_name="deepseek-reasoner"), "key")
    overridden = deepseek.build(
        config(ProviderKind.DEEPSEEK, endpoint="https://gateway.example/v1"), "key"
    )

    profile = cast("OpenAIModelProfile", default.profile)
    assert default.client.base_url.host == "api.deepseek.com"
    assert profile.openai_chat_thinking_field == "reasoning_content"
    assert profile.openai_supports_tool_choice_required is False
    assert overridden.client.base_url == "https://gateway.example/v1/"


@pytest.mark.parametrize("provider", list(ProviderKind))
def test_native_factory_constructs_each_provider_without_network(provider: ProviderKind) -> None:
    built = ModelFactory(FakeVault()).build(config(provider))
    assert built.provider == provider.value


def test_factory_resolves_secret_only_inside_selected_builder() -> None:
    vault = FakeVault()
    seen: list[str] = []

    def builder(_config: ModelInstanceConfig, key: str) -> TestModel:
        seen.append(key)
        return TestModel()

    model_config = config(owner="recommendation", capabilities=ModelCapabilities(tools=True))
    factory = ModelFactory(vault, builders={ProviderKind.OPENAI: builder})
    first = factory.build(model_config)
    second = factory.build(model_config)
    assert vault.resolved == 2
    assert seen == ["secret-canary", "secret-canary"]
    assert first.instance_id == second.instance_id
    assert first.owner == "recommendation"
    assert all(
        result.status is CapabilityStatus.UNVERIFIED for result in first.verification.results
    )
    assert "secret-canary" not in repr(first)


def test_openrouter_endpoint_override_fails_clearly() -> None:
    with pytest.raises(UnsupportedCapabilityError, match="endpoint override"):
        ModelFactory(FakeVault()).build(
            config(ProviderKind.OPENROUTER, endpoint="https://override.example/v1")
        )


def test_fingerprint_and_capability_verification_preserve_identity() -> None:
    base = config()
    assert base.fingerprint() != config(endpoint="https://other.example/v1").fingerprint()
    assert base.fingerprint() != config(options={"disable_thinking": True}).fingerprint()
    assert base.fingerprint() != config(model_name="other").fingerprint()
    assert base.fingerprint() != config(provider_version="2").fingerprint()
    assert "cred" not in verification_key(base)
    assert CapabilityProbe.local_support(base, "tools").status is CapabilityStatus.UNSUPPORTED

    record = (
        ModelFactory(FakeVault(), builders={ProviderKind.OPENAI: lambda *_: TestModel()})
        .build(base)
        .verification
    )
    store = CapabilityVerificationStore()
    store.put(record)
    assert store.get(base) == record


async def test_capability_probe_records_safe_results() -> None:
    model_config = config()

    async def supported() -> bool:
        return True

    async def broken() -> bool:
        raise RuntimeError("unsafe provider body")

    assert (
        await CapabilityProbe.streaming(model_config, supported)
    ).status is CapabilityStatus.VERIFIED
    assert (await CapabilityProbe.vision(model_config, broken)).status is CapabilityStatus.FAILED
    with pytest.raises(ValueError, match="unknown"):
        await CapabilityProbe.run(model_config, "reasoning", supported)


@pytest.mark.integration
@pytest.mark.skip(reason="opt-in real provider capability probes require explicit credentials")
async def test_real_provider_capability_contract_probe() -> None:
    """Integration entrypoint intentionally skipped in normal network-free tests."""
