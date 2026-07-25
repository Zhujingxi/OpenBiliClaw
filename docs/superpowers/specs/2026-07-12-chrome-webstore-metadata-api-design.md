# Chrome Web Store Metadata API Automation Design

**Date:** 2026-07-12

## Goal

Use the existing Chrome Web Store OAuth secrets in GitHub Actions to update the
OpenBiliClaw store summary and detailed description without requiring an
interactive Google Dashboard session. Preserve every unrelated listing field,
verify the written draft, and re-submit the existing `0.3.163` package for
review. Do not claim that screenshots were updated unless an authenticated,
supported asset-write API is verified.

## Constraints

- Chrome Web Store API v2 has package upload, status, publish, cancel, and
  rollout methods, but no listing metadata write method.
- The deprecated v1.1 API remains available until 2026-10-15 and is the only
  candidate for metadata reads and writes.
- OAuth client ID, client secret, refresh token, publisher ID, and item ID stay
  in GitHub Actions secrets. Workflows must not print tokens or secret values.
- The public item is currently represented by extension version `0.3.163`.
  Updating listing metadata must not create or move release tags.
- Screenshot files are ready under `docs/images/chrome-web-store/`, but no
  public Chrome Web Store API documents a screenshot upload operation.

## Recommended Flow

### 1. Read-only capability probe

Add a metadata command that exchanges the existing refresh token for a short
access token and performs:

```text
GET https://www.googleapis.com/chromewebstore/v1.1/items/{itemId}?projection=DRAFT
```

The probe reports only:

- HTTP status;
- top-level response field names;
- whether summary and description are present;
- their lengths and SHA-256 hashes;
- whether any screenshot- or image-related fields exist.

It never prints access tokens, OAuth secrets, cookies, or raw private fields.
The listing text is public, but hashes and lengths are sufficient for the probe.

If the endpoint is unavailable, unauthorized, or does not expose writable
listing fields, stop without cancelling the current review.

### 2. Exact metadata update

After a successful probe, cancel the pending submission through API v2 only if
the item is actively under review. Build an allowlisted payload from the
existing draft response: retain the current `title`, `category`,
`defaultLocale`, `homepageUrl`, and `supportUrl` values when present; never echo
status, package, review, error, or other output-only fields. Replace only these
content keys:

- `summary` with the canonical Short Description from
  `docs/chrome-webstore-listing.md`;
- `description` with the canonical Detailed Description;
- `homepageUrl` and `supportUrl` with the documented project URLs when those
  keys are present in the draft response.

Send the merged payload to:

```text
PUT https://www.googleapis.com/chromewebstore/v1.1/items/{itemId}
```

The command must reject an empty summary, empty description, a summary longer
than 132 characters, content that omits the local-backend and local-data
statements, or a probe response that lacks enough allowlisted fields to preserve
the existing listing identity.

### 3. Read-back verification

Fetch the draft again and compare exact summary and description values with the
canonical document. A successful PUT without an exact read-back match is a
failure and must not proceed to publish.

### 4. Re-submit the existing draft

After read-back verification, call API v2 `publish` without uploading another
ZIP. Fetch status and require `PENDING_REVIEW` or the current equivalent review
state before reporting success.

If publish fails, report that the metadata draft was updated but not submitted;
do not retry package uploads or move tags.

## Screenshot Boundary

The probe may inspect response field names for image or screenshot capabilities.
It must not send files to guessed endpoints or write undocumented asset fields.
Unless a Google-documented endpoint or an authenticated response advertises a
supported upload flow, the five prepared PNG files remain repository artifacts
for later Developer Dashboard upload.

## Components

- `extension/scripts/chrome-webstore-metadata.mjs`
  - parse canonical listing copy;
  - refresh OAuth token;
  - probe, update, verify, and expose machine-readable results;
  - keep API operations independently testable.
- `extension/tests/chrome-webstore-metadata.test.ts`
  - parsing and validation;
  - redaction and probe summaries;
  - merge-only-listed-fields behavior;
  - exact read-back verification;
  - no screenshot write without a supported endpoint.
- `.github/workflows/publish-chrome-webstore.yml`
  - add an explicit metadata-refresh input;
  - run the read-only probe first;
  - cancel only after probe success;
  - update, verify, and publish using existing secrets.
- `docs/chrome-webstore-listing.md` and `docs/modules/extension.md`
  - document the metadata API bridge, its 2026-10-15 sunset, and the screenshot
    Dashboard boundary.

## Error Handling

- Authentication errors stop before any submission cancellation.
- Probe schema mismatches stop before writes.
- Update errors leave the cancelled item as a draft and print a precise recovery
  command without exposing secrets.
- Read-back mismatches stop before publish.
- Publish errors do not repeat metadata writes or package uploads.
- Every network request has an explicit timeout and at most one bounded retry for
  transient 429/5xx responses; authentication and validation failures never
  retry.

## Verification

1. Unit tests prove the metadata parser, validation, merge, redaction, and
   read-back checks using fixed fixtures.
2. Existing Chrome Web Store upload/pending-review tests remain green.
3. A manual Actions dispatch first runs the read-only probe.
4. Only after the probe succeeds does the same run cancel, update, verify, and
   publish.
5. API v2 status confirms the refreshed draft is under review.

## Non-goals

- No version bump or new GitHub Release.
- No changes to extension runtime behavior.
- No secret migration.
- No undocumented Dashboard RPC or cookie extraction.
- No claim that the five screenshots are live until their store-side state is
  independently verified.
