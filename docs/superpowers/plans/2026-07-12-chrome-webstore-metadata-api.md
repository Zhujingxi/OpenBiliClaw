# Chrome Web Store Metadata API Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely update the Chrome Web Store short and detailed descriptions with the existing OAuth secrets, verify the draft exactly, and re-submit the existing package without uploading a new ZIP.

**Architecture:** Keep the metadata bridge separate from the existing package-upload script. A pure library owns canonical-copy parsing, validation, redacted probe reporting, allowlisted payload construction, and read-back checks; a small CLI owns OAuth and the ordered v1.1/v2 API calls; a dedicated Actions workflow exposes a read-only `probe` mode and an explicit `apply` mode.

**Tech Stack:** Node.js 22 ESM, TypeScript/Vitest tests, GitHub Actions, Chrome Web Store API v1.1 metadata endpoints, Chrome Web Store API v2 status/cancel/publish endpoints.

## Global Constraints

- The read-only probe must finish successfully before any cancellation or metadata write.
- Never print access tokens, OAuth secrets, cookies, or raw draft values.
- Preserve only `title`, `category`, `defaultLocale`, `homepageUrl`, and `supportUrl`; replace only `summary` and `description` plus documented URLs when their keys already exist.
- Reject summary length above 132 characters and copy missing the local-backend or local-data statements.
- After PUT, require an exact GET read-back before publish.
- Do not upload a ZIP, create a release/tag, or write screenshot fields/endpoints.
- Chrome Web Store API v1.1 support ends on 2026-10-15, so the workflow and docs must expose that sunset.

---

### Task 1: Pure metadata contract

**Files:**
- Create: `extension/scripts/chrome-webstore-metadata-lib.mjs`
- Create: `extension/tests/chrome-webstore-metadata.test.ts`

**Interfaces:**
- Produces: `parseListingMarkdown(markdown: string): CanonicalListing`
- Produces: `validateListingMetadata(listing: CanonicalListing): void`
- Produces: `summarizeDraft(draft: Record<string, unknown>): DraftProbeSummary`
- Produces: `buildMetadataPayload(draft: Record<string, unknown>, listing: CanonicalListing): Record<string, string>`
- Produces: `verifyMetadataReadback(actual: Record<string, unknown>, expected: CanonicalListing): void`
- `CanonicalListing` contains `{summary, description, homepageUrl, supportUrl}`.

- [ ] **Step 1: Write failing parser and validation tests**

```ts
import { describe, expect, it } from "vitest";
import {
  buildMetadataPayload,
  parseListingMarkdown,
  summarizeDraft,
  validateListingMetadata,
  verifyMetadataReadback,
} from "../scripts/chrome-webstore-metadata-lib.mjs";

const markdown = `## Short Description\n\n\`\`\`text\n需本地后端的七平台内容发现 AI Agent：跨平台推荐、私有画像与可反馈侧边栏\n\`\`\`\n\n## Detailed Description\n\n\`\`\`text\nOpenBiliClaw 需要本地后端，平台数据保存在本地。\n\`\`\`\n\n- Homepage: https://github.com/WhiteCosmos/OpenBiliClaw\n- Support: https://github.com/WhiteCosmos/OpenBiliClaw/issues\n`;

it("parses canonical copy and validates local-data claims", () => {
  const listing = parseListingMarkdown(markdown);
  expect(listing.summary).toContain("需本地后端");
  expect(listing.description).toContain("保存在本地");
  expect(() => validateListingMetadata(listing)).not.toThrow();
});

it("rejects an overlong summary", () => {
  const listing = parseListingMarkdown(markdown);
  expect(() => validateListingMetadata({...listing, summary: "字".repeat(133)})).toThrow("132");
});
```

- [ ] **Step 2: Run the focused test and confirm the module is missing**

Run: `cd extension && npm test -- --run tests/chrome-webstore-metadata.test.ts`

Expected: FAIL because `chrome-webstore-metadata-lib.mjs` does not exist.

- [ ] **Step 3: Implement canonical parsing and validation**

```js
export function parseListingMarkdown(markdown) {
  const block = (heading) => {
    const match = markdown.match(new RegExp(`## ${heading}\\s+\\`\\`\\`(?:text)?\\n([\\s\\S]*?)\\n\\`\\`\\``));
    if (!match) throw new Error(`Missing ${heading} fenced block`);
    return match[1].trim();
  };
  const url = (label) => {
    const match = markdown.match(new RegExp(`^- ${label}:\\s+(https://\\S+)$`, "m"));
    if (!match) throw new Error(`Missing ${label} URL`);
    return match[1];
  };
  return {
    summary: block("Short Description"),
    description: block("Detailed Description"),
    homepageUrl: url("Homepage"),
    supportUrl: url("Support"),
  };
}

