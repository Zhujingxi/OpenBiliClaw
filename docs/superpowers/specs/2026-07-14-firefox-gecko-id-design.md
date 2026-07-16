# Firefox Gecko ID and Signed XPI Release Design

## Context

Issue #71 requests a Firefox XPI that installs in normal Firefox Release and
Beta builds. The repository already builds a Firefox-specific archive and uses
Mozilla AMO unlisted signing when `FIREFOX_SIGNING_ENABLED=true` and the two AMO
JWT secrets are present.

The signing workflow now reaches AMO successfully, but AMO rejects version
submission for the current Gecko ID, `openbiliclaw@whiteguo233.github.io`, with
HTTP 403. AMO's submission API only permits an existing add-on's authors to
publish another version. The current AMO account has no add-on under that ID,
so the ID cannot be used for this project's first signed release.

## Goals

- Give the Firefox build a new stable Gecko ID owned by the current AMO account.
- Publish a traceable v0.3.165 release whose source tag contains that ID.
- Produce a Mozilla-signed `openbiliclaw-extension-v0.3.165-firefox.xpi`.
- Make the signed XPI visible in both the extension channel release and the
  user-facing aggregate release.
- Prevent accidental Gecko ID drift with an automated test.
- Keep release and module documentation synchronized with the code.

## Non-goals

- Do not modify Chrome's extension identity or Chrome Web Store listing.
- Do not rewrite or move the existing v0.3.164 tags and releases.
- Do not add Firefox automatic updates; the GitHub Release XPI remains a
  manually updated, AMO-unlisted package.
- Do not change extension permissions, runtime behavior, module boundaries, or
  architecture diagrams.
- Do not close issue #71 until the signed XPI is present and verified.

## Selected Identity

Set `browser_specific_settings.gecko.id` in
`extension/manifest.firefox.json` to:

```text
openbiliclaw-firefox@whiteguo233.github.io
```

The value is email-shaped as recommended by Mozilla, is human-readable, and is
specific to this project's Firefox distribution channel. It is an immutable
release identity after the first successful AMO submission.

## Implementation Design

### Manifest contract

Only the Firefox manifest changes identity. `extension/manifest.json` remains
the Chrome-compatible manifest and continues to provide the shared extension
version. `extension/scripts/build.mjs` keeps injecting that version into the
Firefox manifest at build time.

Extend `extension/tests/manifest-assets.test.ts` with an assertion that the
Firefox Gecko ID equals the selected value. The test must fail against the old
ID before the manifest is changed and pass after the minimal manifest update.

### Version and release contract

Release v0.3.165 as a new immutable source state. Use the repository's release
helper to keep the mechanical version fields synchronized, including Python,
extension, lockfile, installer, and README/docs version references required by
the established release process.

The release tags must point to the v0.3.165 release commit. Component tags are
pushed separately because GitHub can drop workflow events when several tags
are pushed together. The extension release workflow must see AMO signing
enabled and require the signed XPI; a signing failure remains a failed release
rather than silently publishing only an unsigned Firefox ZIP.

The final extension release must contain:

```text
openbiliclaw-extension-v0.3.165.zip
openbiliclaw-extension-v0.3.165-firefox.zip
openbiliclaw-extension-v0.3.165-firefox.xpi
```

The aggregate `openbiliclaw-v0.3.165` release must also contain the signed XPI
alongside the same-version extension assets.

### Documentation

Update `docs/modules/extension.md` to document the stable Gecko ID and its AMO
ownership requirement. Add a v0.3.165 entry to `docs/changelog.md` describing
the user-visible Firefox installation fix. Update the Chinese and English
README release callouts in lockstep, keeping them within the repository's
four-bullet and one-sentence limits. Update other mechanical version references
required by `scripts/release.py` and the existing release checklist.

Architecture diagrams, CLI documentation, configuration documentation, and
installer-flow documentation do not change because the work alters neither
runtime wiring nor user configuration.

## Validation

Before publishing:

1. Run the targeted manifest test once before the production change and verify
   that it fails because the old Gecko ID is present.
2. Change the manifest ID and rerun the targeted test to green.
3. Run the full extension test suite and TypeScript typecheck.
4. Build the Firefox package and run `web-ext lint` against `dist-firefox`.
5. Run the repository release-consistency checks and the relevant Python test,
   Ruff, and MyPy gates required by the release plan.
6. Confirm the working tree contains only intentional files before commit.

After publishing:

1. Inspect the extension workflow logs for successful AMO validation, approval,
   signed XPI download, archive verification, and Release upload.
2. Query the extension and aggregate GitHub Releases and require the exact XPI
   asset name.
3. Download the XPI and verify it is a valid ZIP/XPI containing a root
   `manifest.json` with the new Gecko ID and version 0.3.165.
4. Keep issue #71 open if AMO review is pending or any required asset is absent.

## Failure Handling

- HTTP 409 for the new ID means the ID is already occupied; stop and select a
  different identity rather than retrying.
- HTTP 401 means the AMO issuer/secret pair is invalid; stop and correct the
  repository secrets without exposing them in logs.
- HTTP 403 for the new ID means the authenticated AMO account lacks ownership;
  stop and inspect the AMO account rather than weakening the workflow.
- AMO validation errors are fixed in source and released from a new commit; no
  unsigned file is renamed to `.xpi`.
- A successful workflow without the exact XPI asset is treated as incomplete.

## Success Criteria

- Source and release tags record the new Gecko ID at v0.3.165.
- All required local validation passes.
- Mozilla signs the Firefox package under the current AMO account.
- Both GitHub Release surfaces expose
  `openbiliclaw-extension-v0.3.165-firefox.xpi`.
- No existing v0.3.164 tag or asset is rewritten.
