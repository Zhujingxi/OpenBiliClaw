# E2E Testing Plan — Real Stack, Layer by Layer

Status: **maintained real-stack validation plan**. Layers L0–L6 preserve their completed execution record; L7 now has an automated, opt-in browser journey. Current commands and capability truth live in `AGENTS.md` and `docs/modules/`.

This plan began from a 716-test hermetic baseline and then exercised the product bottom-up against the real stack. Historical layer descriptions preserve what was observed at each boundary, including gaps that later phases or subsequent work filled; they are not current architecture claims.

## 0. Credential safety rules (hard constraints)

- **No token, key, or cookie ever enters the git repo** — not in config files, test fixtures, scripts, docs, commit messages, or subagent task text.
- `data-e2e/` is gitignored in full. It holds: `kimi_api_key.txt` (the limited test-only Kimi key), `credentials.json` (vault), the test DB, and reports.
- Bilibili cookies extracted from Chrome stay on this machine: the extraction script reads the local Chrome cookie DB and passes values **directly into the product's own credential API** → vault. No cookie file is ever written to the repo.
- Config files contain only `secret_ref = "vault:cred_..."` opaque references.
- A pre-commit audit scans new/changed content for likely secret values; field names and synthetic credential fixtures are allow-listed.

## 1. Test profile

```text
config.e2e.toml        # new schema: [model] [embedding] [content] [recommendation] [host] [runtime]
data-e2e/              # gitignored entirely
  kimi_api_key.txt     # test-only key, loaded by runner -> vault
  credentials.json     # vault backend (0600)
  openbiliclaw.db      # grows progressively across layers
  reports/l0.json ...  # machine-readable per-layer results
```

The legacy `config.toml` / `data-v2/` are never touched.

## 2. External services

| Role | What | Integration |
|---|---|---|
| LLM | `kimi-for-coding` @ `https://api.kimi.com/coding/v1` | models.dev catalog id `kimi-for-coding`; current catalog declares the Anthropic protocol |
| Embedding | `BAAI/bge-small-zh-v1.5` via **infinity-emb** (OpenAI-compatible `/v1`) | separate local service; the app never serves models |
| Platform | Bilibili — anonymous + authenticated (local Chrome session); YouTube — anonymous | product providers; YouTube extraction delegated to yt-dlp |

## 3. Harness

- `tests/e2e/` with pytest markers `e2e_l0`...`e2e_l7` plus `e2e_l1youtube`, **excluded from the default suite** — the hermetic unit suite stays offline (`ALLOW_MODEL_REQUESTS=False`).
- `scripts/e2e.py l<N>` — starts the embedding service if down, loads the e2e profile, runs one layer, writes `data-e2e/reports/l<N>.json` + console summary, non-zero exit on failure.
- L7 uses the optional Python `playwright` extra and installed Chromium against `openbiliclaw serve` plus the built Vue `dist`; it has no alternate app or JavaScript browser-test dependency.
- Assertions are **invariants** (shape, non-empty, dimension, dedupe), never snapshots of live content or model prose.

## 4. Layers (strictly sequential)

**L0 — Environment & model connectivity**
Config validates; vault seeds from `kimi_api_key.txt`; `openbiliclaw check` green; **real Kimi chat round-trip**; **real embedding round-trip** (512-dim, batch invariants). `check` validates startup only; capability probe persistence is not implemented.
*Completed fix:* `NativeEmbeddingTransport` omits the OpenAI-only `dimensions` parameter for custom endpoints while retaining response-dimension validation in `EmbeddingService`.

**L1a — Content acquisition, anonymous**
Real Bilibili public APIs: popular feed, search, and video detail through the production composition/facade path. Assert real BVID identity and field invariants, repeated-detail identity stability, the provider's 50-item page cap, and typed rate/network failures. Search and detail are reads and do not persist; comments/tags are not exposed provider capabilities.