export function validateListingMetadata(listing) {
  if (!listing.summary || !listing.description) throw new Error("Summary and description are required");
  if ([...listing.summary].length > 132) throw new Error("Summary exceeds 132 characters");
  if (!`${listing.summary}\n${listing.description}`.includes("本地后端")) throw new Error("Copy must disclose the local backend");
  if (!listing.description.includes("保存在本地")) throw new Error("Copy must disclose local data storage");
}
```

- [ ] **Step 4: Add failing allowlist, redaction, and exact-readback tests**

```ts
it("summarizes drafts without exposing raw copy and writes only allowlisted fields", () => {
  const listing = parseListingMarkdown(markdown);
  const draft = {title: "OpenBiliClaw", category: "PRODUCTIVITY", defaultLocale: "zh_CN", summary: "old", description: "private old", status: "PENDING_REVIEW", screenshots: ["secret"]};
  const probe = summarizeDraft(draft);
  expect(JSON.stringify(probe)).not.toContain("private old");
  expect(probe.assetFieldNames).toEqual(["screenshots"]);
  expect(buildMetadataPayload(draft, listing)).toEqual({title: "OpenBiliClaw", category: "PRODUCTIVITY", defaultLocale: "zh_CN", summary: listing.summary, description: listing.description});
});

it("requires exact read-back", () => {
  const listing = parseListingMarkdown(markdown);
  expect(() => verifyMetadataReadback({...listing, description: `${listing.description} changed`}, listing)).toThrow("read-back");
});
```

- [ ] **Step 5: Implement hashing, allowlisted merge, and read-back checks**

```js
import { createHash } from "node:crypto";

const PRESERVED_FIELDS = ["title", "category", "defaultLocale", "homepageUrl", "supportUrl"];
const sha256 = (value) => createHash("sha256").update(String(value ?? "")).digest("hex");

export function summarizeDraft(draft) {
  const keys = Object.keys(draft).sort();
  return {
    fieldNames: keys,
    summary: {present: typeof draft.summary === "string", length: [...String(draft.summary ?? "")].length, sha256: sha256(draft.summary)},
    description: {present: typeof draft.description === "string", length: [...String(draft.description ?? "")].length, sha256: sha256(draft.description)},
    assetFieldNames: keys.filter((key) => /(image|screenshot)/i.test(key)),
  };
}

export function buildMetadataPayload(draft, listing) {
  if (typeof draft.title !== "string" || typeof draft.defaultLocale !== "string") throw new Error("Draft lacks listing identity fields");
  const payload = Object.fromEntries(PRESERVED_FIELDS.filter((key) => typeof draft[key] === "string").map((key) => [key, draft[key]]));
  if ("homepageUrl" in draft) payload.homepageUrl = listing.homepageUrl;
  if ("supportUrl" in draft) payload.supportUrl = listing.supportUrl;
  return {...payload, summary: listing.summary, description: listing.description};
}

