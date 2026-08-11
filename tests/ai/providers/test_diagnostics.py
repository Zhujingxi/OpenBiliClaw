from openbiliclaw.ai.providers.diagnostics import (
    DiagnosticStatus,
    ProviderDiagnostic,
    construction_diagnostic,
)


def test_diagnostic_never_echoes_exception_or_secret() -> None:
    error = RuntimeError("Authorization: Bearer secret-canary upstream body")
    result = construction_diagnostic("openai", "instance", error)
    assert result.status is DiagnosticStatus.UNAVAILABLE
    assert "secret-canary" not in repr(result)
    assert result.detail == "provider construction failed"
    with_secret = ProviderDiagnostic(
        provider="openai", instance_id="i", status=DiagnosticStatus.READY
    )
    assert with_secret.detail is None
