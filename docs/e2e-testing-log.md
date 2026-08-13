# E2E Testing Log

This is the durable test → debug → fix → test trace for the real-stack plan. It contains no credentials or external response bodies.

## L0 — Environment and model connectivity

### Setup

- Stored the limited test key only in gitignored `data-e2e/kimi_api_key.txt` with mode `0600`.
- Installed infinity-emb 0.0.77 into the local `.venv` as test infrastructure only; it is not an application dependency.
- Downloaded `BAAI/bge-small-zh-v1.5` through the Hugging Face CLI cache.
- Seeded the test vault and generated gitignored `data-e2e/config.e2e.toml` from `config.e2e.example.toml`.

### Trace

1. **Test:** added a unit test requiring custom embedding endpoints to omit `dimensions`.
   **Failure:** the fake native client recorded `dimensions=2` for a custom endpoint.
   **Root cause:** `NativeEmbeddingTransport.embed()` always passed the OpenAI-specific optional parameter.
   **Fix:** branch only at the native SDK call: official OpenAI keeps `dimensions`; endpoint overrides omit it. `EmbeddingService` remains the single vector-length validator.
   **Retest:** embedding transport suite passed (7 tests).

2. **Test:** install infinity-emb using the requested pip executable.
   **Failure:** this uv-created virtual environment has no `pip` executable or pip module.
   **Root cause:** the environment is managed by uv.
   **Fix:** installed with `uv pip install --python .venv/bin/python` instead; no project dependency changed.
   **Retest:** infinity-emb installed.

3. **Test:** import and start infinity-emb 0.0.77 after `[all]` installation.
   **Failure:** `ModuleNotFoundError: optimum.bettertransformer`.
   **Root cause:** infinity's optional-dependency guard checks that Optimum imports, but not that the `bettertransformer` submodule still exists; Optimum 2.x removed it.
   **Escalation:** another model checked live package metadata and wheel contents before the fix.
   **Fix:** test environment only: install `optimum<2`, resolving to 1.27.0. No repository dependency was added.
   **Retest:** `import infinity_emb` passed.

4. **Test:** start infinity-emb after the Optimum fix.
   **Failure:** Typer 0.12.5 with the installed Click 8.3.1 raised `TypeError: Secondary flag is not valid for non-boolean flag` before serving.
   **Root cause:** infinity's declared Typer range resolved to an old CLI implementation incompatible with current Click.
   **Fix:** test environment only: upgrade Typer within its current supported major range.
   **Retest:** CLI help succeeded; server became healthy using the Torch CPU engine.

5. **Test:** call the planned OpenAI-compatible path `/v1/embeddings`.
   **Failure:** HTTP 404.
   **Root cause:** infinity-emb 0.0.77's `v2` server exposes `/embeddings` at the root, not under `/v1`.
   **Fix:** corrected the e2e endpoint to `http://127.0.0.1:7997` after checking the server's OpenAPI paths.
   **Retest:** one real Chinese input returned exactly 512 floats.

6. **Test:** execute `scripts/e2e.py l0` directly.
   **Failure:** system Python could not import the application, then the first rerun exposed nested module marker syntax rejected by the current pytest.
   **Root cause:** the script shebang did not guarantee the project venv, and `pytestmark` was a tuple containing a tuple rather than a list of marks.
   **Fix:** the runner re-executes itself with `.venv/bin/python`; the test module uses a list of marks.
   **Retest:** runner completed and wrote `data-e2e/reports/l0.json`.

7. **Test:** run the full real L0 layer.
   **Result:** 4 passed: validated profile/vault resolution; real Kimi chat through `ModelFactory → AIRuntime.run()`; real single/batch BGE embeddings with 512 dimensions and semantic separation; real `openbiliclaw check` startup/shutdown with the isolated database.

8. **Review test:** compare the runner's JSON report with its real multi-line pytest output.
   **Failure:** pytest printed `4 passed`, but the report recorded zero because `_pytest_count` only matched a summary at the start of the whole string.
   **Root cause:** the regular expression used a line-start anchor without multiline mode; its unit test supplied only single-line output.
   **Fix:** enable multiline matching and add a realistic dots-line plus summary-line regression case.
   **Retest:** the real L0 run passed four tests and `data-e2e/reports/l0.json` recorded `passed: 4`, `failed: 0`.

### Surfaced architecture finding