export function verifyMetadataReadback(actual, expected) {
  if (actual.summary !== expected.summary || actual.description !== expected.description) throw new Error("Metadata read-back did not exactly match canonical copy");
}
```

- [ ] **Step 6: Run focused tests and commit**

Run: `cd extension && npm test -- --run tests/chrome-webstore-metadata.test.ts`

Expected: all metadata library tests PASS.

```bash
git add extension/scripts/chrome-webstore-metadata-lib.mjs extension/tests/chrome-webstore-metadata.test.ts
git commit -m "feat: add Chrome Web Store metadata contract"
```

### Task 2: Ordered, testable metadata API CLI

**Files:**
- Create: `extension/scripts/chrome-webstore-metadata.mjs`
- Modify: `extension/tests/chrome-webstore-metadata.test.ts`

**Interfaces:**
- Consumes the five exports from Task 1.
- Produces: `parseArgs(argv: string[]): MetadataOptions`
- Produces: `runMetadataCommand({options, env, fetchImpl, log}): Promise<Record<string, unknown>>`
- CLI flags: `--listing <path> --mode <probe|apply> [--replace-pending] [--publish]`.

- [ ] **Step 1: Write failing orchestration tests with an in-memory fetch stub**

```ts
it("probe performs only token exchange and one v1.1 GET", async () => {
  const calls: Array<{url: string; method: string}> = [];
  const fetchImpl = fakeFetch(calls, [oauthResponse(), draftResponse()]);
  await runMetadataCommand({options: probeOptions, env: oauthEnv, fetchImpl, log: () => {}});
  expect(calls.map((call) => call.method)).toEqual(["POST", "GET"]);
  expect(calls.some((call) => /cancelSubmission|:publish/.test(call.url))).toBe(false);
});

it("apply cancels only after probe, verifies PUT, then publishes", async () => {
  const calls = [];
  const fetchImpl = fakeFetch(calls, [oauthResponse(), draftResponse(), statusResponse("PENDING_REVIEW"), okResponse(), okResponse(), updatedDraftResponse(), okResponse(), statusResponse("PENDING_REVIEW")]);
  await runMetadataCommand({options: {...applyOptions, replacePending: true, publish: true}, env: oauthEnv, fetchImpl, log: () => {}});
  expect(calls.map(({url, method}) => `${method} ${url.split("?")[0].split("/").pop()}`)).toEqual([
    "POST token", "GET item-id", "GET publishers/publisher-id/items/item-id:fetchStatus", "POST publishers/publisher-id/items/item-id:cancelSubmission", "PUT item-id", "GET item-id", "POST publishers/publisher-id/items/item-id:publish", "GET publishers/publisher-id/items/item-id:fetchStatus",
  ]);
});
```

- [ ] **Step 2: Run focused tests and confirm missing exports**

Run: `cd extension && npm test -- --run tests/chrome-webstore-metadata.test.ts`

Expected: FAIL because `runMetadataCommand` and the CLI do not exist.

- [ ] **Step 3: Implement bounded request, OAuth, draft/status operations, and CLI order**

```js
export async function runMetadataCommand({options, env, fetchImpl = fetch, log = console.log}) {
  const canonical = parseListingMarkdown(await readFile(options.listing, "utf8"));
  validateListingMetadata(canonical);
  const accessToken = await getAccessToken(env, fetchImpl);
  const draft = await getDraft(env.extensionId, accessToken, fetchImpl);
  const probe = summarizeDraft(draft);
  log(JSON.stringify({operation: "probe", ...probe}));
  if (!probe.summary.present || !probe.description.present) throw new Error("Draft does not expose writable metadata fields");
  if (options.mode === "probe") return {probe};

  const statusBefore = await fetchStatus(env, accessToken, fetchImpl);
  if (isPendingReview(statusBefore)) {
    if (!options.replacePending) throw new Error("Submission is pending review; pass --replace-pending to cancel it");
    await cancelSubmission(env, accessToken, fetchImpl);
  }
  await putDraft(env.extensionId, accessToken, buildMetadataPayload(draft, canonical), fetchImpl);
  const readback = await getDraft(env.extensionId, accessToken, fetchImpl);
  verifyMetadataReadback(readback, canonical);
  if (!options.publish) return {probe, updated: true, published: false};
  await publish(env, accessToken, fetchImpl);
  const statusAfter = await fetchStatus(env, accessToken, fetchImpl);
  if (!isPendingReview(statusAfter)) throw new Error("Metadata updated but publish status is not pending review");
  return {probe, updated: true, published: true, reviewState: findReviewState(statusAfter)};
}
```

Each request uses an `AbortSignal.timeout(30_000)` signal and retries once only for HTTP 429 or 5xx. Error messages include the status and endpoint operation name, never request headers or OAuth payloads.

- [ ] **Step 4: Add tests for schema failure, no-replace pending state, retry limits, and log redaction**

```ts
it("stops before status or cancellation when probe schema is incomplete", async () => {
  const calls = [];
  await expect(runMetadataCommand({options: applyOptions, env: oauthEnv, fetchImpl: fakeFetch(calls, [oauthResponse(), jsonResponse({title: "OpenBiliClaw"})]), log: () => {}})).rejects.toThrow("writable metadata");
  expect(calls).toHaveLength(2);
});

