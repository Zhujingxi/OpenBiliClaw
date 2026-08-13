# E2E Testing Log

This is the durable test → debug → fix → test trace for the real-stack plan. It contains no credentials or external response bodies.

## Catalog-backed model configuration

The L5 live HTTP check now reads the catalog and requires both `deepseek` and `kimi-for-coding`, then starts an isolated server against a throwaway config/data copy, submits the DeepSeek key through the write-only PUT contract, and verifies the GET projection resolves `deepseek` through the OpenAI protocol family with `secret_configured=true`. Neither the real E2E profile nor its vault is mutated; assertions and logs contain no key or credential reference. The existing L3 provider matrix remains the runtime construction proof.

## Catalog-driven provider resolution

models.dev currently declares `kimi-for-coding` with `npm = "@ai-sdk/anthropic"` and `api = "https://api.kimi.com/coding/v1"`; DeepSeek declares the OpenAI-compatible protocol family and `https://api.deepseek.com`. E2E templates now omit project-owned endpoint pins and Kimi's OpenAI-only disable-thinking workaround. Live verification passed 4/4 after resetting the isolated accumulated E2E database: Kimi Assistant and preference analysis succeeded through `AnthropicProvider` with thinking enabled and automatic tool selection, while DeepSeek chat succeeded through the catalog OpenAI-family dispatch and PydanticAI's native `DeepSeekProvider`. The first Kimi attempt exposed that Anthropic's SDK appends `/v1/messages`; catalog base URLs ending in `/v1` are normalized once before provider construction. The matrix identifiers now explicitly describe `kimi-anthropic-thinking-on-prompted` and `deepseek-native-default`. Review follow-up reran the hermetic gates and the reset isolated live L3 matrix (4/4 passed). No credential or response body is written here.

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

## L2 — Observations

### Trace

1. **Discovery:** traced the public write surface from `POST /v1/observations` through `CompositionFacade.record_observations()` and Application `RecordObservations` to the sole `ObservationIngressService`; feedback uses `CompositionFacade.record_feedback()` and its observation unit of work. `SqliteObservationRepository` is the only writer of `content_references` from acquired content.

2. **Test:** acquire a real Bilibili search result through the production facade, submit host-opened and recommendation-liked observations, then submit the same observations again.
   **Result:** both initial rows were inserted; retries were typed duplicates under `(producer, idempotency_key)`. Direct database inspection found exactly one `content_references` row for the real provider identity. This confirms identity landing/deduplication without adding persistence to content reads.

3. **Test:** submit saved feedback twice through the Application feedback workflow, stop the graph, and rebuild it over the same `data-e2e/openbiliclaw.db`.
   **Result:** first feedback inserted and the retry did not; its generated observation plus the explicit opened/liked rows survived restart intact and in insertion order.

4. **Test:** replay observations using the documented insertion cursor.
   **Result:** the cursor page returned a durable cursor and the next page did not repeat the last consumed row. Replay remains the authoritative recovery path; committed-ID events are only latency hints.

5. **Test:** extract the authenticated Chrome session in memory, reconnect through the real manual verification path, fetch real watch history, and convert the returned identities into typed `provider_history_import` observations.
   **Failure:** after making history keys stable across runs, a duplicate receipt correctly returned the previously committed observation ID rather than the new retry object's ID; the replay assertion looked for the discarded retry IDs and found no rows.
   **Root cause:** the test confused caller-proposed observation identity with the ingress idempotency contract: `(producer, idempotency_key)` is authoritative and duplicate receipts point at the original committed observation.
   **Fix:** replay assertions use the receipt's committed IDs for both inserted and duplicate outcomes.
   **Retest:** history imports were inserted (or recognized as prior-run duplicates) with authenticated high-trust `provider_import` provenance, content references, and stable event identifiers derived by one-way hash of provider identity plus source timestamp. An immediate retry returned only typed duplicates. Assertions inspected shapes and counts only; no account, title, content identity, or cookie value was logged.

### Observed behavior

- Observation ingress persists content identity in `content_references`; it does not write `content_cache` because the observation contract carries `ContentRef`, not title/body/projection data. This is now explicit in module documentation.
- The current history capability exposes content timestamp and identity but no watch progress/event identifier. L2 therefore derives a stable one-way event digest from content identity plus source timestamp. A future provider-native history event ID/progress field would preserve multiple same-content watch events more precisely.
- L2 found no production defect requiring a code fix; the prior L1 adapter and restart fixes held under the observation pipeline.