**L1b — Content acquisition, authenticated**
First fix `ConnectSource` idempotency versus in-memory connection restoration across process restarts (TDD): a cached `CONNECTED` result must not leave `AccessService` disconnected. The L1b test then extracts the required Bilibili session fields from local Chrome in-process → submits through the **product's own manual access path** (auth flow itself is tested, not bypassed) → vault → `nav` identity verified → authenticated history/related fetches. Cookie values stay only in pytest process memory; the standalone script is a structural diagnostic using the same helper.

**L1-YouTube — anonymous acquisition through yt-dlp**
Real YouTube search, stable-video fetch, and channel creator-page extraction run through production Composition with no API key. Tests assert canonical 11-character IDs, dedupe, bounded page sizes, non-empty metadata, and typed malformed-reference failures. Results are invariant-based, never title snapshots. YouTube removed generic Trending in July 2025, so the provider honestly dropped `FEED` rather than substituting search or music charts; discovery must follow manifest capabilities.

**L2 — Observations**
View/like/feedback events through the product path against real ingested content, plus real account history as bootstrap. Assert durability across a rebuilt application graph, producer-key idempotency, deterministic insertion-cursor replay, and `content_references` landing/dedupe through observation ingress (the architecture's only content-reference persistence path). The direct liked-event helper uses the canonical neutral `RecommendationFeedbackPayload` without exploration attribution and with the default unexposed state. Because layers share durable state sequentially, public acquisition reuses a restored connected Bilibili access status that grants `read_public` and creates anonymous access only when disconnected; it never replaces credentials. `content_cache` remains empty because observations carry identity, not provider projection bodies.

**L3 — Understanding**
Profile derivation with real Kimi thinking enabled, persistence across graph rebuild, and inspect/correct workflows. The real Assistant matrix covers Kimi through its catalog Anthropic protocol and native DeepSeek chat; required Assistant/Understanding routes must validate, while incompatible optional RecommendationBrief/vision services stay honestly absent. The preference analyzer retains hard limits with a domain-sized output/total allowance for provider-counted reasoning plus its bounded typed batch. Its Bilibili embedding smoke and the later L4 refill use the L2 shared restored-or-anonymous access helper, so sequential runs reuse only a connected handle whose verification grants `read_public` and never reconnect over durable access. Composition exposes the configured `EmbeddingService`; a real-title semantic smoke verifies 512 dimensions and BGE query-only instruction behavior.

**L4 — Recommendation**
Ranked recommendations from the real accumulated pool: non-empty ranked output with durable reasons, contribution/rank invariants, fixed provider/creator quotas, bounded refill, duplicate-safe repeat refill, and profile-shaped discovery topics. Recommendation discovery is text-query based and has no semantic retrieval consumer or durable projection text, so L4 resolves the L3 deferral by **not** adding a speculative embedding index; one will be designed only when semantic discovery exists.

**L5 — Application workflows**
Full loop on live `openbiliclaw serve` via `/v1` HTTP only: bootstrap/status and typed errors → supervised refill → delivered recommendations with stable shown IDs → validated/idempotent feedback → content-linked preference observation → scheduled profile shift → restart persistence. The profile read surface exposes bounded preference text but not evidence IDs, so linkage is proved by the accepted observation receipt plus the run-unique profile value rather than a new inspection API.

**L6 — Docker deployment (primary deploy method)**

1. Compose updated to new architecture: `embedding` sidecar (infinity-emb + bge-small-zh, healthchecked), stale sidecar comments removed, config template points at `http://embedding:7997`. Build the sidecar with `infinity_emb[torch,server]`, not `[all]`, to keep the image smaller and avoid the optional Optimum dependency. App container stays model-serving-free.
2. Build + boot: image builds; `/v1/runtime/health` green; `openbiliclaw check` inside container; Vue SPA served.
3. Core L1–L5 flow re-run against the containerized stack.
4. Persistence: write → `down && up` → data survives in `/app/runtime` volume.
5. Secrets injected at runtime (env/file mount → vault inside container), never baked into the image.
6. Resolved the surfaced restart decision: access connections remain process-local and the vault has no provider/account-to-reference mapping, so clients resubmit provider form credentials after container restart; Docker does not invent a durable mapping.
7. `docs/docker-deployment.md` rewritten to verified reality.
8. The L6 harness explicitly selects tracked `docker-compose.yml` on every Compose call while retaining its dedicated project, volumes, port, and runtime key-file path. Ignored developer overrides therefore cannot change the production-stack validation graph.
9. Fresh-state replenishment may combine up to 20 search and 20 feed candidates. The shared evaluation seam must preserve that mixed order, invoke the evaluator only in its declared batches of at most 20, and apply selection across the complete evaluated set. Hermetic regression coverage is landed; the fresh real-Docker rerun follows after merge.

**L7 — Hosts & UI**
`tests/e2e/test_l7_assistant_ui.py` drives Python Playwright against the production composition and built Vue assets. It verifies the localized Assistant journey (`en`, `zh-CN`, `zh-TW`), New chat, one bounded real-model turn, context presentation, safe optional reasoning/tool presentation, server transcript/tool-summary hydration, Stop cancellation, console/page/network cleanliness, and the 390×844 mobile navigation/overflow boundary. The only accepted failed response is the initial conversation lookup 404 for an unsent fresh local conversation. Screenshots, server logs, and the runner report stay under ignored `data-e2e/reports/`.

Interactive browser exploration remains useful for discovery, but it is no longer the L7 regression gate. Run `npm --prefix frontend run build`, install `.[browser]` plus Chromium, then execute `scripts/e2e.py l7`.

## 5. Process per layer

Implement harness + tests (TDD) → run against real stack → fix bugs → **independent review** → gates green (ruff, mypy, unit pytest, frontend gates when touched, **credential-leak grep**) → **atomic commit** → next layer.

## 6. Prerequisites installed at L0

`infinity-emb` (test infrastructure, not an app dependency); bge-small-zh-v1.5 via `hf` CLI. Local L0 uses infinity-emb 0.0.77 with `optimum<2` and a Typer version compatible with the installed Click. Docker option: if the L6 sidecar pattern proves clean, dev-loop embedding may switch to the same container.

## Progress

| Layer | Status | Commit | Notes |
|---|---|---|---|
| L0 environment | completed | f8417db6 | 4 real E2E tests passed; harness and local embedding server verified; see testing log |
| L1a content anonymous | completed | b369fa08 | 2 real E2E tests passed; real API adapters corrected; see testing log |
| L1b content authenticated | completed | 3d28bb46 | 2 real E2E tests passed; restart replay and authenticated native adapters corrected; see testing log |
| L1 YouTube anonymous | completed | — | 2 real E2E tests passed; yt-dlp search/fetch/creator, identity, page bounds, and typed invalid refs verified |
| L2 observations | completed | f9799dca | 2 real E2E flows passed; content landing/dedupe, restart durability, neutral feedback payload, feedback idempotency, cursor replay, authenticated history import, and durable access-state reuse are covered; see testing log |
| L3 understanding | completed | 03a05f42 | real E2E covers composed embeddings, profile derivation/persistence/correction, and Kimi Assistant forced-output tools with thinking disabled; durable semantic index deferred to L4 |
| L4 recommendation | completed | 4356d0c1 | 2 real E2E tests passed; real refill/ranking/reasons/diversity/restart verified; duplicate refill and profile-to-discovery seams fixed; semantic index intentionally not added |
| L5 workflows | completed | 71c16616 | 2 live-server E2E tests passed; HTTP feed delivery/feedback state machine, typed errors, scheduled profile shift, and restart persistence verified; see testing log |
| L6 docker | completed | 446f055d | 1 real Docker E2E passed; sidecar/build/boot/check/core loop/restart persistence verified; bearer and provider-form host blockers fixed; see testing log |
| L7 UI | completed (automated) | current branch | `scripts/e2e.py l7`: 1 passed against production serve + built Vue + real model; ignored report at `data-e2e/reports/l7.json`; see testing log |
