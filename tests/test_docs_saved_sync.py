from pathlib import Path


def test_saved_sync_docs_name_default_and_routes() -> None:
    config_doc = Path("docs/modules/config.md").read_text()
    integration_doc = Path("docs/modules/integrations.md").read_text()
    saved_sync_doc = Path("docs/modules/saved-sync.md").read_text()
    architecture_doc = Path("docs/architecture.md").read_text()
    docs_index = Path("docs/index.md").read_text()

    assert "[saved_sync]" in config_doc
    assert "auto_sync_enabled = false" in config_doc
    assert "OpenBiliClaw" in integration_doc
    assert "watch_later" in integration_doc
    assert "favorite" in integration_doc
    assert "B站稍后再看" in integration_doc
    assert "B站稍后观看" not in integration_doc
    assert "三个图形化保存界面 + CLI 配置可见" in saved_sync_doc
    assert "三个图形化保存界面 + CLI 配置可见" in architecture_doc
    assert "native-save-e2e.md" in docs_index
