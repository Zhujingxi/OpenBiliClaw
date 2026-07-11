# Private Managed Ollama Self-Heal — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan
> task-by-task; fall back to plain sequential execution if the skill is unavailable).
> **Spec:** [`2026-07-10-private-ollama-selfheal-spec.md`](./2026-07-10-private-ollama-selfheal-spec.md)
> **Status:** r1 — 2026-07-10.
> **Execution order:** Task 0 → 1 → 2 → 3 → 4 (single wave; 1 and 2 both depend on 0 and
> are mutually independent).
> **Tech:** Python 3.11+, pytest (`asyncio_mode=auto`), Ruff, MyPy strict, 100-char lines.
> Interpreter is `.venv/bin/python` (plain `python`/`python3` has no deps). Per task:
> `.venv/bin/python -m pytest <touched tests> -q`, then `.venv/bin/python -m ruff check`
> / `ruff format --check` on touched files, then `.venv/bin/python -m mypy
> src/openbiliclaw/`. The worktree is SHARED with concurrent sessions: re-read files
> before editing (line numbers are hints), commit per task with explicit pathspecs
> (`git commit -m "..." -- <files>`), never `git add -A` / `checkout` / `stash` / `clean`.

**Invariants that MUST hold (from Spec — re-read before each task):**

- Never signal/kill/restart a daemon we did not start; adoption (private port only) is
  record-only.
- Restart always reuses the recorded `(host, models_dir)`; a private daemon never comes
  back on 11434 or with the wrong models dir.
- Watchdog backoff: 5s doubling to 300s cap, give up after 5 consecutive failures until
  manual repair success or process restart; healthy probe resets.
- Private daemon env is hard-set (`OLLAMA_KEEP_ALIVE=24h`, `OLLAMA_HOST`,
  `OLLAMA_MODELS`); default-daemon path keeps `setdefault`.
- Both `may_manage` sites in `api/app.py` use the same new predicate; zero remaining
  `_is_default_ollama_endpoint`-only gates (grep-verified).
- Non-managed setups (remote endpoints, `manage_ollama=False`) behave exactly as today;
  the full existing suite passes untouched.

---

### Task 0: Supervisor managed-daemon spec + restart routing + env hardening

**Files:** Modify `src/openbiliclaw/runtime/ollama_supervisor.py`;
Test `tests/test_ollama_supervisor.py`

**Interfaces:** Consumes: `subprocess.Popen`, `httpx` probe, `embedding_progress`.
Produces: `_ManagedDaemon` record, `is_managed_endpoint()`, spec-aware
`restart_managed_ollama()`, `ensure_managed_ollama(endpoint)`.

**Steps:**

- [ ] Read the current `tests/test_ollama_supervisor.py` to inventory `_managed_proc`
  usages; plan test updates rather than a compatibility shim unless a shim is trivially
  needed elsewhere (`grep -rn '_managed_proc' src/ tests/`).
- [ ] Write focused failing tests: (a) `_ollama_start_serve_background` records
  `(proc, http://localhost:11434, managed_models_dir())`; (b) `start_managed_ollama_at`
  records `(proc, base_url, abspath(models_dir))` and its adoption early-return records
  `(None, base_url, abspath(models_dir))`; (c) `is_managed_endpoint` normalizes
  host/scheme (`localhost` ≡ `127.0.0.1`, `/v1`-stripped input handled by caller) and is
  False with no record; (d) `restart_managed_ollama` with a private record relaunches via
  the private path (fake Popen captures env: `OLLAMA_HOST`, `OLLAMA_MODELS`,
  `OLLAMA_KEEP_ALIVE` hard-set) and never touches 11434; (e) with a default record keeps
  today's behavior; (f) external refusal probes the recorded endpoint and returns
  `external_ollama` / `adopted_alive` without killing anything; (g)
  `restart_managed_ollama_with_models_dir` refuses `private_daemon` on a private record;
  (h) `stop_managed_ollama` clears the record; (i) default path `OLLAMA_KEEP_ALIVE`
  still `setdefault`.
- [ ] Run `.venv/bin/python -m pytest tests/test_ollama_supervisor.py -q` — confirm the
  new tests FAIL for the intended missing behavior.
