---
name: openbiliclaw
version: "1.0.0"
description: Operate every OpenBiliClaw user workflow through its local JSON CLI.
user-invocable: true
---

# OpenBiliClaw CLI

Use the installed `openbiliclaw` command. Do not call internal Python modules, edit the database, read the credential vault, or use HTTP loopback.

## Rules

1. Pass the selected `--config PATH` and `--data-dir PATH` consistently.
2. Read stdout as exactly one JSON document. Read JSON errors from stderr and check the exit status.
3. Run `openbiliclaw COMMAND --help` for exact arguments.
4. Use `-` and stdin for JSON containing credentials or API keys; never put secrets in argv.
5. Supply a stable unique `--idempotency-key` for every mutation.
6. Confirm or reject pending actions explicitly; never bypass the pending-action workflow.

## Discover and inspect

```bash
openbiliclaw check --config config.toml --data-dir data
openbiliclaw sources list --config config.toml --data-dir data
openbiliclaw feed --limit 20 --config config.toml --data-dir data
openbiliclaw runtime health --config config.toml --data-dir data
openbiliclaw runtime events --after 0 --limit 100 --config config.toml --data-dir data
openbiliclaw models current --config config.toml --data-dir data
```

## User workflows

- Sources: `sources list|status|form|capabilities|access-recipe|submit-material|add|remove|sync`
- Recommendations: `feed`, `refresh`, `feedback`, `record-feedback`
- Learning input: `observations -`
- Profile: `profile show|exploration|edit`
- Assistant: `assistant`, `conversations show|messages`
- Content: `search`, `content detail`, `actions propose|confirm|reject`
- Runtime/configuration: `runtime health|config-diagnostics|model-diagnostics|events`, `models catalog|current|set`
- Security/data: `set-password --password-stdin`, `ext-token`, `export`, `import`

## Secret-safe JSON

```bash
printf '%s' "$SOURCE_FIELDS_JSON" | openbiliclaw sources add bilibili builtin.manual \
  --permission read_public --idempotency-key "$KEY" --fields-file - \
  --config config.toml --data-dir data

printf '%s' "$MODEL_REQUEST_JSON" | openbiliclaw models set - \
  --config config.toml --data-dir data
```

Typed request commands accept at most 1 MiB and use the same strict schemas as the `/v1` API. Preserve returned `shown_id`, `conversation_id`, `ContentRef`, and `pending_action_id` values exactly for subsequent commands.
