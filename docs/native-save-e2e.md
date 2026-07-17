# Historical v0.3 native-save E2E archive

> **Non-executable archive.** Native platform save, saved sync, automatic sync, and their
> `/api/saved*`, `/api/saved-sync*`, `/api/config`, and platform-specific task endpoints were
> removed from the vNext product/runtime. Do not run the former account-mutating procedure against
> a current installation.

The historical Bilibili native-save runbook is retained only in Git history for release archaeology.
Current favorites and watch later are local vNext collections exposed through
`/api/v1/library/{collection}`; removing a local collection item never mutates a platform account.
Browser-assisted read/import work uses the generic `/api/v1/source-tasks/claim` and
`/api/v1/source-tasks/{task_id}/complete` contract.

For current verification, use [Manual E2E](manual-e2e.md) and the generated OpenAPI contract.