it("does not cancel a pending review without explicit authorization", async () => {
  const calls = [];
  await expect(runMetadataCommand({options: applyOptions, env: oauthEnv, fetchImpl: fakeFetch(calls, [oauthResponse(), draftResponse(), statusResponse("PENDING_REVIEW")]), log: () => {}})).rejects.toThrow("--replace-pending");
  expect(calls.some(({url}) => url.includes("cancelSubmission"))).toBe(false);
});
```

- [ ] **Step 5: Run metadata and existing upload tests, then commit**

Run: `cd extension && npm test -- --run tests/chrome-webstore-metadata.test.ts tests/chrome-webstore-pending.test.ts`

Expected: all focused metadata and pending-review tests PASS.

```bash
git add extension/scripts/chrome-webstore-metadata.mjs extension/tests/chrome-webstore-metadata.test.ts
git commit -m "feat: automate Chrome Web Store listing metadata"
```

### Task 3: Safe Actions entry point and mandatory documentation

**Files:**
- Create: `.github/workflows/update-chrome-webstore-listing.yml`
- Modify: `tests/test_github_workflows.py`
- Modify: `docs/chrome-webstore-listing.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes the Task 2 CLI.
- Workflow inputs: `mode` choice (`probe` default, `apply` optional), `replace_pending` boolean default `false`, `publish` boolean default `false`.
- Uses the five existing `CHROME_WEBSTORE_*` secrets; no ZIP artifact or extension build step.

- [ ] **Step 1: Write a failing workflow contract test**

```python
def test_chrome_webstore_listing_workflow_is_probe_first_and_never_uploads_a_zip() -> None:
    workflow = Path(".github/workflows/update-chrome-webstore-listing.yml").read_text(encoding="utf-8")
    assert 'default: "probe"' in workflow
    assert "--mode \"$MODE\"" in workflow
    assert "args+=(--replace-pending)" in workflow
    assert "args+=(--publish)" in workflow
    assert "CHROME_WEBSTORE_REFRESH_TOKEN: ${{ secrets.CHROME_WEBSTORE_REFRESH_TOKEN }}" in workflow
    assert "chrome-webstore-metadata.mjs" in workflow
    assert "chrome-webstore-upload.mjs" not in workflow
    assert "npm run package" not in workflow
```

- [ ] **Step 2: Run the contract test and confirm the workflow is missing**

Run: `pytest tests/test_github_workflows.py -q`

Expected: FAIL with `FileNotFoundError` for the new workflow.

- [ ] **Step 3: Add the dedicated manual workflow**

```yaml
name: Update Chrome Web Store Listing

on:
  workflow_dispatch:
    inputs:
      mode:
        type: choice
        options: [probe, apply]
        default: "probe"
      replace_pending:
        type: boolean
        default: false
      publish:
        type: boolean
        default: false

permissions:
  contents: read

jobs:
  metadata:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-node@v6
        with:
          node-version: "22"
      - name: Probe or update listing metadata
        env:
          CHROME_WEBSTORE_CLIENT_ID: ${{ secrets.CHROME_WEBSTORE_CLIENT_ID }}
          CHROME_WEBSTORE_CLIENT_SECRET: ${{ secrets.CHROME_WEBSTORE_CLIENT_SECRET }}
          CHROME_WEBSTORE_REFRESH_TOKEN: ${{ secrets.CHROME_WEBSTORE_REFRESH_TOKEN }}
          CHROME_WEBSTORE_PUBLISHER_ID: ${{ secrets.CHROME_WEBSTORE_PUBLISHER_ID }}
          CHROME_WEBSTORE_EXTENSION_ID: ${{ secrets.CHROME_WEBSTORE_EXTENSION_ID }}
          MODE: ${{ inputs.mode }}
          REPLACE_PENDING: ${{ inputs.replace_pending }}
          PUBLISH: ${{ inputs.publish }}
        run: |
          args=(--listing docs/chrome-webstore-listing.md --mode "$MODE")
          if [ "$REPLACE_PENDING" = "true" ]; then args+=(--replace-pending); fi
          if [ "$PUBLISH" = "true" ]; then args+=(--publish); fi
          node extension/scripts/chrome-webstore-metadata.mjs "${args[@]}"
```