### Reproduction

```bash
./scripts/e2e.py l2
cat data-e2e/reports/l2.json
```

The real run reported two passed tests and preserved its observations for later layers.

## L3 — Understanding

### Architecture trace and approved scope

1. Understanding consumes only bounded observation evidence; it has no embedding port or semantic trigger.
2. L2 intentionally persisted `content_references` only. `content_cache` has no title/body projection to embed.
3. Before L3, Composition did not construct `EmbeddingService`.
4. The target schema had no embedding artifact/index table.
5. Semantic retrieval's first concrete consumer is Recommendation discovery, not the canonical-profile owner.

Adding persistence in L3 would therefore invent table ownership, ingestion triggers, document sourcing, and a query API. The approved narrow scope constructs/exposes `EmbeddingService`, adds the BGE query seam, and runs a real-title in-memory smoke. L4 designs durable ingestion/indexing against Recommendation's actual consumer.

### Trace

1. **Test:** require Composition to expose an embedding service and require a BGE query instruction only on queries.
   **Failure:** `ApplicationServices` had no embedding boundary and `EmbeddingService.embed_query()` sent the same text as a document.
   **Fix:** Composition builds the native transport/service from `[embedding]`; `query_prefix_for_model()` selects the exact `BAAI/bge-small-zh-v1.5` model-card instruction `为这个句子生成表示以用于检索相关文章：`. Documents are unchanged.
   **Retest:** hermetic wiring/prefix tests passed; real Bilibili titles produced 512-float vectors and the queried title ranked first by cosine similarity.

2. **Test:** acquire real titles for semantic smoke.
   **Failure:** one live search response included a legacy numeric archive row with no BVID. That row cannot form the provider's stable `ContentRef`, and strict page validation rejected every valid row with it.
   **Fix:** Bilibili search normalization drops only rows without BVID before strict model validation. A real-shape regression fixture pins mixed legacy/valid behavior.
   **Retest:** live search and L1a/L1b acquisition remained green.

3. **Test:** process an honest preference statement with real Kimi through the production understanding analyzer.
   **Failure:** native tool-call structured output surfaced as safe `UnavailableError`; changing to PydanticAI `PromptedOutput(ProposalBatch)` then hit the bounded timeout.
   **Corrected root cause:** the earlier conclusion that Kimi did not reliably support native tool calls was wrong. Secret-safe probes later proved tool calls succeed with automatic tool choice. PydanticAI's forced output tool sent `tool_choice = "required"`; Kimi coding enables thinking by default and rejected that exact combination with HTTP 400. `required` plus `thinking = {type = "disabled"}` returned HTTP 200 with a proper tool call. Separately, canonical `ProposalBatch` remains an invalid model-authored contract because it requires SHA-256-derived IDs, aware timestamps, and canonical evidence objects that an LLM must not invent.
   **Escalation/experiments:** another model reviewed the schema and identified the deterministic-identity flaw. Direct, secret-safe endpoint probes measured: tiny JSON with `max_tokens=300` 2.56s; the complete 6.6KB canonical schema with an explicitly empty batch and `max_tokens=2048` 4.72s; OpenAI JSON-object response format 2.13s. Thus schema latency was not the blocker for the prompted draft path, while the native output-tool failure was specifically the thinking/required-tool-choice conflict.
   **Fix:** the routed preference agent uses `PromptedOutput(PreferenceDraftBatch)`, containing only dimension/value/confidence/evidence ID references. A pure adapter resolves evidence against the supplied batch, drops hallucinated references, and attaches deterministic IDs/timestamps before producing validated `ProposalBatch`. The three currently unrouted analyzer definitions still use canonical output and must receive equivalent draft contracts before production routing.
   **Retest:** valid/garbage prompted-output parsing and draft adaptation passed hermetically. Real Kimi returned a validated draft; the deterministic policy accepted it and committed the profile.

4. **Test:** persist, inspect, update, and correct the real profile.
   **Failure:** the first assertion compared an observation ID with canonical evidence IDs (`obs_` versus `ev_`).
   **Root cause:** test confusion, not product behavior; Understanding deliberately derives a separate evidence identity from each observation.
   **Fix:** assert the documented deterministic `ev_` identity.
   **Retest:** profile survived a graph rebuild, a second preference advanced the analyzer checkpoint/update path, public inspection returned a bounded v1 projection, and a remove correction persisted both override and audit observation.

