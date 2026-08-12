# Recommendation target pipeline

`openbiliclaw.recommendation` now contains the target deterministic discovery-to-history pipeline. Candidate state is explicit (`discovered → normalized → prefiltered → evaluated → admitted → selected → shown → interacted/expired`, with terminal rejection) and every transition is checked in Python and enforced by a SQLite transition trigger. Rejections and admissions have dedicated immutable audit tables; selection persists evaluated → admitted → selected atomically.

Discovery plans bounded provider-neutral queries from `DiscoveryProfile`, falls back to deterministic defaults when `recommendation.query` is unavailable, and calls typed provider search capabilities directly. Hard prefilters run before the one-shot `recommendation.evaluate` contract. Selection applies hard negative preferences, then uses named model/freshness/novelty contributions plus fixed provider, creator and topic quotas. Optional `recommendation.expression` stores copy provenance separately and falls back to safe deterministic text.

`RecommendationService.feed()` reads selected records without a model. Discovery, evaluation, expiry and replenishment are Core `JobSpec`s with explicit timeout, resource and non-overlap policies.

Not yet wired into production composition. Feedback-to-Observation wiring belongs to Plan 11; legacy discovery/recommendation engines and producers remain until Plan 15; offline domain evaluation scenarios remain deferred to the evaluation sweep.
