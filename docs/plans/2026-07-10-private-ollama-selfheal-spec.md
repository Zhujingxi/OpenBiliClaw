# Private Managed Ollama Self-Heal Spec — with-embedding daemon crashes become recoverable

**Created:** 2026-07-10
**Scope:** `src/openbiliclaw/runtime/ollama_supervisor.py` (managed-daemon state, restart
routing, watchdog), `src/openbiliclaw/api/app.py` embedding-repair `may_manage` gates,
`packaging/entry.py` private-daemon boot recording, related tests, module docs + changelog.
**Out of scope:** killing an alive-but-unhealthy orphan by port (we only ever signal
processes we spawned), proxy-env sanitation for `ollama pull`, private-models-dir awareness
in `_ollama_models_disk_root` disk diagnostics, surfacing seed-failure reasons in the UI,
any change to embedding call/retry semantics in `llm/ollama_provider.py`.

## Goal

Desktop users (including the `with-embedding` variant that ships a baked bge-m3) report the
embedding service "keeps dying". Root causes are confirmed in code (see D1-D4): the private
daemon on `127.0.0.1:11435` is excluded from every self-heal path by a hardcoded
port-11434 guard, and no watchdog exists for either variant — a crashed daemon stays dead
until app restart. Target outcomes:

- A crashed managed daemon (default 11434 **or** private 11435) is restarted automatically
  by a watchdog within `2 × interval` (default interval 30s) — verified by
  `tests/test_ollama_supervisor.py` watchdog tests with a fake clock/probe.
- The one-click embedding repair endpoint manages the private daemon exactly as it manages
  the default one: `POST /api/embedding/repair` on a dead private daemon performs a restart
  instead of returning `409 not_running` — verified by new `tests/test_api_app.py` cases.
- The supervisor can always answer "how was our daemon started" — restart uses the recorded
  `(host, models_dir)`, never the wrong port/dir.

Verification commands: `.venv/bin/python -m pytest tests/test_ollama_supervisor.py
tests/test_api_app.py -q` plus the manual kill-restart procedure in "Verification after
merge".

## Design invariants (MUST hold in every phase)

1. **Never manage a daemon we did not start.** External Ollama (official app, user daemon)
   is never signalled, killed, or restarted. Managed identity = we hold the `Popen` handle
   (or an explicitly recorded adoption of the *private* port, which no external product
   uses by convention). Verified by existing external-daemon refusal tests staying green.
2. **Restart preserves launch parameters.** A managed daemon restarts with the exact
   `(host, OLLAMA_MODELS)` it was started with — a private daemon never restarts on 11434
   and never loses its private models dir. Verified by unit tests on the recorded spec.
3. **Watchdog cannot flap-loop.** Consecutive failed restart attempts back off
   exponentially (5s → capped 300s) and give up after 5 consecutive failures (phase
   `down`, log ERROR) until a manual repair or app restart resets the counter. A healthy
   probe resets the backoff. Verified by fake-clock unit tests.
