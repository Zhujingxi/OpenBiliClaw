# Observation Ingress

`openbiliclaw.observations` is the sole ingress boundary for user-behavior evidence and is wired by production composition into Application and Understanding. It owns an immutable typed observation vocabulary, shared provenance/trust validation, idempotent SQLite persistence, cursor replay, and post-commit notifications containing only committed IDs. It does not update the user profile or own analyzer checkpoints.

## Implemented

- 14 Pydantic discriminated observation variants: recommendation shown/opened/liked/disliked/saved/dismissed, host content opened/saved, Assistant feedback/preference, deterministic profile edit, provider-history import, and shared `external_history_view` / `external_save` for credential and Takeout data; schema version is fixed at 1.
- Each record contains a stable observation ID, producer idempotency key, occurred/received timestamps, optional account/content refs, typed provenance/trust, and a variant-specific payload; there is no `event_type + dict` fallback.
- Validation covers the producer allowlist, event allowlist, source/event pairing, clock skew, required content, account identity, and trust. An unauthenticated host producer is limited to low trust and cannot claim account identity.
- A batch contains at most 100 records and each serialized record at most 64 KB. Per-record validation rejection and duplicate acceptance are returned separately. All accepted rows commit in one SQLite transaction; rollback publishes no notification.
- Credentialed `History`/`Saved` pages and verified YouTube Takeout watch-history exports normalize to the same bounded external-content payload (title, optional creator, provider event ID). They are authenticated high-tier behavioral observations, so Understanding projects them at `0.6`, never statement-level `1.0`; identity is deterministic by provider content ID + event type and retries deduplicate.
- SQLite uniqueness is `(producer, idempotency_key)`, with deterministic replay by insertion cursor. An observation with a `ContentRef` idempotently writes `(provider, external_id)` to `content_references`. Observations carry no projection body and therefore do not write `content_cache`. Understanding owns its processing checkpoint. Committed-ID publication is only an advisory latency hint; cursor reads are authoritative for recovery, and post-commit publish failures are not retried within ingress.
- Future `ObservationProvider` implementations use Core `ObservationProviderRegistration`. A future browser extension must submit the same shared observation schema through a separate signed/device-authenticated producer. Browser-specific payloads, cookies, cross-site trackers, browser sessions, and managed browsers are outside this module.

## Security boundary

Free text has strict length limits and rejects HTML, authorization/cookie canaries, and prompt-instruction text. Notifications publish only observation IDs; full payloads are read only from the repository. This module must not import Understanding, Recommendation, Assistant, or Hosts.

## Composition

Application workflows own built-in producer submission; Understanding owns consumer checkpoints. Deleted event-ingress and scattered write paths have no compatibility or double-write path.