- [ ] Implement per spec Phase 0 (dataclass record, predicate, routing, refusal reasons,
  env hard-set, record clearing). Keep bool/tuple return contracts; no new exceptions
  escape.
- [ ] Rerun the file — all PASS, no warnings; update any pre-existing tests that asserted
  the old `_managed_proc` shape.
- [ ] Ruff check + format-check on both files; `.venv/bin/python -m mypy src/openbiliclaw/`.

**Acceptance:**

- Numeric gate: `pytest tests/test_ollama_supervisor.py -q` exits 0 with ≥ 9 new
  assertions covering (a)-(i); failure means the routing contract is not fully pinned.
- Reproduce with `.venv/bin/python -m pytest tests/test_ollama_supervisor.py -q`.

### Task 1: Repair-gate parity in the embedding-repair endpoint

**Files:** Modify `src/openbiliclaw/api/app.py`,
`src/openbiliclaw/runtime/ollama_supervisor.py` (exports only);
Test `tests/test_api_app.py`; possibly `config.example.toml` (see last step)

**Interfaces:** Consumes: Task 0's `is_managed_endpoint` / `ensure_managed_ollama` /
spec-aware restart. Produces: `may_manage_ollama_endpoint()` used by both repair gates.

**Steps:**

- [ ] Write focused failing tests in `tests/test_api_app.py` (follow the existing
  embedding-repair test fixtures/stubs there): (a) with a recorded private daemon
  (monkeypatched supervisor record) and embedding base_url `http://127.0.0.1:11435/v1`,
  a `DIAG_NOT_RUNNING` repair call invokes the private-start routing (stub records the
  call) and does NOT return 409; (b) same setup on the `DIAG_ERROR` path invokes
  spec-aware restart; (c) with no record and a non-default endpoint, repair still 409s
  (invariant: no behavior change for non-managed setups); (d) `manage_ollama=False`
  still 409s even with a record.
- [ ] Confirm FAIL, then implement: add `may_manage_ollama_endpoint` +
  `ensure_managed_ollama` to the supervisor exports; swap both gates
  (`api/app.py` not_running and provider_error branches) and the not_running action per
  spec Phase 1.
- [ ] Grep gate: `grep -n '_is_default_ollama_endpoint' src/openbiliclaw/api/app.py`
  returns 0 lines (the predicate lives only in the supervisor).
- [ ] Check the desktop template: `grep -n 'manage_ollama' config.example.toml
  packaging/` — if the with-embedding flow relies on a default-False `manage_ollama`,
  set it appropriately in the template/entry flow and cover with a test; record the
  finding either way in the PR description.
- [ ] Rerun touched tests → PASS; ruff + mypy.

**Acceptance:**

- Numeric gate: the four new endpoint tests pass; `grep -c '_is_default_ollama_endpoint'
  src/openbiliclaw/api/app.py` == 0. Failure means a gate site was missed (invariant 5).
- Reproduce with `.venv/bin/python -m pytest tests/test_api_app.py -q -k repair`.

### Task 2: Watchdog with backoff

**Files:** Modify `src/openbiliclaw/runtime/ollama_supervisor.py`;
Test `tests/test_ollama_supervisor.py`

**Interfaces:** Consumes: `_ManagedDaemon` record, injectable `_watchdog_sleep` /
`_watchdog_probe` seams. Produces: `start_ollama_watchdog()`, `reset_watchdog_backoff()`.

**Steps:**

- [ ] Write focused failing tests driving the watchdog loop synchronously via the
  injectable seams (no real threads/sleeps in assertions; if a thread is unavoidable,
  join with a hard 5s timeout): (a) healthy probe → no restart, failure counter stays 0;
  (b) owned proc exited + probe dead → restart routing invoked once with recorded params;
  (c) restart failure → backoff sequence 5, 10, 20, 40, 80 observed via captured sleep
  calls, 5th consecutive failure stops attempts and reports phase `down`; (d) healthy
  probe after failures resets the counter; (e) `start_ollama_watchdog` is idempotent
  (one thread); (f) `reset_watchdog_backoff()` re-enables attempts after give-up; (g) no
  record → loop idles without probing.
