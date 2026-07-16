"""Static regressions for PCWeb model service probe controls."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_web_settings_exposes_and_wires_exact_model_probe_controls() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/model-settings.js").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    assert 'id="modelProbeButton"' in html
    assert 'id="modelProbeStatus"' in html
    assert 'aria-live="polite"' in html

    assert '"/api/model-config/probe"' in js
    assert "revision:" in js
    assert "connection:" in js
    assert "provider:" in js
    assert "settings:" in js
    assert "observed_dimension" in js
    assert "probed_at" in js

    assert ".settings-probe-row" in css
    assert ".settings-probe-status" in css


def test_desktop_web_model_probe_has_one_exact_selected_row_owner() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/model-settings.js").read_text(
        encoding="utf-8"
    )
    probe = js.split("async function probeSelected()", 1)[1].split("function retainSelection", 1)[0]

    assert "const kind = state.activeRoute;" in probe
    assert "const record = selectedRecord(state, kind);" in probe
    assert "createProbeSignature(state, kind, record.id)" in probe
    assert "applyProbeResult(state, signature" in probe
    assert "probeRequestVisible(signature)" in probe
    assert "llm_fallback" not in js
    assert "probeConfigService" not in js


def test_desktop_web_model_probe_allows_the_backend_probe_deadline() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/model-settings.js").read_text(
        encoding="utf-8"
    )
    probe = js.split("async function probeSelected()", 1)[1].split(
        "function retainSelection", 1
    )[0]

    assert "const MODEL_PROBE_TIMEOUT_MS = 60_000;" in js
    assert "timeoutMs: MODEL_PROBE_TIMEOUT_MS" in probe


def test_desktop_web_settings_exposes_and_wires_network_proxy() -> None:
    """The general tab must expose the [network].proxy field with a
    connectivity probe, and the copy must state CN requests stay direct
    (invariant: overseas-only proxy, never a global proxy)."""
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'id="networkProxyMode"' in html
    assert 'id="networkProxy"' in html
    assert 'id="probeNetworkProxy"' in html
    assert 'id="probeNetworkProxyStatus"' in html
    assert "海外" in html
    assert "国内请求始终直连" in html

    assert 'setSelect("networkProxyMode", config.network?.mode || "direct")' in js
    assert 'setInput("networkProxy", config.network?.proxy || "")' in js
    assert 'network: { mode: getInput("networkProxyMode"), proxy: getInput("networkProxy") }' in js
    assert "function runNetworkProxyConfigProbe()" in js
    assert 'probeConfigService("network_proxy",' in js
    assert 'safeBind("#probeNetworkProxy"' in js


def test_desktop_web_general_config_payload_never_sends_legacy_llm() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    body = js.split("function buildConfigUpdate()", 1)[1].split("function configErrorMessage", 1)[0]

    assert "llm" not in body
