# Douyin Content Provider

`content/providers/douyin/` retains the strict aweme native schema, canonical video identity, purpose-specific projections, and presentation descriptor. It currently advertises only projection; the strict native schema, canonical identity, purpose projections, and presentation descriptor remain available for a future replayable `AccessMethod` to unlock read capabilities.

The provider manifest is `degraded` and advertises only `projection`. The old direct-cookie search still depends on session-varying msToken/X-Bogus/risk-control state and is not a safely replayable anonymous or manual-credential capability. Recommendation feeds, creator, fetch, personal history, saved content, writes, Cookie login, and browser/extension task dispatch are outside the current provider. Manifest capabilities may be expanded only after a new replayable `AccessMethod` is independently verified.

The production graph registers this degraded projection-only provider. Deleted direct-cookie/task adapters have no compatibility facade or double-write path.
