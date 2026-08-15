# Application Workflows

`src/openbiliclaw/application/` 是跨模块产品操作唯一的显式 sequencing 层。每个 workflow 接受 frozen typed command/query 和窄 Protocol 依赖；没有 command bus、workflow DSL、hook choreography、service locator 或 god orchestrator。Composition supplies the concrete repositories and transaction adapters; API, CLI, and Assistant are transport/tool adapters over these workflows.

## Workflow boundary

- model-free reads: source status, recommendation feed delivery, profile projection, provider search, content details, and job health, all with bounded pagination; feed delivery atomically records stable shown IDs before returning items;
- mutations: connect/disconnect, typed observation import, feedback, profile edit, bounded recommendation refresh admission;
- feedback validates the delivered shown record/content pair, transitions shown → interacted, and commits its learning observation through an explicit unit of work; profile override + audit observation uses its own unit of work;
- source connect verifies and stores credentials through Access before refreshing availability;
- pending actions store only identity, scope, safe preview, expiry, and idempotency metadata; confirmation revalidates all of them;
- recommendation refresh requests Core-owned job admission and never creates unmanaged tasks.

## Operation ownership

| Surface | Workflow owner |
|---|---|
| source status/connect/disconnect | `GetSourceStatus`, `ConnectSource`, `DisconnectSource` |
| profile read/edit | `ShowProfile`, `EditProfile` |
| recommendation feed/refresh/feedback | `GetRecommendations`, `RefreshRecommendations`, `RecordFeedback`, `RecordFeedbackForShown` |
| observation batches/history imports | `RecordObservations` |
| provider search/content detail | `SearchContent`, `GetContentDetails` |
| runtime diagnostics | `GetJobHealth` |
| content mutation proposal/confirmation | `ProposeContentAction`, `ConfirmContentAction` |
| Assistant dialogue | Assistant facade calling these workflows |

Broad integration facades, direct profile writes, fake source tools, extension credential tasks, and other legacy sequencing were deleted. Each retained product operation has one owner.

## Transaction and notification rules

Workflow contracts expose validation, authorization, idempotency, and audit fields. A unit of work returns only after primary state commits; post-commit publisher failure is recoverable and does not imply rollback. Cancellation is never converted to success. User data is never silently reset or discarded.