- [ ] **Step 4: Document operation, sunset, recovery, and screenshot boundary**

Add to `docs/chrome-webstore-listing.md` and `docs/modules/extension.md`:

```markdown
### Metadata API bridge

`.github/workflows/update-chrome-webstore-listing.yml` defaults to a read-only probe. `apply` validates the probe, optionally cancels a pending review, writes the canonical summary/description through API v1.1, reads the draft back exactly, and only then calls API v2 publish. API v1.1 is deprecated and scheduled to stop working on 2026-10-15. The bridge never uploads a ZIP or screenshots; screenshots still require the Developer Dashboard.
```

Add an Unreleased changelog entry naming the CLI, workflow, tests, v1.1 sunset, and unsupported screenshot upload.

- [ ] **Step 5: Run workflow/docs tests and commit**

Run: `pytest tests/test_github_workflows.py tests/test_chrome_webstore_listing.py -q`

Expected: all workflow and listing-document tests PASS.

```bash
git add .github/workflows/update-chrome-webstore-listing.yml tests/test_github_workflows.py docs/chrome-webstore-listing.md docs/modules/extension.md docs/changelog.md
git commit -m "ci: add Chrome Web Store metadata workflow"
```

### Task 4: Full verification, push, live probe, and guarded apply

**Files:**
- Verify only; modify earlier task files only if verification finds a defect.

**Interfaces:**
- Consumes the workflow and CLI from Tasks 1–3.
- Produces an Actions run proving the v1.1 draft schema before any mutation, followed only on success by an apply/publish run.

- [ ] **Step 1: Run repository verification**

```bash
cd extension && npm test -- --run && npm run typecheck && npm run build
cd .. && ruff check src tests scripts && pytest -q
```

Expected: all extension tests, typecheck, build, Ruff, and Python tests PASS.

- [ ] **Step 2: Inspect the final diff for secrets and unintended package/release changes**

```bash
git diff --check main...HEAD
git diff --stat main...HEAD
rg -n "access_token|client_secret|refresh_token" extension/scripts/chrome-webstore-metadata.mjs .github/workflows/update-chrome-webstore-listing.yml
git diff main...HEAD -- extension/manifest.json extension/package.json .github/workflows/release.yml
```

Expected: clean diff check, only variable/secret references (no values), and no version/package/release modifications.

- [ ] **Step 3: Merge to main and push after branch-completion verification**

Use `superpowers:finishing-a-development-branch`, merge the verified feature branch into local `main`, re-run focused tests, and then:

```bash
git push origin main
```

Expected: `origin/main` contains the metadata automation without touching release tags.

- [ ] **Step 4: Dispatch and inspect the read-only probe**

```bash
gh workflow run update-chrome-webstore-listing.yml --ref main -f mode=probe -f replace_pending=false -f publish=false
gh run list --workflow update-chrome-webstore-listing.yml --limit 1
gh run watch <probe-run-id> --exit-status
gh run view <probe-run-id> --log
```

Expected: the log contains a redacted probe with `summary.present=true` and `description.present=true`; it contains no cancel, PUT, or publish operation. If not, stop here.

- [ ] **Step 5: Dispatch apply only after the probe succeeds**

```bash
gh workflow run update-chrome-webstore-listing.yml --ref main -f mode=apply -f replace_pending=true -f publish=true
gh run list --workflow update-chrome-webstore-listing.yml --limit 1
gh run watch <apply-run-id> --exit-status
gh run view <apply-run-id> --log
```

Expected: exact metadata read-back succeeds and the final v2 status is `PENDING_REVIEW` or its current equivalent. If publish fails after read-back, report “draft updated, not submitted” and do not upload a ZIP.

- [ ] **Step 6: Report the screenshot boundary accurately**

Report the listing description as updated and submitted only when Step 5 proves it. Report the five PNGs under `docs/images/chrome-web-store/` as prepared but not live because no supported screenshot API was used.