- [ ] Confirm FAIL, then implement per spec Phase 2: daemon thread, module lock shared
  with start/stop/restart mutations, "restart in progress" flag, arming calls inside both
  successful-start paths, `reset_watchdog_backoff()` invoked from the repair endpoint's
  success path (small `api/app.py` touch is allowed here if cleaner — keep it one line).
- [ ] Rerun → PASS with no hanging threads (pytest exits promptly); ruff + mypy.

**Acceptance:**

- Numeric gate: tests (a)-(g) pass; total `tests/test_ollama_supervisor.py` runtime stays
  < 5s (proves no real sleeps leaked into the loop under test).
- Reproduce with `.venv/bin/python -m pytest tests/test_ollama_supervisor.py -q`.

### Task 3: entry.py adoption + desktop-flow verification

**Files:** Test `tests/test_bundled_embedding_entry.py` (and
`src/openbiliclaw/runtime/ollama_supervisor.py` / `packaging/entry.py` only if a gap
surfaces)

**Interfaces:** Consumes: Task 0's adoption recording + Task 2's arming inside
`start_managed_ollama_at`. Produces: test-pinned desktop boot contract.

**Steps:**

- [ ] Write failing (or newly-passing — verify they fail against pre-Task-0 code via
  `git stash`-free reasoning, i.e. assert on the new record API) tests: (a) the seeding
  success path leaves a managed record for `127.0.0.1:11435` and an armed watchdog;
  (b) the adoption branch (port already responding at boot, no proc) records
  `proc=None` and `is_managed_endpoint("http://127.0.0.1:11435")` is True; (c) after an
  adopted daemon's probe flips to dead, the watchdog launches a fresh private daemon with
  the recorded models dir (stubbed Popen).
- [ ] Implement any wiring gaps the tests expose (expected: none beyond Tasks 0/2).
- [ ] Rerun → PASS; ruff + mypy on touched files.

**Acceptance:**

- Numeric gate: the three scenario tests pass; zero changes needed outside tests is the
  expected outcome — if code changes were needed, list them in the PR as spec deviations.
- Reproduce with `.venv/bin/python -m pytest tests/test_bundled_embedding_entry.py -q`.

### Task 4: Docs sync

**Files:** Modify `docs/modules/llm.md`, `docs/changelog.md`; `config.example.toml` only
if Task 1's template finding requires it

**Steps:**

- [ ] `docs/modules/llm.md`: managed-Ollama lifecycle section — recorded daemon spec,
  watchdog (interval/backoff/give-up), repair coverage table now including the private
  11435 daemon, `OLLAMA_KEEP_ALIVE` ownership rules.
- [ ] `docs/changelog.md`: one bullet under the current version block, e.g. `fix:
  with-embedding 私有 Ollama(11435) 纳入自愈/一键修复 + 托管 daemon watchdog 崩溃自动拉起
  （含退避与放弃阈值）`.
- [ ] Full gate: `.venv/bin/python -m pytest -q` (entire suite), ruff on all touched
  files, `.venv/bin/python -m mypy src/openbiliclaw/`. Pre-existing failures clearly
  caused by concurrent-session churn in unrelated files: report, don't chase.

**Acceptance:**

- Numeric gate: full suite exit 0 (or exactly the documented pre-existing unrelated
  failures); changelog contains exactly one new bullet for this change.
- Reproduce with `.venv/bin/python -m pytest -q`.

---

## Verification after merge

1. Manual kill-restart drill (macOS dev box, owner: session operator, ~10 min): start the
   desktop entry (or simulate: `start_managed_ollama_at` via a REPL with a scratch models
   dir), `kill -9` the `ollama serve` on 11435, watch the watchdog restart it within ~60s
   (`curl -s 127.0.0.1:11435/api/version`); then kill it again and immediately call
   `POST /api/embedding/repair` — expect a restart, not 409.
2. Ship in the next desktop release; watch GitHub issues / user reports for "embedding
   挂掉" recurrence over two weeks. Rollback trigger: watchdog flap reports (repeated
   restart ERROR logs) from users — revert the arming call sites, keeping the repair-gate
   fix.

## Explicitly out of scope

- Killing alive-but-unhealthy orphan processes by port.
- Proxy-env sanitation for `ollama pull`; disk diagnostics for the private models dir.
- Seed-failure UI surfacing; changes to `llm/ollama_provider.py` retry semantics.
