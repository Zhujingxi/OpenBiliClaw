# Task 8 — bounded provider/payload retry report

## Scope

Implemented Task 8 only: shared transient classification, provider-vs-payload
separation, bounded missing-member retries for expression copy and candidate
evaluation, token-safe unresolved evaluation reset, and coordinator pause/backoff
semantics. Soul prompts, token accounting, and cost behavior were not changed.

## RED evidence

Command (repository virtual environment):

```text
.venv/bin/pytest tests/test_llm_service.py -q -k 'classify_llm_failure_kind'
```

Observed before production edits: 3 failed, 6 passed. The failures were exactly:

- `ConnectionError("connection reset by peer")`: returned `None`, expected `connection`
- `OSError("network is unreachable")`: returned `None`, expected `connection`
- `LLMProviderExecutionError("upstream returned HTTP 503")`: returned `None`, expected `server_error`

The original terminal output was also captured at `/tmp/task8-red.txt` during the
implementation session. An earlier bare `pytest` invocation failed because pytest
was not on PATH; it was not treated as RED evidence.

## Implemented behavior

- Auth/no-provider/rate-limit precedence remains ahead of coarse connection and
  server-error markers.
- 429, timeout, connection, and 5xx provider failures bypass malformed split
  retry and single fallback.
- Keyed successful siblings are persisted before missing/invalid/duplicate
  members are reported.
- Each top-level malformed batch owns one mutable six-extra-request budget and
  maximum split depth three. An eight-item permanently malformed expression
  batch makes at most seven calls total.
- Permanently malformed singleton expression copy remains pending and never
  calls `recommendation.expression`.
- Evaluation retries only missing keyed members. Unresolved candidates use the
  transport marker `evaluation_response_missing`, are excluded from low-score
  persistence, and are reset by matching claim token without attempt increment.
- Expression and evaluation coordinators use transient backoff
  15/30/60/120/300 seconds; auth/no-provider pauses until startup/config/manual
  notification. Provider `retry_after` wins when longer.

## Verification

See the final handoff for fresh command results. Documentation checklist applied:
LLM, discovery, recommendation, runtime module docs and current changelog updated;
no architecture, CLI, config, dependency, installer, or positioning boundary changed.