### Reproduction

```bash
./scripts/e2e.py l3
cat data-e2e/reports/l3.json
./scripts/e2e.py l0
./scripts/e2e.py l1a
./scripts/e2e.py l1b
./scripts/e2e.py l2
```

The real L3 run reported two passed tests. L0–L2 remained green. No provider body, title, profile text, account identity, cookie, or key is recorded in reports or this log.

## L4 — Recommendation

### Architecture trace and semantic-index decision

Recommendation discovery currently consumes bounded text queries from `DiscoveryProfile` and calls provider search directly. It has no semantic query API, no durable provider projection text (`content_cache` remains empty), and no embedding consumer. Adding `content_embeddings` would still require inventing document ownership, ingestion triggers, and retrieval semantics. L4 therefore resolves the L3 deferral by adding **no table, repository, or embedding wiring**. A durable index will be designed only when semantic discovery is an actual consumer.

### Trace

1. **Test:** refill over the L3 profile and assert the resulting discovery topic.
   **Failure:** Recommendation hardcoded profile `default`, while L3 wrote `e2e-real`; even after using one canonical `DEFAULT_PROFILE_ID`, the only routed analyzer emits `PreferenceClaim` while both projections consumed only `StableInterestClaim` from an unrouted analyzer.
   **Root cause:** profile identity and claim-type seams made the landed L3→L4 chain decorative.
   **Fix:** use the shared `DEFAULT_PROFILE_ID` for understanding jobs, L3, and Recommendation; project content-dimension preferences into discovery interests and recommendation positive topics until the stable-interest analyzer is routed.
   **Retest:** a real Kimi-derived content preference appears in the discovery projection and shapes the provider query.

2. **Test:** assert that the profile-derived query survives as durable candidate topic provenance and that hard negative preferences reach both exclusion stages.
   **Failure:** `RecommendationPipeline` discarded `PlannedQuery.topic`, passed no avoidances to prefilter, and passed no negative preferences to selection.
   **Root cause:** Composition rebuilt candidates from flattened previews after discovery and supplied empty policy arguments.
   **Fix:** `DiscoveryService` returns each preview with its plan topic; candidates persist that topic; discovery avoidances feed hard prefilter and recommendation negative topics feed selection.
   **Retest:** hermetic tests pin topic persistence and both exclusion arguments; live rows contain the profile query in `topics[0]`.

3. **Test:** require every feed item to expose the documented reason after restart.
   **Failure:** expression records were persisted but `RecommendationRepository.feed()` never joined them, so the public feed exposed score contributions but no reason.
   **Root cause:** the model-free read projection joined selections and candidates only.
   **Fix:** feed joins the matching immutable expression, returns `RecommendationFeedItem.reason`, and orders each run by stored rank. OpenAPI/client snapshots were regenerated.
   **Retest:** real and restarted feeds expose non-empty safe fallback reasons plus model/freshness/novelty contributions.

4. **Test:** trigger refill through the public workflow and wait for its supervised outcome.
   **Failure:** `job_health()` returned Composition lifecycle health, whose job list is always empty; the refresh workflow also discarded `maximum_items`.
   **Root cause:** Facade was wired to the wrong health source and `_RefreshSupervisor` ignored the bounded caller input.
   **Fix:** Facade reads `RuntimeSupervisor.health()`; manual replenishment wraps the registered job with the requested bound, while scheduled runs keep the configured target.
   **Retest:** live refresh reports `RUN`, completes with `SUCCESS`, and uses the bounded discovery limit.

5. **Test:** repeat refill on the accumulated database.
   **Failure:** the first probe selected 2 of 20 candidates while 18 remained `EVALUATED`, masking a repeat-run defect. A later refresh rediscovered the same deterministic IDs; `add_candidate()` returned false, but the pipeline still attempted `DISCOVERED → NORMALIZED`, so the job errored or ran into its 55-second timeout.
   **Root cause:** candidate insertion idempotency was ignored.
   **Fix:** process only newly inserted candidates; an all-known batch completes as an observable `ReplenishmentResult(discovered=N, added=0, selected=0)` no-op. No state is retransited. The 55-second timeout may still be tight for slow live providers at the 20-item ceiling; L6 should observe it under Docker, but L4 does not tune policy.
   **Retest:** regression tests pin the all-known no-op. The real accumulated run completed successfully and added a new ranked pair without duplicates.

