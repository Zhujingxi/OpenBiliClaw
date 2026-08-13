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
