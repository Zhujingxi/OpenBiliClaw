- **目标 responsive Vue web shell 落地（尚未接入 production composition）**：`frontend/apps/web/` 新增按 session、sources、recommendations、content、profile、Assistant、runtime/job 与 host-local preferences 拆分的 Pinia stores；server reads 具备 idle/loading/success/empty/error、AbortController 取消，event stream 只有一个 owner 并使用 bounded reconnect backoff。单一 responsive shell 提供 recommendations、provider tabs、search/detail、profile、Assistant、source connection、settings 与 runtime health views，desktop/mobile 使用不同 navigation/density 但不复制业务 UI；补齐 skip/focus/keyboard/reduced-motion/contrast/live-region 基础可访问性。Shared `ApiClient` 新增 generated-operation-typed query/path interpolation，避免 web 硬编码 URL。按计划，production composition、browser E2E、extension cutover 与 legacy JS 删除等待 Phase 14c/Plan 15。

# Phase 14b review fixes

- Recommendation reads now join each persisted selection to its durable ContentRef and CardData projection; generated OpenAPI types expose the feed contract and the web renders it through shared presentation cards.
- Web source connection, search-to-detail navigation, route-selected detail, and Assistant history/output are functional.
- Typed API requests require generated query/path parameters, reject unresolved templates, preserve AbortError, and stream with replay cursors.
- Stores uniformly suppress cancellation and stale commits; runtime streaming deduplicates, caps events, replays from the highest event ID, and bounds reconnect delay.
- Added distinct desktop/mobile host layouts, safe skip-link focus, semantic provider status list, and preference bootstrap hydration/persistence.
- Expanded api-client, store, bootstrap, accessibility, and view behavioral tests.
- Acceptance fixes persist a browser device identity and attach the host-required matching device/CSRF headers to every typed mutation; complete deferred state matrices now cover content/profile/Assistant/sources/runtime, and the responsive shell mounts routed content exactly once.