6. **Test:** assert ranking/diversity/profile invariants on live output.
   **Result:** two L4 tests passed. The latest run has contiguous ranks, non-increasing scores, score equals named contribution sum, non-empty reasons, no duplicate candidate IDs, creator quota ≤1, Bilibili provider quota ≤2, durable profile query topics, and feed persistence across graph restart. Profile influence is intentionally narrow and honest: it shapes provider query/topic; the current 0.65 baseline scorer is not personalized.

7. **Independent review:** repeat L4 from the accumulated profile.
   **Failure:** the single natural-language statement sometimes produced style-only preferences; historical local attempts were roughly split between style and content. The test also returned `interests[0]` and then asserted that same value was present, so its influence check was tautological and could point at an older claim outside the current run's evidence.
   **Root cause:** the real model classification is nondeterministic, and the planner intentionally consumes only the first five interests.
   **Fix:** retry at most three run-unique, explicitly content-scoped statements. Each attempt matches claims by its own evidence ID and accepts only a `CONTENT` claim; exhaustion fails loudly with the derived dimension summary. After success, older content-preference claims are removed through the real override workflow so the run's evidence-backed claim is provably inside `interests[:5]`. The candidate-topic assertion uses that exact claim value. Composition Assistant also reuses `DEFAULT_PROFILE_ID` instead of two remaining magic strings.
   **Retest:** three consecutive `./scripts/e2e.py l4` executions passed (2 tests each), followed by green L0–L3 regressions. Ruff format check was rerun after the reviewer caught an unformatted test block.

### Reproduction

```bash
./scripts/e2e.py l4
cat data-e2e/reports/l4.json
```

The real run reported two passed tests. No provider body, title, profile text, account identity, cookie, or key is recorded in reports or this log.

## L5 — Live Application workflows over `/v1`

### Architecture trace and scope decisions

1. The public feed returned `RecommendationFeedItem` without `shown_id` and never called the existing shown-history transition, while `/v1/feedback` required a caller-supplied `shown_id`. The feedback repository accepted arbitrary IDs and never moved shown → interacted. A real client therefore could not submit valid feedback or exercise the documented candidate state machine. The approved fix belongs in the Application feed-delivery/feedback workflows; generating an arbitrary ID in the E2E test was rejected as a fake test.
2. Understanding is scheduled every 60 seconds, not synchronously triggered by feedback. Ordinary feedback evidence contains provider/content identity only, and the bounded profile API exposes preference summaries but not evidence IDs. L5 therefore submits real feedback and a separate, explicit content-scoped preference statement referencing the same delivered item, waits on the supervised `understanding.analysis` job through health, and proves linkage from the accepted observation receipt plus a run-unique value in `/v1/profiles/default`. No evidence-inspection API was invented.

### Trace

1. **Test:** request the feed as an HTTP client, then submit feedback with the returned delivery identity.
   **Failure:** feed items had no delivery identity; arbitrary `shown_id` values were accepted and candidate state remained selected.
   **Root cause:** `GetRecommendations` was modeled as a passive repository read even though the recommendation aggregate already had selected → shown → interacted states and a shown table. The feedback unit of work did not validate its delivery reference.
   **Fix:** `GetRecommendations` now invokes atomic feed delivery. Selected items receive deterministic shown records and transition to shown before return; repeated reads reuse the same stable ID. Feedback validates the shown row and matching `ContentRef`, rejects unknown IDs with typed `not_found`, transitions shown → interacted, and returns idempotent duplicate success for an already committed feedback ID.
   **Retest:** hermetic Application/repository/API tests passed; the real HTTP loop received `shown_id`, committed liked feedback, replayed it as `inserted=false`, and rejected an unknown ID with JSON `not_found` rather than a 500/HTML response.

2. **Test:** poll supervised understanding completion after the HTTP observation write.
   **Failure:** the first real run hit the host's 120 requests/minute limiter while polling every 250ms and received typed HTTP 429.
   **Root cause:** the test-side poll interval ignored the real host security policy; product behavior was correct.
   **Fix:** poll once per second, still bounded and without a fixed long sleep.
   **Retest:** the 60-second understanding tick completed successfully, real Kimi derived the run-unique content preference, and `/v1/profiles/default` exposed it.

