# Historical v0.3 six-platform native-save E2E archive

> **Non-executable archive.** The installed-extension native-save harness, saved-sync runtime, and
> platform-account mutation routes were removed from vNext. Do not obtain authorization or execute
> the former write matrix against a current installation.

The former six-platform procedure remains available only in Git history. Current favorites and
watch later are local collections served by `/api/v1/library/{collection}`. The vNext extension
performs passive evidence capture and manifest-declared read/import operations through the generic
`/api/v1/source-tasks/claim` and `/api/v1/source-tasks/{task_id}/complete` contract; it does not
write favorites, watch-later entries, follows, likes, subscriptions, or other state to platform
accounts.

For current browser/source verification, use [Manual E2E](../manual-e2e.md).
