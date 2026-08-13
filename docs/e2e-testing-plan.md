# E2E Testing Plan — Real Stack, Layer by Layer

Status: **active**. Progress is tracked in the table at the bottom; this file is updated at every layer boundary.

The refactor is complete (716 hermetic unit tests, all mocked). Nothing has run against real APIs, real models, or a real database yet. This plan tests the product bottom-up against the real stack: content acquisition first, then layer by layer outward.

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
| LLM | `kimi-for-coding` @ `https://api.kimi.com/coding/v1` | `provider = "openai"` + endpoint override (native PydanticAI path) |
| Embedding | `BAAI/bge-small-zh-v1.5` via **infinity-emb** (OpenAI-compatible `/v1`) | separate local service; the app never serves models |
| Platform | Bilibili — anonymous + authenticated (local Chrome session) | product's own provider + credential API |

## 3. Harness

- `tests/e2e/` with pytest markers `e2e_l0`...`e2e_l7`, **excluded from the default suite** — the 716-test unit suite stays hermetic (`ALLOW_MODEL_REQUESTS=False`).
- `scripts/e2e.py l<N>` — starts the embedding service if down, loads the e2e profile, runs one layer, writes `data-e2e/reports/l<N>.json` + console summary, non-zero exit on failure.
- Assertions are **invariants** (shape, non-empty, dimension, dedupe), never snapshots of live content.

## 4. Layers (strictly sequential)

**L0 — Environment & model connectivity**
Config validates; vault seeds from `kimi_api_key.txt`; `openbiliclaw check` green; **real Kimi chat round-trip**; **real embedding round-trip** (512-dim, batch invariants). `check` validates startup only; capability probe persistence is not implemented.
*Completed fix:* `NativeEmbeddingTransport` omits the OpenAI-only `dimensions` parameter for custom endpoints while retaining response-dimension validation in `EmbeddingService`.

**L1a — Content acquisition, anonymous**
Real Bilibili public APIs: popular feed, search, and video detail through the production composition/facade path. Assert real BVID identity and field invariants, repeated-detail identity stability, the provider's 50-item page cap, and typed rate/network failures. Search and detail are reads and do not persist; comments/tags are not exposed provider capabilities.

**L1b — Content acquisition, authenticated**
First fix `ConnectSource` idempotency versus in-memory connection restoration across process restarts (TDD): a cached `CONNECTED` result must not leave `AccessService` disconnected. The L1b test then extracts the required Bilibili session fields from local Chrome in-process → submits through the **product's own manual access path** (auth flow itself is tested, not bypassed) → vault → `nav` identity verified → authenticated history/related fetches. Cookie values stay only in pytest process memory; the standalone script is a structural diagnostic using the same helper.

**L2 — Observations**
View/like/feedback events through the product path against real ingested content, plus real account signals (history/favorites) as bootstrap. Assert durability, idempotency, replay, and `content_references` landing/dedupe through observation ingress (the architecture's only content-reference persistence path).

**L3 — Understanding**
Profile derivation + semantic ingestion with real Kimi + real embeddings. Assert inspectable/correctable profile, embedding index at correct dimension.
*Candidate tasks:* wire the first production embedding consumer in composition (contract landed, unwired); decide whether this first real consumer needs durable capability-probe results. BGE asymmetric retrieval should prepend the model's query instruction to query texts; infinity does not add it automatically.

**L4 — Recommendation**
Ranked recommendations from the real accumulated pool: non-empty ranked output with reasons, diversity/budget rules, refill path.

**L5 — Application workflows**
Full loop on live `openbiliclaw serve` via `/v1` HTTP only: bootstrap → refill → recommend → feedback → profile shifts.

**L6 — Docker deployment (primary deploy method)**

1. Compose updated to new architecture: `embedding` sidecar (infinity-emb + bge-small-zh, healthchecked), stale sidecar comments removed, config template points at `http://embedding:7997`. Build the sidecar with `infinity_emb[torch,server]`, not `[all]`, to keep the image smaller and avoid the optional Optimum dependency. App container stays model-serving-free.
2. Build + boot: image builds; `/v1/runtime/health` green; `openbiliclaw check` inside container; Vue SPA served.
3. Core L1–L5 flow re-run against the containerized stack.
4. Persistence: write → `down && up` → data survives in `/app/runtime` volume.
5. Secrets injected at runtime (env/file mount → vault inside container), never baked into the image.
6. Resolve the surfaced restart decision: access connections are process-local and the vault has no provider/account-to-reference mapping, so choose and verify either durable reconnection mapping or client credential resubmission on container start.
7. `docs/docker-deployment.md` rewritten to verified reality.

**L7 — Hosts & UI**
agent_browser session against the live backend: setup, pool view, recommendations, feedback, profile view. Bugs filed/fixed as found. No scripted browser suite — UI testing is driven interactively by the agent.

## 5. Process per layer

Implement harness + tests (TDD) → run against real stack → fix bugs → **independent review** → gates green (ruff, mypy, unit pytest, frontend gates when touched, **credential-leak grep**) → **atomic commit** → next layer.

## 6. Prerequisites installed at L0

`infinity-emb` (test infrastructure, not an app dependency); bge-small-zh-v1.5 via `hf` CLI. Local L0 uses infinity-emb 0.0.77 with `optimum<2` and a Typer version compatible with the installed Click. Docker option: if the L6 sidecar pattern proves clean, dev-loop embedding may switch to the same container.

## Progress

| Layer | Status | Commit | Notes |
|---|---|---|---|
| L0 environment | completed | f8417db6 | 4 real E2E tests passed; harness and local embedding server verified; see testing log |
| L1a content anonymous | completed | b369fa08 | 2 real E2E tests passed; real API adapters corrected; see testing log |
| L1b content authenticated | completed | — | 2 real E2E tests passed; restart replay and authenticated native adapters corrected; see testing log |
| L2 observations | pending | — | |
| L3 understanding | pending | — | |
| L4 recommendation | pending | — | |
| L5 workflows | pending | — | |
| L6 docker | pending | — | |
| L7 UI | pending | — | |