The original plan expected `openbiliclaw check` to persist and reuse capability-verification fingerprints. Actual code has only opt-in probe primitives and an unused in-memory `CapabilityVerificationStore`; Composition neither runs nor persists probes. Building durable verification was not approved for L0 and no production consumer currently requires it, so L0 tests the real `check` contract (configuration, graph construction, migrations, staged lifecycle, readiness, reverse shutdown). The first semantic consumer in L3 is the next point to decide whether durable probes are necessary.

### Reproduction

```bash
~/.local/bin/uv pip install --python .venv/bin/python 'infinity-emb[all]' 'optimum<2' 'typer>=0.16,<1'
~/.local/bin/hf download BAAI/bge-small-zh-v1.5
./scripts/e2e.py l0
```

The runner starts the embedding service when it is not healthy and writes the machine-readable result under `data-e2e/reports/`.

## L1a — Anonymous Bilibili acquisition

### Trace

1. **Test:** real popular-feed acquisition through the composed Bilibili provider.
   **Failure:** the transport rejected the HTTP 200 response as invalid: the synthetic contract expected `data.items`, while the real endpoint returns `data.list` with native Bilibili field names and an extra `ttl` envelope field.
   **Root cause:** unit fixtures modeled an invented normalized wire schema instead of the provider's actual endpoint schemas.
   **Fix:** keep the provider-owned strict `BilibiliVideo` model, but move endpoint-specific normalization into `BilibiliClient`: popular `data.list`, search `data.result`, and detail raw `data` now normalize native identity, owner, statistics, duration, cover URL, and availability before entering the integration boundary.
   **Retest:** popular feed and detail returned typed real videos with BVID identity, title, and positive duration.

2. **Test:** real anonymous search through `CompositionFacade.search_content()`.
   **Failure:** Bilibili returned an anti-bot HTML page instead of JSON.
   **Root cause:** the shared application user-agent identifies itself as a service client, while this public search endpoint expects a browser-compatible request context and an anonymous browser cookie established from the site.
   **Fix:** the Bilibili-owned HTTP transport now supplies a browser-compatible user-agent, search referer, and a bounded homepage bootstrap on the same scoped client before anonymous search. No session credential is stored.
   **Retest:** real search returned typed results.

3. **Test:** repeat the same live search and assert an identical ordered result set.
   **Failure:** Bilibili legitimately re-ranked results between adjacent calls.
   **Root cause:** live search ordering is dynamic; the assertion was a snapshot assumption, not a product invariant.
   **Fix:** assert BVID/title invariants on both result sets and verify identity stability by fetching the same selected reference twice. No product code changed for live re-ranking.
   **Retest:** both result sets validated and repeated detail preserved the exact `ContentRef`.

4. **Test:** reconnect anonymous access in a fresh application graph using a fixed idempotency key.
   **Failure:** the durable idempotency journal returned the prior `CONNECTED` result without recreating the in-memory `AccessService` connection; the next public search reported that the source was not connected.
   **Root cause:** `ConnectSource` returns the durable idempotency result before checking/restoring the live connection. Exact repro: connect with key K → stop application → build/start a fresh graph on the same data directory → connect with K returns cached `CONNECTED` → `connected_handle()` is `None` and search fails.
   **L1a containment:** use a unique idempotency key per graph so this acquisition layer tests the intended provider path. This is not considered fixed; L1b must repair restart consistency first, TDD, in the access/idempotency path.
   **Retest:** each L1a graph acquired its anonymous handle with a unique command key.

5. **Test:** request 100 feed items and point the supported transport seam at a dead loopback port.
   **Result:** the provider returned no more than its 50-item cap, and the dead endpoint raised typed `PROVIDER_UNAVAILABLE`. Unit coverage also verifies HTTP 412 and 429 map to typed `RATE_LIMITED`.

### Surfaced architecture findings

- The plan assumed search/fetch landed content in `content_references` / `content_cache`. Actual Application search and detail workflows are read-only; `content_references` is persisted only by Observation Ingress. Adding persistence to reads would create a parallel path, so database landing/dedupe moved to L2 where the architecture owns it.
- No configurable per-provider request budget exists. L1a therefore verifies the landed provider page cap and typed upstream rate-limit behavior rather than inventing budget configuration.
- Bilibili comments and tags are not exposed capabilities. L1a verifies the available detail enrichment only.
- Anonymous search may reorder results between calls; tests assert stable identity parsing, not live ordering.

### Reproduction

```bash
./scripts/e2e.py l1a
cat data-e2e/reports/l1a.json
```

