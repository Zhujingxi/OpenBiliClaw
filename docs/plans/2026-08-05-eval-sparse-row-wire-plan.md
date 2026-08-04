# Evaluator Sparse Payload and Row Wire Implementation Plan

**Spec:** `docs/plans/2026-08-05-eval-sparse-row-wire-spec.md`

## Task 1 — Freeze contracts and measurements

**Owner:** root

- [x] Audit constructor fields, prompt rules, result binding and downstream classification consumers.
- [x] Measure production, compact, sparse-JSON and escaped-row character proxies on the frozen 100 rows.
- [x] Define the canonical sparse schema, local-ID safety contract, row escaping and independent A/B arms.
- [ ] Review implementation diffs against this spec before any provider call.

## Task 2 — Canonical sparse payload and local IDs

**Owner:** `luna_max_cache`

- [ ] Implement one canonical sparse batch builder shared by both transports.
- [ ] Add request-local ID mapping and strict result-member resolution without multi-member positional binding.
- [ ] Preserve production defaults; expose treatment only through an instance/replay seam.
- [ ] Cover duplicate aliases, empty omission, homogeneous defaults, mixed batches and cache-key isolation.
- [ ] Run focused tests, Ruff and `git diff --check`.

## Task 3 — Row-wire-v1 codec and multimodal anchors

**Owner:** `luna_max_audit`

- [ ] Implement deterministic row encoding and strict decoding of the canonical sparse payload.
- [ ] Cover tabs, CR/LF, backslashes, Unicode, empty cells, lists, malformed escapes and row-width failures.
- [ ] Keep image bytes/order unchanged while using request-local text/image anchors.
- [ ] Prove production prompt rendering remains byte-identical when the experiment seam is off.
- [ ] Run focused tests, Ruff and `git diff --check`.

## Task 4 — Independent replay arms and artifact gates

**Owner:** `luna_max_replay`

- [ ] Add `--arm-b sparse-json` with production A and sparse JSON B.
- [ ] Add `--arm-b row-wire-v1` with sparse JSON A and row-wire B.
- [ ] Audit decoded canonical equality, local-ID coverage, image pairing and privacy-safe prompt usage.
- [ ] Add locked savings gates and retain score/admission/classification/repair/usage gates.
- [ ] Add focused replay tests without changing production behavior.

## Task 5 — Root integration and comprehensive verification

**Owner:** root

- [ ] Review all changes for schema drift, unsafe fallback, cache collisions and hidden content loss.
- [ ] Resolve integration issues and update mandatory discovery/changelog/cache-convention documentation.
- [ ] Run focused prompt/discovery/replay/multimodal tests.
- [ ] Run Ruff format/check, MyPy, full Pytest, coverage sanity and `git diff --check`.
- [ ] Run deterministic candidate-pipeline E2E including warm-cache and member-repair paths.
- [ ] Commit a clean experiment implementation before any real provider call.

## Task 6 — Real replays and independent audit

**Owner:** root

- [ ] Run `sparse-json` on 100 candidates × 3 repeats and independently recompute all gates.
- [ ] If and only if sparse JSON passes, run `row-wire-v1` on the same 100 × 3 design.
- [ ] Scan artifacts against source rows for privacy leakage and verify usage completeness.
- [ ] Record exact commands, commits, artifact hashes, runtimes, savings, quality deltas and incidents.

## Task 7 — Production decision

**Owner:** root

- [ ] Land row-wire-v1 only if both independent experiments pass every locked gate.
- [ ] Otherwise keep production bytes unchanged and record the failed arm without threshold tuning.
- [ ] Rerun applicable full and end-to-end tests after the landing/rejection decision.
