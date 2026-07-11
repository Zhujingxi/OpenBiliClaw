from pathlib import Path


def test_saved_sync_docs_name_default_and_routes() -> None:
    config_doc = Path("docs/modules/config.md").read_text()
    integration_doc = Path("docs/modules/integrations.md").read_text()

    assert "[saved_sync]" in config_doc
    assert "auto_sync_enabled = false" in config_doc
    assert "OpenBiliClaw" in integration_doc
    assert "watch_later" in integration_doc
    assert "favorite" in integration_doc