The real run made a small number of requests and reported two passed tests.

## L1b — Authenticated Bilibili acquisition

### Trace

1. **Test:** reuse a completed `ConnectSource` idempotency key after constructing a fresh access service with no live handle.
   **Failure:** the workflow returned the durable `CONNECTED` result without calling `AccessService.connect`; subsequent content reads reported not connected.
   **Root cause:** the journal cached the transport-safe result, while the connection and opaque handle are intentionally process-local. The workflow treated durable result replay as proof of live runtime state.
   **Fix:** reuse a cached connect result only when `connected_handle(provider_id, account_id)` confirms a matching live connection. Otherwise execute the normal connect path; realistic restart tests resubmit the credential through the manual form.
   **Retest:** the failing unit test reconnects once after restart while same-process idempotency still connects only once.

2. **Test:** feed a real-shaped native Bilibili search row through client normalization.
   **Failure/debt:** L1a production fixes had live coverage but no hermetic fixture pinning digit-string integers, protocol-relative covers, HTML titles, and `MM:SS` durations together.
   **Fix:** added one native-shape unit test and removed the unused `_JSON` adapter.
   **Retest:** strict normalized video fields match the provider-independent model.

3. **Test:** parse authenticated history plus related responses and inspect their outgoing query parameters.
   **Failure:** history native owner/cover/time fields and nested history aid were not mapped; related returns a bare `data` array rather than a page object; history sent generic paging parameters.
   **Fix:** normalize history `author_name`/`author_mid`/`cover`/`view_at`/`history.oid`, accept related bare arrays, send history `ps/max/view_at`, and send creator `pn/ps` while parsing its nested `list.vlist`.
   **Retest:** hermetic real-shape tests pass for history and related; the authenticated E2E layer exercises history, related, nav identity, status, public search on the credential handle, and restart reconnect usability.

4. **Design test:** execute Chrome extraction as a process before a separate pytest process.
   **Finding:** manual connect vaults the secret but intentionally stores no durable provider/account-to-credential mapping; stopping the script discards the only live handle. A later pytest process cannot reconnect without seeing the cookie again.
   **Decision:** keep extraction and authenticated tests in one pytest process, with cookie values memory-only. A thin diagnostic script uses the same helper and never prints values. Durable reconnect versus client resubmission is an open product/deployment decision for L6, not invented in L1b.

5. **Test:** run the diagnostic and real L1b layer against Chrome.
   **Failure:** manual verification returned `unavailable/network_unavailable` even though the provider was reachable.
   **Root cause:** real nav returns numeric `mid`, while the synthetic schema required a string; the resulting response-contract validation failure was then incorrectly collapsed into `NETWORK_UNAVAILABLE` by the verifier.
   **Escalation:** another model verified the two-defect diagnosis before the fix.
   **Fix:** nav identity now normalizes the real `isLogin`/`uname` aliases, accepts non-negative integer `mid`, and requires it positive when logged in; transport failures use the distinct integration `NETWORK_UNAVAILABLE` code, while parse/contract failures project to new sanitized `PROVIDER_RESPONSE_INVALID` degraded evidence.
   **Retest:** the verifier connected to the real account; the next run exposed and fixed the nav wire aliases (`isLogin`/`uname`) and history's nested `history.bvid`. The final real L1b layer passed both authenticated and restart-reconnect tests.

6. **Credential-output incident:** the first failing pytest assertion rendered the dataclass representation of `BrowserCookies`, exposing the real values in local terminal output. The values were not written to tracked files or git; generated reports contain only test names/counts. `BrowserCookies.__repr__` and `__str__` are now fixed redacted strings with regression coverage, and the E2E invocation does not enable pytest local-variable dumps. The affected Bilibili session should still be rotated at the user's convenience for defense in depth.

7. **Test infrastructure:** installed `secretstorage` and `pycryptodome` into the local `.venv` only. The helper detects Chrome/Chromium databases, takes a SQLite backup (including live WAL state), decrypts Linux v10/v11 AES-CBC values via libsecret, selects only the required/optional Bilibili names, and prints only names plus lengths.

### Reproduction

```bash
./scripts/e2e_bilibili_cookies.py  # optional structural/verifier diagnostic; never prints values
./scripts/e2e.py l1b               # extraction + authenticated checks happen in one process
cat data-e2e/reports/l1b.json
```

The real L1b run reported two passed tests; missing authenticated fields fail explicitly rather than skip.
