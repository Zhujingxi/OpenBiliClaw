from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_and_extension_general_tabs_expose_tailnet_controls() -> None:
    desktop = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    extension = (ROOT / "extension/popup/popup.html").read_text(encoding="utf-8")

    for document, prefix in ((desktop, "tailnet"), (extension, "cfgTailnet")):
        assert f'id="{prefix}Enabled"' in document
        assert f'id="{prefix}Hostname"' in document
        assert f'id="{prefix}BootstrapCredential" type="password"' in document
        assert f'id="{prefix}AdvertiseTags"' in document
        assert "tskey-auth-… / tskey-client-…" in document
        assert "0600" in document


def test_desktop_and_extension_send_write_only_tailnet_bootstrap_fields() -> None:
    desktop = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    extension = (ROOT / "extension/popup/popup.js").read_text(encoding="utf-8")

    for script in (desktop, extension):
        assert "bootstrap_credential:" in script
        assert "advertise_tags:" in script
        assert "clear_bootstrap_credential:" in script
        assert "bootstrap_credential_staged" in script
        assert '.startsWith("tskey-client-")' in script
        assert "完整重启" in script