3. **Test:** restart the live server over the same isolated data directory.
   **Result:** recommendation IDs and their stable shown IDs remained available, the bounded profile projection was byte-equivalent, and the feedback observation had already survived through the accepted HTTP receipt/idempotent replay. Server startup and shutdown were managed by the E2E fixture and logs stayed under gitignored `data-e2e/`.

4. **API surface:** loopback reads require no bearer token by design; every mutation still requires matching device/CSRF headers. Invalid query input returns the documented typed `validation` JSON envelope. OpenAPI snapshot and the generated TypeScript client now include `RecommendationFeedItem.shown_id`.

5. **Independent review:** repeat the model-dependent profile assertion nine times.
   **Failure:** two runs derived a valid content preference but paraphrased away the run token, so the single-shot assertion failed. Successful and failed attempts also permanently accumulated claims on the shared `default` profile; it reached 28 preferences. `DialogueProfile.preference_summary` allows at most 30 entries, while `dialogue_projection()` bounds characters but not count, so the 31st preference would make `/v1/profiles/default` raise a validation error and return 500.
   **Root cause:** L5 had not reused L4's bounded real-model retry/cleanup discipline, and the projection has a latent count-overflow defect.
   **Fix:** make at most three content-scoped attempts with distinct markers, match only newly derived summaries, fail loudly with derived-summary diagnostics on exhaustion, and remove failed/successful L5 claims through the real `/v1/profiles/edit` override workflow. The same workflow performs a one-time cleanup of prior `HTTP 工作流测试` claims before testing.
   **Retest:** three consecutive L5 executions passed without growing the profile. The general product defect remains: any non-E2E profile with more than 30 active preference claims can make `dialogue_projection()` fail; fixing that projection is a follow-up, not an L5 test change.

### Reproduction

```bash
./scripts/e2e.py l5
cat data-e2e/reports/l5.json
```

The real run reported two passed tests. The server fixture invokes `openbiliclaw serve --config data-e2e/config.e2e.toml --data-dir data-e2e`, waits on `/v1/runtime/health`, and terminates it cleanly. No key, cookie, provider body, title, profile text, or account identity is written to the report or this log.

## L6 — Docker deployment

### Trace

1. **Preflight:** Docker Engine 29.7.2 and Compose 5.4.0 were available through the host's Docker group (`sg docker -c 'docker ...'`). The E2E uses project `openbiliclaw-e2e-l6`; setup removes only that project and teardown always executes `down -v --remove-orphans`.

2. **Test:** build a minimal Infinity sidecar with `infinity_emb[torch,server]==0.0.77`.
   **Failure:** first pip solve rejected the L0 Typer workaround because Infinity declares `typer<0.13`; after installing Infinity first and upgrading Typer without dependency resolution, startup failed with `NameError: BetterTransformerManager` even though Optimum was not installed.
   **Root cause:** Infinity's optional-dependency guard incorrectly enters the BetterTransformer path without a usable Optimum module. The `[torch,server]` image intentionally avoids Optimum.
   **Fix:** build only the Torch/server extras, upgrade the CLI Typer separately, and start with `--no-bettertransformer`. No model-serving package enters the application image.
   **Retest:** the sidecar downloaded `BAAI/bge-small-zh-v1.5`, reached `/health`, and Compose started the dependent backend.

3. **Test:** start the backend on the container-required `0.0.0.0` binding.
   **Failure:** every backend restart crashed because `HostSecurityPolicy` correctly requires a bearer for non-loopback binding, while configuration/composition had no bearer reference path.
   **Root cause:** the reviewed security boundary existed but the supported Docker composition could not satisfy it.
   **Fix (TDD):** `[host].bearer_secret_ref` and `OPENBILICLAW_API_BEARER_SECRET_REF` accept an opaque vault reference; composition resolves it inside the credential boundary. First-start seeding generates a cryptographically random bearer and stores only its reference in runtime config. Docker health resolves it internally without logging it.
   **Retest:** unauthenticated SPA/API requests returned 401, authenticated health and SPA requests returned 200, and in-container `openbiliclaw check` passed.

