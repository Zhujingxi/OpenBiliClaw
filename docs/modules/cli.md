# CLI Host

The installed `openbiliclaw` command is owned by Runtime Composition. There is no legacy/v2 split.

## Command surface

```text
openbiliclaw check [--config PATH] [--data-dir PATH]
openbiliclaw serve [--config PATH] [--data-dir PATH]
openbiliclaw set-password [--config PATH] [--password-stdin]
openbiliclaw ext-token [--data-dir PATH]
openbiliclaw export PATH [--data-dir PATH] [--include-config] [--config PATH]
openbiliclaw import PATH [--data-dir PATH] [--force]
openbiliclaw import PROVIDER EVIDENCE_PATH [--config PATH] [--data-dir PATH]

openbiliclaw sources list [--account-id ID] [--limit N]
openbiliclaw sources status PROVIDER [--account-id ID]
openbiliclaw sources form PROVIDER METHOD
openbiliclaw sources capabilities PROVIDER
openbiliclaw sources access-recipe PROVIDER
openbiliclaw sources submit-material PROVIDER REQUEST_JSON_OR_-
openbiliclaw sources add PROVIDER METHOD --permission PERMISSION --idempotency-key KEY [--account-id ID] [--fields-file REQUEST_JSON_OR_-]
openbiliclaw sources remove PROVIDER --idempotency-key KEY [--account-id ID]
openbiliclaw sources sync PROVIDER

openbiliclaw feed [--limit N]
openbiliclaw refresh --idempotency-key KEY [--maximum-items N]
openbiliclaw feedback SHOWN_ID <like|dismiss|save|open> --idempotency-key KEY [--exposed]
openbiliclaw record-feedback REQUEST_JSON_OR_-
openbiliclaw observations REQUEST_JSON_OR_-

openbiliclaw profile show [--profile-id ID]
openbiliclaw profile exploration <disable|enable> --idempotency-key KEY [--profile-id ID] [--account-id ID]
openbiliclaw profile edit REQUEST_JSON_OR_-

openbiliclaw assistant MESSAGE [--conversation-id ID] [--device-id ID] [--locale LOCALE]
openbiliclaw conversations show CONVERSATION_ID [--device-id ID]
openbiliclaw conversations messages CONVERSATION_ID [--device-id ID] [--limit N]

openbiliclaw search PROVIDER QUERY [--limit N]
openbiliclaw content detail JSON_CONTENT_REF
openbiliclaw actions propose REQUEST_JSON_OR_-
openbiliclaw actions confirm PENDING_ACTION_ID [--user-id ID]
openbiliclaw actions reject PENDING_ACTION_ID [--user-id ID]

openbiliclaw runtime health
openbiliclaw runtime config-diagnostics
openbiliclaw runtime model-diagnostics
openbiliclaw runtime events [--after EVENT_ID] [--limit N]

openbiliclaw models catalog
openbiliclaw models current
openbiliclaw models set REQUEST_JSON_OR_-
```

Global `--config` and `--data-dir` options work before or after commands. Every command and nested command has `--help` discovery text.

## Agent contract

The CLI covers the same user operations as the Web/API surface. Product commands start the in-process composition graph, call one typed workflow or host service, print one compact JSON document, and stop the graph. They never use HTTP loopback and contain no product business logic. Expected Application and validation failures are one JSON error document on stderr with non-zero exit status.

Commands with `REQUEST_JSON_OR_-` accept a JSON file path or `-` for stdin and reject inputs over 1 MiB. Agents should use stdin for source material, model API keys, and other secret-bearing requests so secrets do not enter argv or shell history. `sources add --field KEY=VALUE` remains for interactive compatibility; automation should use `--fields-file -`. `set-password --password-stdin` reads one UTF-8 password from stdin without confirmation or echo.

Request JSON uses the same strict Pydantic transport schemas as `/v1`: `AccessMaterialRequest`, `FeedbackRequest`, `ObservationsRequest`, `ProfileEditRequest`, `ProposeActionRequest`, and `ModelConfigurationRequest`. Unknown fields and invalid identities fail closed.

`check`, archive export/import, password configuration, and token minting also emit one JSON document. `ext-token` returns the raw token once; only its SHA-256 hash is stored. Archive import validates and migrates a staged snapshot before installation and refuses a non-empty destination unless `--force`.

`sources sync` invokes bounded external-evidence ingestion. Disconnected/public-only sources return a successful skipped result; credentialed providers import at most two recent pages per declared `History`/`Saved` capability. Provider evidence import uses the same observation normalization and idempotency path.

The CLI intentionally does not expose raw database writes, vault reads, destructive reset, or an unsafe self-reload. Model updates persist validated settings and report restart requirements through the existing model configuration contract.