4. **Private daemon env is fully owned.** The private daemon always gets
   `OLLAMA_KEEP_ALIVE=24h` and its own `OLLAMA_HOST`/`OLLAMA_MODELS` **hard-set** (not
   `setdefault`) — user environment cannot degrade it. The default-daemon path keeps
   `setdefault` (respects a user's deliberate global setting). Verified by env-inspection
   unit tests.
5. **Repair gate parity.** Both `may_manage` computations in `api/app.py` (not_running and
   provider_error paths) use the same new predicate; no call site keeps the old
   `_is_default_ollama_endpoint`-only logic. Verified by grep in the plan's acceptance and
   by endpoint tests.
6. **No behavior change for non-managed setups.** Remote/base_url-custom endpoints and
   `manage_ollama=False` configs behave exactly as today (repair returns 409, watchdog
   never starts). Existing test suite passes untouched.

## Current diagnosis

### D1. Private daemon (11435) is excluded from every self-heal path

`may_manage` in both repair branches requires `_is_default_ollama_endpoint(endpoint)`
(`api/app.py:2582`, `:2670`), which hardcodes `port == 11434`
(`runtime/ollama_supervisor.py:91-95`). The with-embedding variant rewrites the embedding
base_url to `http://127.0.0.1:11435/v1` (`packaging/entry.py:527,591-598`), so
`effective_ollama_endpoint` yields port 11435 and `may_manage` is always False: repair
returns `409 not_running` (`app.py:2584-2589`) — the "dead repair button".

### D2. All restart machinery targets 11434 only

`restart_managed_ollama` / `restart_managed_ollama_with_models_dir` call
`_ollama_start_serve_background()` (`ollama_supervisor.py:289,298`), which starts on the
default port with `managed_models_dir()`. The only function that can start the private
daemon, `start_managed_ollama_at` (`:210`), is called exactly once at boot
(`packaging/entry.py:600`) and never from any repair/restart path.

### D3. No watchdog anywhere

`_managed_proc` is written at start (`:190`, `:267`) and read only by
`stop_managed_ollama` (`:315`). There is no poll loop, health monitor, or restart-on-death
for either variant; a daemon killed by OOM / sleep-wake / AV stays dead until app restart.
Confirmed by grep: `poll()` appears only inside `stop_managed_ollama`.

### D4. Crash-orphan adoption is unrecorded

`start_managed_ollama_at` returns True early when the private port already responds
(`:225-227`) **without recording anything**: a leftover daemon from a force-killed app is
served to the user but is invisible to `stop_managed_ollama` and (post-fix) to restart
routing. The private port is dedicated to OpenBiliClaw, so adoption is safe to *record*;
we still cannot signal a process we don't own (invariant 1) — recording enables
probe-based watchdog recovery after it dies, which is the case that matters.

### D5 (minor). `OLLAMA_KEEP_ALIVE` uses `setdefault` for the private daemon

`ollama_supervisor.py:237` — a user-level `OLLAMA_KEEP_ALIVE=0` (common RAM-saving tweak)
leaks into our private daemon and causes 5-minute model unloads plus cold-start 502s that
the 60s diagnostic probe can misread as `model_broken`.

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 0 | Supervisor managed-daemon spec (record host+models_dir, endpoint predicate, restart routing, D5 env fix) | **MUST** | Foundation: D2/D4/D5; everything else consumes the recorded spec |
| 1 | Repair-gate parity in `api/app.py` (D1) | **MUST** | Un-breaks the one-click repair for with-embedding users |
| 2 | Watchdog thread with backoff (D3) | **MUST** | Removes the "dies and stays dead" class for both variants |
| 3 | `packaging/entry.py` adoption recording + watchdog arming (D4) | RECOMMENDED | Completes coverage for force-quit orphans and boots the watchdog in the desktop process |
| 4 | Docs sync (module doc + changelog) | **MUST** | CLAUDE.md documentation requirement |

Dependencies: Phase 1, 2, 3 all depend on Phase 0's recorded spec. Phases 1 and 2 are
independent of each other. **Single wave** (dependency order 0 → 1 → 2 → 3 → 4): all
phases change runtime product behavior and ship together behind the existing test suite;
work may stop after Phase 2 with the primary complaint fixed (Phase 3 covers the rarer
force-quit path).

## Phase designs

### Phase 0 — Managed-daemon spec + restart routing (supervisor)

- New module-level record replacing bare `_managed_proc`:
  `_ManagedDaemon(proc: Popen | None, base_url: str, models_dir: str | None)` stored in
  `_managed_daemon: _ManagedDaemon | None`. `proc=None` means "adopted" (recorded but not
  signalable). Keep a `_managed_proc` compatibility accessor only if existing tests
  require it (check `tests/test_ollama_supervisor.py` usages; prefer updating tests).
- Writers: `_ollama_start_serve_background` records
  `(proc, _DEFAULT_OLLAMA_ENDPOINT, managed_models_dir())`; `start_managed_ollama_at`
  records `(proc, base_url, abspath(models_dir))`, and its early-return adoption branch
  records `(None, base_url, abspath(models_dir))` (D4 recording happens here so Phase 3
  is just entry.py wiring + tests).
- New predicate `is_managed_endpoint(endpoint: str) -> bool`: normalized host:port
  comparison against `_managed_daemon.base_url` (scheme-insensitive, `localhost` ≡
  `127.0.0.1` ≡ `::1`), False when no record exists.
- Restart routing: `restart_managed_ollama()` becomes spec-aware — with a recorded private
  daemon it stops (only when `proc` is owned) and re-launches via
  `start_managed_ollama_at(models_dir, hostport)`; with a default-daemon record it keeps
  today's `_ollama_start_serve_background()` path. External-daemon refusal probes the
  **recorded** endpoint, not hardcoded 11434: refuse when the endpoint responds but our
  record is absent/`proc=None`-alive (can't stop what we don't own → `external_ollama` /
  new reason `adopted_alive`). `restart_managed_ollama_with_models_dir` keeps its current
  default-daemon semantics (it is the path-encoding migration tool) but must refuse with
  `private_daemon` when the record is a private daemon (models migration doesn't apply).
- `stop_managed_ollama` clears the whole record; unchanged kill semantics.
- D5 fix: in `start_managed_ollama_at`, `env["OLLAMA_KEEP_ALIVE"] = "24h"` (hard set).
  Default path keeps `setdefault` (invariant 4).

Error behavior: all new paths log via the existing `console`/logger patterns; no new
exception types escape to callers (bool/tuple returns preserved).

### Phase 1 — Repair-gate parity (api/app.py)

- New supervisor export `may_manage_ollama_endpoint(endpoint: str) -> bool` =
  `_is_default_ollama_endpoint(endpoint) or is_managed_endpoint(endpoint)`.
- Both `may_manage` computations (`app.py:2578-2583`, `:2666-2671`) swap
  `_is_default_ollama_endpoint(endpoint)` → `may_manage_ollama_endpoint(endpoint)`.
- The not_running action swaps `_ollama_start_serve_background()` for a new
  `ensure_managed_ollama(endpoint) -> bool`: routes to `start_managed_ollama_at(recorded
  models_dir, recorded hostport)` when `is_managed_endpoint(endpoint)` and the record is
  private, else `_ollama_start_serve_background()`. The provider_error action's
  `restart_managed_ollama()` is already spec-aware after Phase 0 — no change beyond the
  gate.
- Note for the desktop flow: `manage_ollama` (`cfg.autostart.manage_ollama`) must be True
  for repair to manage anything (unchanged); packaged desktop configs set it — verify with
  a grep and record the finding in the PR (if the with-embedding template leaves it False,
  add it to the template in this phase; that is in scope).

### Phase 2 — Watchdog

- `start_ollama_watchdog(interval_seconds: float = 30.0) -> None` in the supervisor:
  idempotent (second call no-ops), spawns a single daemon thread (`obc-ollama-watchdog`).
  Loop: sleep interval → if no `_managed_daemon` record, continue → health = HTTP probe of
  recorded endpoint (`_ollama_is_running(base_url)`, already `trust_env=False`) → healthy:
  reset failure counter/backoff, continue → unhealthy AND (owned proc has exited, or
  adopted record no longer responds): attempt restart via the Phase 0 routing; on failure
  apply invariant 3 backoff (5s, 10s, 20s … cap 300s; give up after 5 consecutive
  failures with phase `down` + ERROR log; counter reset by manual repair success — the
  repair endpoint calls a small `reset_watchdog_backoff()` hook — or process restart).
- Threading: all state transitions guarded by a module lock shared with the
  start/stop/restart functions (they mutate `_managed_daemon` too); watchdog restart calls
  run outside the lock's critical section for the blocking parts (Popen + 15s health wait)
  with a "restart in progress" flag preventing concurrent manual-repair races.
- Arming: `_ollama_start_serve_background` and `start_managed_ollama_at` call
  `start_ollama_watchdog()` after a successful start/adoption record — every managed
  daemon is watched with zero call-site changes. Testability: interval and probe/clock
  injectable via module-level indirection (`_watchdog_sleep`, `_watchdog_probe`) so tests
  drive iterations synchronously.

### Phase 3 — entry.py + template wiring

- `packaging/entry.py:600` success path needs no new call (Phase 2 arms the watchdog
  inside `start_managed_ollama_at`); this phase verifies that with a unit test on the
  seeding flow (`tests/test_bundled_embedding_entry.py`) asserting the watchdog is armed
  and the adoption branch records the daemon.
- Force-quit scenario test: simulate "port responds at boot, no proc handle" → record
  exists with `proc=None`, `is_managed_endpoint` True, repair gate passes, and after the
  adopted daemon "dies" (probe flips False) the watchdog start-routing launches a fresh
  private daemon.

### Phase 4 — Docs

`docs/modules/llm.md` (managed-Ollama lifecycle: recorded spec, watchdog, repair coverage
table incl. private daemon), `docs/changelog.md` bullet. `config.example.toml` only if
Phase 1's `manage_ollama` template finding requires it.

## Expected impact

| Lever | Measured effect |
| --- | --- |
| Phase 0+1 | with-embedding repair button: 409-always → actually restarts the private daemon (endpoint test flips) |
| Phase 2 | crashed managed daemon: dead-until-app-restart → auto-recovered ≤ 2×30s; flap-guarded |
| Phase 3 | force-quit orphan: invisible → recorded, watchdog-covered after death |
| Phase 0 (D5) | private daemon immune to user `OLLAMA_KEEP_ALIVE`; fewer cold-load `model_broken` misdiagnoses |

## Documentation obligations

- `docs/modules/llm.md` — managed-Ollama lifecycle + repair coverage
- `docs/changelog.md` — bullet under current version block
- `config.example.toml` — only if the `manage_ollama` template gap is confirmed in Phase 1
- No architecture-diagram change (no new module/dependency; intra-module lifecycle fix)