4. **Test:** submit the provider-owned Bilibili connection form through `/v1/sources/connect`.
   **Failure:** the host converted a generic `credential` string into `{"credential": value}`, but Bilibili's advertised form field is `cookie`; the manual verifier could never receive a valid submission over HTTP.
   **Root cause:** the host invented a generic secret field instead of preserving the provider form contract. L1b called the Application facade directly, so this host-only defect remained hidden.
   **Fix (TDD):** the request accepts a secret `submission` mapping and passes provider field IDs unchanged. OpenAPI and the generated TypeScript client were regenerated.
   **Retest:** host regression coverage pins the mapping. The Docker restart contract remains client resubmission because process-local access handles are not reconstructed from the vault.

5. **Test:** containerized anonymous connect → bounded refill → delivered feed → feedback.
   **Result:** the 20-item refill completed successfully within the 55-second supervised production policy on this host; feed items had stable shown IDs and feedback inserted once. A backend restart retained remaining feed IDs, the profile projection, and feedback idempotency. The interacted item correctly disappeared from the feed, so persistence compares only the still-selected IDs.

6. **Restart probe:** after backend restart, source status is `disconnected`; resubmitting the anonymous connection succeeds. Authenticated credentials are opaque in the vault but no provider/account mapping exists, so clients must resubmit `submission.cookie`. Durable automatic reconnection remains deliberately unimplemented rather than guessed in Docker composition.

7. **Presentation finding:** bearer middleware protects the SPA fallback as well as `/v1`. The extension can store a bearer, but the current Vue Web app cannot enroll one; direct Docker Web use therefore receives 401. This is recorded for L7 instead of weakening non-loopback authentication.

### Reproduction

```bash
export OPENBILICLAW_MODEL_KEY_FILE="$HOME/.config/openbiliclaw/model_api_key"
./scripts/e2e.py l6
cat data-e2e/reports/l6.json
```

The final real run reported one passed Docker E2E test. No key, bearer, cookie, provider response body, title, profile text, or account identity appears in the report or this log.

8. **Independent review — build-context secret boundary:** image-layer inspection was clean, but `.dockerignore` omitted `data-e2e/` and `model_api_key.txt`; the source Compose default also pointed at the gitignored L0 key path. Docker therefore sent the local E2E vault/key/cookie/database directory to the build daemon even though no Dockerfile instruction copied it into a layer. Fixed both supported secret locations in `.dockerignore`, aligned both Compose defaults to `./model_api_key.txt`, and pinned the exclusion with a static regression test.

## L7 — Agent-driven Web UI

### Trace

1. **Test:** open the live Vue SPA in Chrome and load Recommendations.
   **Failure:** every API call failed with `TypeError: Illegal invocation`.
   **Root cause:** `ApiClient` captured native `fetch` and later invoked it without its Window receiver, which Chrome 151 rejects.
   **Fix:** bind the injected fetcher when constructing the client and pin the receiver requirement in a unit test.
   **Retest:** Recommendations rendered real feed cards and Profile rendered the derived projection.

2. **Test:** connect Bilibili through the Web source form.
   **Failure:** the browser's JSON `permissions` array always returned 422 because the strict transport schema accepted only an already-constructed Python `frozenset[Permission]`.
   **Fix:** validate JSON collections and convert each string member at the transport boundary.
   **Retest:** anonymous connection succeeded and the Search view returned real provider results.

3. **Test:** send an Assistant message.
   **Failure:** the hardcoded `conv_web...` ID did not satisfy `conv_[0-9a-f]{32}` and returned 422. After replacing it, the model request failed and escaped as an untyped 500.
   **Initial fix:** generate and persist a valid browser conversation ID and translate Assistant `AIRuntimeError` to safe Application `UNAVAILABLE`.
   **Correction and compatibility fix:** Kimi does support tool calls. Capturing the application request showed PydanticAI uses `tool_choice = "required"` for the Assistant output tool. Probe evidence: automatic tool choice succeeded; required choice with default thinking returned HTTP 400; required choice with `thinking = {type = "disabled"}` returned HTTP 200 and a proper tool call. A reviewed `model.options.disable_thinking` toggle now adds only that OpenAI `extra_body`; false preserves the old request exactly, and non-OpenAI constructors are unchanged.
   **Retest/debug/fix:** disabling thinking removed the provider 400 and produced a real `assistant_output` tool call, but its `kind` was `text` because `_validate_output` annotated the discriminator as unconstrained `str`; the generated tool schema therefore advertised any string even though the validator accepts only four literals. Narrowing it to the existing four `Literal` values fixed the schema at the source. A real Assistant turn through the production application graph then returned a typed `message` with non-empty text; L3 preference processing remained green.

