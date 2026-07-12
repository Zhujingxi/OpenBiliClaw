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

## Review-fix follow-up

Review found five boundary gaps. A second RED run was captured in
`/tmp/task8-review-red.txt`: 14 failures proved that the public expression drain
swallowed typed failures, duplicate evaluation IDs used last-wins, candidate
resume matching was too broad, non-rate-limit transients ignored `retry_after`,
and arbitrary local `OSError` values were classified as network failures. Two
additional coordinator RED failures proved cumulative progress was lost and a
stale retry deadline blocked config resume.

The follow-up now propagates transient/auth/no-provider failures after sibling
batch completion, carries cumulative successful writes and provider retry-after,
invalidates duplicate keyed evaluation rows before retry, restricts resume to
exact `startup` or `config_*` / `manual_*`, applies retry-after to every transient
kind, and recognizes only connection-specific `OSError` errno/message shapes.

## Cumulative same-branch follow-up

An additional review isolated the nested shape “A/B persisted, C/D missing-only
retry fails”. The public drain regression already proved transient progress was
two, but the recursive implementation mutated the downstream exception and did
not attach ancestor progress to auth/no-provider exceptions. A new RED auth case
recorded `completed=0` after two successful writes. The recursion now creates a
fresh transient error with `ancestor_total + downstream.completed` (preserving
kind and retry-after), avoiding mutation/double counting, and attaches the same
cumulative count to auth/no-provider failures. Public drain plus coordinator
coverage asserts A/B persisted, C/D pending, propagated `completed=2`, and
`expression_last_completed=2`.
