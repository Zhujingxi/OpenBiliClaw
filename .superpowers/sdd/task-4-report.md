# Task 4 report — runtime-wide LLM concurrency gate

## Result

Implemented one runtime-owned total/background LLM gate for API, OpenClaw and each CLI composition. Total defaults to 4, background derives as `max(1, total - 1)`, explicit positive totals remain unchanged, and candidate evaluation remains configured at 3.

## RED evidence

Initial new concurrency suite:

```text
PYTHONPATH=src .../.venv/bin/pytest tests/test_llm_concurrency.py -q
ModuleNotFoundError: No module named 'openbiliclaw.llm.concurrency'
```

Composition identity test:

```text
tests/test_soul_dialogue.py::test_dialogue_reuses_soul_engine_service_identity
RuntimeError: Dialogue service is not configured.
```

The test suite was written first for total/background capacity, total=1 degradation, queued total/background cancellation, exact caller classification, warning-once unknown callers, bypass-never-total, shared service identity, composition derivation, status fields and UI defaults.

## GREEN evidence

- Required backend matrix plus refresh regression: `1005 passed`.
- Concurrency/service initial focused matrix: `81 passed`.
- Extension: `npm test` — `711 passed`; `npm run typecheck` and `npm run build` succeeded.
- Ruff: `All checks passed!`.
- MyPy: `Success: no issues found in 188 source files`.
- `git diff --check`: clean.
- Repository-wide `pytest -q` was attempted with a 120-second ceiling; it reached 24% with no failures before timeout. The complete affected matrix above passed.

## Boundary and inventory review

- Inventoried all production `LLMService(...)` constructors: Soul, dialogue legacy fallback, API runtime, OpenClaw, and five CLI sites.
- Normal, structured, multimodal, dialogue and tool calls all converge on `_provider_slot`; legacy bypass skips background admission only.
- `PrioritySemaphore` moved without behavioral changes beyond read-only capacity/active/waiting properties and remains re-exported by `llm.service`.
- API/OpenClaw main service, Soul internal service and refresh controller share the same gate object; CLI uses `_RUNTIME_COMPONENTS` identity caching.
- No Task 5 inventory reservation API/state (`update_inventory`, refill waiter reservation, zero-inventory maintenance parking) was added.
- No Soul prompt, token, pricing, usage recording or cost semantics changed.

## Self-review and concerns

- Background acquires background before total and releases in reverse; cancellation at either queue is covered and counters return to zero.
- Unknown/empty caller tags are maintenance-limited and warning-once per gate.
- With explicit total 1, background capacity is 1: safe/no deadlock, but no interactive reservation is mathematically possible; documented.
- Full-suite timeout is the only incomplete verification item; no failure was observed, and all directly affected suites plus refresh passed.
