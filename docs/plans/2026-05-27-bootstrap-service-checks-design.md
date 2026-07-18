# Bootstrap Service Checks Design

## Goal

Before one-line installs auto-run `openbiliclaw init`, verify that both the
configured LLM provider and the configured embedding service can answer a real
lightweight request. If either service is unhealthy, stop before init and tell
the user what to fix.

## Approaches Considered

1. Reuse the CLI `health-check` command before init.
   This covers chat providers but not the independent embedding service.

2. Add a bootstrap-local pre-init gate.
   This keeps the one-line installer contract self-contained, can emit
   `BOOTSTRAP_STATUS` events, and can block only the automated init path.

3. Add a backend `/api/health` dependency check.
   This would be useful later for UI diagnostics, but it would couple install
   progress to the running API process and still need bootstrap parsing.

## Decision

Use approach 2. `scripts/agent_bootstrap.py` will run a pre-init service check
after backend health and init decisions are resolved, but before calling
`openbiliclaw init`.

## Design

The bootstrap script will execute a small Python probe inside the installed
project. The probe loads `config.toml`, builds the LLM registry, calls the
default chat provider with a tiny prompt, builds the embedding service, and
embeds a short text. It returns structured JSON with one result for `llm` and
one result for `embedding`.

When both checks pass, bootstrap continues to `openbiliclaw init`. When either
check fails, bootstrap emits `BOOTSTRAP_STATUS` with status
`service_check_failed`, includes the failing service names and errors, prints a
human-readable repair hint, and returns without running init.

Embedding remains mandatory for auto-init unless the user explicitly disabled
it with `--embedding-provider ""`; in that disabled case the service check
reports embedding as skipped and init may continue.

## Tests

Add unit coverage around the bootstrap gate:

- passing LLM + embedding checks allow init to run;
- failed LLM checks block init;
- failed embedding checks block init;
- explicitly disabled embedding skips the embedding probe.

Docs that describe the one-line install contract will mention that init is
blocked until LLM and embedding checks pass.
