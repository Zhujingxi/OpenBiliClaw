# Application Workflows

`src/openbiliclaw/application/` is the only explicit sequencing layer for cross-module product operations. Each workflow accepts a frozen typed command/query and narrow Protocol dependencies; there is no command bus, workflow DSL, hook choreography, service locator, or god orchestrator. Composition supplies the concrete repositories and transaction adapters; API, CLI, and Assistant are transport/tool adapters over these workflows.

## Workflow boundary

- model-free reads: source status with a joined recommendation-inventory summary, recommendation feed delivery, profile projection, provider search, content details, and job health, all with bounded pagination; feed delivery atomically records stable shown IDs before returning items;
- mutations: connect/disconnect, typed observation import, bounded credentialed history/save sync, verified provider-export import, feedback, profile edit, bounded recommendation refresh admission;
- feedback validates the delivered shown record/content pair, resolves exploration attribution from the durable candidate (never client input), transitions shown → interacted, and commits its learning observation through an explicit unit of work; only a newly inserted feedback record invokes the optional reward/Understanding sink, preventing duplicate hypothesis/exploit/proposal credit; profile override + audit observation uses its own unit of work;
- source connect verifies and stores credentials through Access before refreshing availability; `PluginAssistedAccess` serves provider recipe data and validates exact browser material before converging on the same connect/replace path;
- pending actions store only typed declarative effect, identity, scope, safe preview, expiry, decision, and idempotency metadata; confirmation revalidates all of them. Content confirmation revalidates provider state; profile-revision confirmation dispatches to canonical `EditProfile`; `POST /v1/content/actions/reject` durably cancels even an expired pending action but conflicts after approval, and cannot execute either mutation;
- `ExternalEvidenceIngestion` pages at most two 50-item `History` and `Saved` pages for credential handles, normalizes each item through Observation ingress, and silently skips disconnected/public-only providers. The same normalizer accepts only the existing real YouTube Takeout watch-history format; subscriptions/likes are reported as ignored rather than mislabeled as saves, and no Bilibili archive schema is invented.
- recommendation refresh requests Core-owned job admission and never creates unmanaged tasks.

## Operation ownership

| Surface | Workflow owner |
|---|---|
| source list/inventory and status/connect/disconnect | `ListSources`, `GetSourceStatus`, `ConnectSource`, `DisconnectSource` |
| plugin recipe/material | `PluginAssistedAccess.recipe`, `PluginAssistedAccess.submit` |
| profile read/edit | `ShowProfile`, `EditProfile` |
| recommendation feed/refresh/feedback | `GetRecommendations`, `RefreshRecommendations`, `RecordFeedback`, `RecordFeedbackForShown` |
| observation batches | `RecordObservations` |
| credentialed history/save sync and YouTube Takeout evidence | `ExternalEvidenceIngestion.sync`, `ExternalEvidenceIngestion.import_file` |
| provider search/content detail | `SearchContent`, `GetContentDetails` |
| runtime diagnostics | `GetJobHealth` |
| content mutation proposal/confirmation | `ProposeContentAction`, `ConfirmContentAction` |
| profile correction proposal/approval/rejection | `ProposeProfileRevision`, `ConfirmProfileRevision`, `RejectPendingAction` → `EditProfile` |
| Assistant dialogue | Assistant facade calling these workflows |

Broad integration facades, direct profile writes, fake source tools, extension credential tasks, and other legacy sequencing were deleted. Each retained product operation has one owner.

## Transaction and notification rules

Workflow contracts expose validation, authorization, idempotency, and audit fields. A unit of work returns only after primary state commits; the feedback reward callback is post-insert policy-journal accounting and is never called for an idempotent duplicate. Post-commit publisher failure is recoverable and does not imply rollback. Cancellation is never converted to success. User data is never silently reset or discarded.