4. **Test:** click Like/Dismiss on a recommendation card.
   **Failure:** both buttons were presentation-only; no event, Web API operation, store action, or `shown_id` mapping existed, so the L5 feedback workflow was unreachable.
   **Fix (TDD):** shared cards emit typed `like`/`dismiss` events; the Web card view retains `shown_id`; the recommendation store calls generated `POST /v1/feedback` with `idempotency_key`, `shown_id`, matching `content_ref`, and exact `liked`/`dismissed` kinds. Updates are server-authoritative, and 404/409 delivery expiry is visibly actionable.
   **Retest:** api-client serialization, presentation event propagation, store success/expiry, and RecommendationsView wiring tests pass.

5. **Test:** rebuild the Vite artifact, then navigate in the existing Chrome session.
   **Failure:** the host served SPA HTML without cache policy, so Chrome heuristically reused HTML that referenced an old bundle hash.
   **Fix (TDD):** index/route fallback responses use `Cache-Control: no-cache`; fingerprinted `/assets/*` responses use `public, max-age=31536000, immutable`.
   **Retest:** host-level static serving coverage pins both response classes.

No secret, provider response body, title, profile text, account identity, cookie, key, or bearer value is recorded in this trace.

## OpenAI-compatible Assistant matrix validation

1. **Probe:** call DeepSeek `deepseek-chat` at `https://api.deepseek.com` with ordinary chat and with a forced `tool_choice = "required"`, without `disable_thinking`.
   **Result:** both returned HTTP 200; the forced request returned a proper tool call. This establishes a native default-path control beside the Kimi thinking-disabled case.
2. **Test:** run the same production Application Assistant turn with tolerant invariants against both generated E2E profiles.
   **Result:** Kimi `kimi-for-coding` with `disable_thinking = true` and DeepSeek `deepseek-chat` with the option absent each returned a typed `AssistantMessage` with non-empty text. The toggle is therefore scoped to thinking-forced OpenAI-compatible endpoints rather than required by the shared OpenAI constructor path.
3. **Provider correction:** rerun L3 after changing the DeepSeek profile from generic `openai` plus endpoint override to PydanticAI's native `deepseek` provider.
   **Result:** 4 passed. The same live Assistant matrix remains green while DeepSeek now inherits PydanticAI's vendor model profile instead of discarding it.

No credential or provider response body is recorded in this matrix trace.

## L7 closing sweep

- **Full-layer closing sweep (l0-l6) caught one L7-induced assertion drift:** the final all-layer run failed L4 at the rank-contiguity invariant `(2,) != (1,)`. Root cause is correct product behavior, not a product bug: the live L7 Like click transitioned the seed's rank-1 candidate shown→interacted, and later feeds legitimately exclude interacted candidates, leaving the seed's visible ranks a proper subset of 1..n. The L4 invariant had assumed a no-interaction database. Fix: the assertion now requires strictly ascending, duplicate-free visible ranks within a seed (documented inline), instead of a full contiguous prefix. Verified with two consecutive green l4 runs; all other layers green; unit suite 743 passed.

## Catalog integration closing sweep

- **Post-catalog full sweep (l0-l6) found three drift points, all fixed:**
  1. `config.docker.toml` still pinned `provider="openai"` + `kimi-for-coding`; with catalog-as-truth the model is not listed under that id and the Docker backend failed closed at startup. The template now uses the catalog id `kimi-for-coding` (endpoint/protocol from the catalog); the container fetches models.dev on first start and caches it in the runtime volume. l6 re-verified live (build/boot/flow/persistence green).
  2. l0's chat assertion expected provider attribution `"openai"`; catalog resolution attributes the catalog id instead. The test now asserts attribution equals the configured provider id (no hardcoding).
  3. l3's profile-derivation test flaked once with an upstream Kimi account budget error; green on rerun. The Kimi key is a limited test key by design — transient budget_exhausted is expected environmental noise, and reruns are the documented remedy.
- docs/docker-deployment.md now documents catalog-driven model config and the catalog-bypassing embedding sidecar.
