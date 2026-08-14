# Plan: Anonymous Provider Ports — Bangumi · V2EX · Linux.do · Weibo

Status: 🟡 IN PROGRESS · Created: 2026-08-14 · Branch: `refactor/architecture`
Progress manager: this file is the source of truth for task state. Update checkboxes
in the same commit that completes the task.

## Goal

Enable four zero-credential content providers end-to-end (search → detail → UI),
following the YouTube reference pattern. No user session tokens anywhere — all four
connect via `builtin.anonymous`.

## Ground truth (verified 2026-08-14)

| Provider | Current state | Work needed |
|----------|---------------|-------------|
| `bangumi` | **Real** `HttpxBangumiTransport` (api.bgm.tv, 183 LoC, typed errors) + projections + manual PAT form; wired in `composition/providers.py` | Verify live; add anonymous connect; e2e layer; enable |
| `v2ex` | **Real** `HttpxV2EXTransport` (www.v2ex.com, 140 LoC, typed errors); wired | Same as bangumi |
| `linuxdo` | **Stub** — client wraps `LinuxDoTransport` protocol, composition passes `unavailable`; no `projections.py`; manifest: SEARCH/FETCH/PROJECTION | New Discourse JSON transport + projections; anonymous connect; e2e layer |
| `weibo` | **Stub** — `WeiboClient(unavailable)`; no `projections.py`/`auth.py`; manifest: SEARCH/FETCH/PROJECTION | Port old anonymous visitor client (m.weibo.cn + `genvisitor2` SUB flow) from `main:src/openbiliclaw/sources/weibo_client.py`; projections; e2e layer |

Existing coverage: `tests/content/providers/test_public_api_providers.py`,
`test_public_provider_transports.py` (+ edges/failures) already cover bangumi/v2ex
hermetically. E2e wiring: layer = pyproject marker + `scripts/e2e.py::LAYERS` +
`tests/e2e/test_l1_*.py`; reference = `test_l1_youtube.py` + `e2e_l1youtube` marker.

## Rules (apply to every task)

1. **TDD**: failing test first (red), minimal implementation (green), refactor.
2. **One feature → independent fresh-context reviewer → resolve FIX before PASS →
   one atomic commit.** Reviewer has no shell; parent runs gates and pastes results.
3. **One live e2e layer per provider** (`l1<name>`): anonymous connect → real search →
   real detail fetch. Assertions are invariants (shape/identity), never live-content
   snapshots; fail loudly on upstream blocks.
4. Gates per task: `ruff format/check src/ tests/ scripts/`, `mypy src/ tests/`,
   `ALLOW_MODEL_REQUESTS=False pytest -q`. Frontend touched → all frontend gates.
5. No credentials anywhere: anonymous only; no tokens in configs, tests, or git.
6. Honest capabilities: if an endpoint doesn't exist upstream, drop the capability
   from the manifest (YouTube-FEED precedent), never fake it.
7. Docs sync per commit: module docs + `docs/changelog.md`; e2e incidents →
   `docs/e2e-testing-log.md`.
8. Workers implement via subagent; parent reviews, runs live layers, commits.

---

## Phase 1 — Bangumi ✅ (2026-08-14)

Transport exists; this phase proves it live and wires anonymous access.

- [x] 1.1 Verify anonymous connect: does the bangumi builder accept
  `builtin.anonymous` (YouTube precedent)? If the manual PAT form blocks it, make
  anonymous the zero-config path and keep PAT as optional upgrade. TDD: unit test
  that manifest exposes an anonymous connection method.
- [x] 1.2 Unit tests (extend `test_public_api_providers.py` / transports if gaps):
  search page normalization, subject-type mapping, cursor/dedupe, typed errors
  (429→RATE_LIMITED, 403→ACCESS_DENIED, timeout→NETWORK_UNAVAILABLE).
- [x] 1.3 Live e2e layer `l1bangumi` (marker + LAYERS + `tests/e2e/test_l1_bangumi.py`):
  anonymous connect → search "孤独摇滚" → invariant ids/canonical `bgm.tv/subject/<id>`
  URLs → detail fetch 200-equivalent with title present.
- [x] 1.4 Enable `bangumi` in `config.e2e.example.toml`, `config.docker.toml`,
  `config.example.toml` (all anonymous).
- [x] 1.5 Reviewer PASS → gates → commit. UI spot-check via agent-browser:
  providers view shows bangumi connected; search → detail renders metadata.
  **UI check caught a live bug**: `limit=20` searches 503'd because upstream
  rows with empty-string image URLs failed `image_url` pattern validation and
  poisoned the whole page → fixed at the shared seam (`... or None`
  normalization in `_subject`) + transport test with the real dirty-row shape;
  detail renderer gained `image_url`/`summary` fallbacks (provider-agnostic).
