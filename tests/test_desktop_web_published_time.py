import subprocess
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    marker = f"function {name}("
    start = APP_JS.find(marker)
    assert start >= 0, f"{name} function not found"
    opening_brace = APP_JS.find("{", start)
    assert opening_brace >= 0, f"{name} opening brace not found"
    depth = 0
    for index in range(opening_brace, len(APP_JS)):
        if APP_JS[index] == "{":
            depth += 1
        elif APP_JS[index] == "}":
            depth -= 1
            if depth == 0:
                return APP_JS[start : index + 1]
    raise AssertionError(f"{name} closing brace not found")


def test_normalize_recommendation_carries_publication_fields() -> None:
    normalize = _function_source("normalizeRecommendation")

    assert 'published_at: String(item?.published_at ?? "").trim()' in normalize
    assert (
        'published_label: String(item?.published_label ?? "").replace(/\\s+/g, " ")'
        ".trim().slice(0, 64)"
    ) in normalize


def test_normalize_delight_carries_publication_fields() -> None:
    normalize = _function_source("normalizeDelight")

    assert 'published_at: String(item?.published_at ?? "").trim()' in normalize
    assert (
        'published_label: String(item?.published_label ?? "").replace(/\\s+/g, " ")'
        ".trim().slice(0, 64)"
    ) in normalize


def test_format_published_time_executes_all_exact_and_fallback_boundaries() -> None:
    formatter = _function_source("formatPublishedTime")
    node_script = (
        formatter
        + r"""
const now = new Date(2026, 6, 11, 12, 0, 0, 0).getTime();
const iso = (offset) => new Date(now + offset).toISOString();
const cases = [
  ["exact-precedes-label", { published_at: iso(-7_200_000), published_label: "备用" }, "2 小时前"],
  ["label-fallback", { published_label: "  3   天前\n" }, "3 天前"],
  ["empty", {}, ""],
  ["invalid", { published_at: "not-a-date", published_label: "来源时间" }, "来源时间"],
  ["just-now", { published_at: iso(-59_999) }, "刚刚"],
  ["hour", { published_at: iso(-60_000) }, "1 小时前"],
  ["before-24-hours", { published_at: iso(-86_399_999) }, "23 小时前"],
  ["day", { published_at: iso(-86_400_000) }, "1 天前"],
  ["before-7-days", { published_at: iso(-604_799_999) }, "6 天前"],
  ["exactly-7-days", { published_at: iso(-604_800_000) }, "7月4日"],
  ["same-year", { published_at: new Date(2026, 0, 2, 12).toISOString() }, "1月2日"],
  ["old-year", { published_at: new Date(2025, 10, 9, 12).toISOString() }, "2025-11-09"],
  ["small-future", { published_at: iso(300_000) }, "刚刚"],
  ["obvious-future", { published_at: iso(300_001) }, "7月11日"],
];
const failures = cases
  .map(([name, item, expected]) => ({ name, expected, actual: formatPublishedTime(item, now) }))
  .filter(({ actual, expected }) => actual !== expected);
if (failures.length) {
  process.stderr.write(JSON.stringify(failures, null, 2));
  process.exit(1);
}
"""
    )

    result = subprocess.run(
        ["node", "-e", node_script],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr


def test_grid_renders_escaped_publication_time_and_exact_title() -> None:
    meta = _function_source("recommendationMetaHtml")

    assert "const published = formatPublishedTime(item);" in meta
    assert "new Date(item.published_at).toLocaleString()" in meta
    assert 'class="published-time"' in meta
    assert "escapeHtml(published)" in meta


def test_set_active_delight_renders_publication_text_title_and_visibility() -> None:
    set_active = _function_source("setActiveDelight")

    assert "const published = formatPublishedTime(state.delight);" in set_active
    assert "delightPublishedEl.textContent = published;" in set_active
    assert "new Date(state.delight.published_at).toLocaleString()" in set_active
    assert "delightPublishedEl.hidden = !published;" in set_active
    assert 'id="delightPublished"' in INDEX_HTML
    assert ".published-time" in APP_CSS


def test_empty_delight_queue_clears_stale_publication_state() -> None:
    set_active = _function_source("setActiveDelight")
    empty_queue = set_active.split("if (!state.delights.length) {", maxsplit=1)[1]
    empty_queue = empty_queue.split("state.delightIndex", maxsplit=1)[0]

    assert 'const delightPublishedEl = $("#delightPublished");' in empty_queue
    assert 'delightPublishedEl.textContent = "";' in empty_queue
    assert 'delightPublishedEl.removeAttribute("title");' in empty_queue
    assert "delightPublishedEl.hidden = true;" in empty_queue
