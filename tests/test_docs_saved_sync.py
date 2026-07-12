from pathlib import Path


def test_saved_sync_docs_name_default_and_routes() -> None:
    config_doc = Path("docs/modules/config.md").read_text()
    integration_doc = Path("docs/modules/integrations.md").read_text()
    saved_sync_doc = Path("docs/modules/saved-sync.md").read_text()
    storage_doc = Path("docs/modules/storage.md").read_text()
    architecture_doc = Path("docs/architecture.md").read_text()
    docs_index = Path("docs/index.md").read_text()
    e2e_doc = Path("docs/native-save-e2e.md").read_text()
    changelog = Path("docs/changelog.md").read_text()

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
    assert "set -Eeuo pipefail" in e2e_doc
    assert "--noproxy '*' --connect-timeout 5 --max-time 30" in e2e_doc
    assert "trap cleanup_native_save_e2e EXIT" in e2e_doc
    assert "trap 'exit 130' INT" in e2e_doc
    assert "OBC_RESTORE_DONE=1" in e2e_doc
    assert "bash --noprofile --norc" in e2e_doc
    assert "OBC_CONFIG_TOUCHED=1" in e2e_doc
    assert "自动同步配置恢复失败" in e2e_doc
    assert "OBC_HEADERS=()" in e2e_doc
    assert "if (( ${#OBC_HEADERS[@]} )); then" in e2e_doc
    assert "Bash 3.2" in e2e_doc
    assert "授权 E2E" in saved_sync_doc
    assert "授权真实账号 E2E" in changelog
    assert "仅本地保存" in saved_sync_doc
    assert "saved_memberships(item_key)" in storage_doc
    assert "canonical `saved_memberships` 保护" in changelog
    assert "trap - EXIT INT TERM" in e2e_doc
    assert "(.items | length) > 0" in e2e_doc
    assert "非浏览器 Bearer" in e2e_doc