- [x] 1.6 Docs: `docs/modules/content-provider-authoring.md` capability matrix,
  changelog.

## Phase 2 — V2EX 🔲

Same shape as Phase 1.

- [ ] 2.1 Anonymous connect verification/fix (TDD).
- [ ] 2.2 Unit-test gaps: topic normalization, `www.v2ex.com/t/<id>` canonical URLs,
  typed errors.
- [ ] 2.3 Live e2e layer `l1v2ex`: anonymous connect → search → invariant ids →
  topic detail with title.
- [ ] 2.4 Enable in the three configs.
- [ ] 2.5 Reviewer PASS → gates → commit → UI spot-check.
- [ ] 2.6 Docs + changelog.

## Phase 3 — Linux.do 🔲 (new transport)

- [ ] 3.1 TDD transport: `HttpxLinuxDoTransport` behind the existing
  `LinuxDoTransport` protocol — Discourse JSON: search `GET /search.json?q=`,
  latest/top `/latest.json`, topic fetch `/t/<id>.json`. Failing tests first with
  recorded-shape fixtures (no live calls in unit tests).
- [ ] 3.2 Typed error mapping (429/403/timeout/invalid) + allowlist payload
  normalization (YouTube tuple-leak precedent: only declared fields reach models).
- [ ] 3.3 `projections.py`: topic → ContentPreview; identity =
  `linux.do/t/topic/<id>` canonical URL, numeric topic id.
- [ ] 3.4 Wire in composition (replace `unavailable`); anonymous connect.
- [ ] 3.5 Live e2e layer `l1linuxdo`: anonymous connect → search → detail.
- [ ] 3.6 Enable in configs. Reviewer PASS → gates → commit → UI spot-check.
- [ ] 3.7 Docs + changelog.

## Phase 4 — Weibo 🔲 (port old anonymous client)

- [ ] 4.1 TDD port of `main:src/openbiliclaw/sources/weibo_client.py` into the
  provider contract: anonymous visitor flow (`visitor.passport.weibo.cn/…/genvisitor2`
  → in-memory `SUB` cookie, never persisted, never user cookies), search via
  `m.weibo.cn/api/container/getIndex`, hot via `weibo.com/ajax/side/hotSearch`.
  Failing tests first (visitor-flow state machine, SUB refresh on 401/403).
- [ ] 4.2 Typed errors + allowlist normalization; HTML→text extraction for weibo
  bodies (port `_HTMLTextExtractor` behavior as unit-tested helper).
- [ ] 4.3 `projections.py`: mblog → ContentPreview; identity = mblog id /
  `weibo.com/status/<bid>` canonical URL.
- [ ] 4.4 Wire in composition; anonymous connect.
- [ ] 4.5 Live e2e layer `l1weibo`: anonymous connect → search → detail. NOTE:
  m.weibo.cn churns (dataabc/weibo-crawler broke 2025-06) — if the visitor flow is
  dead upstream, STOP, report, and re-scope rather than faking.
- [ ] 4.6 Enable in configs. Reviewer PASS → gates → commit → UI spot-check.
- [ ] 4.7 Docs + changelog.

## Phase 5 — All-platform final sweep 🔲

- [ ] 5.1 `content.enabled` e2e assertion updated to all enabled providers.
- [ ] 5.2 Full e2e run, every platform: `l0 l1a l1b l1youtube l1bangumi l1v2ex
  l1linuxdo l1weibo l2 l3 l4 l5 l6` (l7 UI pass with all six providers visible:
  providers view + per-provider search → detail spot check, traces in
  `data-e2e/ui-traces/<ts>/`).
- [ ] 5.3 Hermetic unit suite + frontend gates green.
- [ ] 5.4 `docs/e2e-testing-log.md` incident/results entry; capability matrix in
  authoring docs lists all six providers honestly; changelog finalized.
- [ ] 5.5 Final reviewer PASS over the whole diff → final atomic commit(s).

## Done definition

Six providers (bilibili, youtube, bangumi, v2ex, linuxdo, weibo) enabled in all
configs, each with: anonymous connect, hermetic unit coverage, one live e2e layer,
UI-verified search→detail. All gates + all layers green in one final sweep.

## Open risks

- Weibo visitor-flow upstream churn (4.5 stop condition).
- V2EX rate limits on search endpoint — keep live layer to 2–3 requests.
- Linux.do Cloudflare challenges non-browser UAs — if seen, set honest
  User-Agent; if blocked anyway, drop to stub + document (never fake).
