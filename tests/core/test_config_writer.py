from pathlib import Path

from openbiliclaw.core.config import CapabilitySettings, ModelSettings
from openbiliclaw.core.config_writer import replace_model_section, write_model_settings


def test_replaces_only_model_owned_tables_and_preserves_other_sections(tmp_path: Path) -> None:
    original = """# retained header
[model]
# old comment
provider = "old"
model_name = "old"

[model.options]
disable_thinking = true

[embedding]
# retained embedding comment
model_name = "embed"
"""
    settings = ModelSettings(provider="deepseek", model_name="deepseek-chat")
    updated = replace_model_section(original, settings)
    assert '# retained embedding comment\nmodel_name = "embed"' in updated
    assert "# old comment" not in updated
    assert 'provider = "deepseek"' in updated

    path = tmp_path / "config.toml"
    path.write_text(original, encoding="utf-8")
    write_model_settings(path, settings)
    assert path.read_text(encoding="utf-8") == updated


def test_renders_complete_custom_capabilities() -> None:
    updated = replace_model_section(
        '[embedding]\nmodel_name = "embed"\n',
        ModelSettings(
            provider="private",
            model_name="chat",
            protocol="openai",
            endpoint="https://private.example/v1",
            capabilities=CapabilitySettings(
                tools=True,
                structured_output=False,
                vision=False,
                context_tokens=4096,
                streaming=True,
                reasoning=False,
            ),
        ),
    )
    assert "[model.capabilities]" in updated
    assert "context_tokens = 4096" in updated
    assert updated.startswith('[embedding]\nmodel_name = "embed"\n')
