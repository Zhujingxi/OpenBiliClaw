# Split Release Channels Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split plugin and backend publishing into separate GitHub Release channels using `extension-v*` and `backend-v*` tags without rewriting release history.

**Architecture:** Keep the repository unified, but split release automation by tag prefix. Backend keeps using the PyInstaller packaging path with tag normalization, while the extension gets its own workflow and a small packaging helper that understands prefixed release tags.

**Tech Stack:** GitHub Actions, Python packaging script, Node extension packaging script, Markdown docs

---

### Task 1: Normalize Release Tag Versions In Packaging Helpers

**Files:**
- Create: `extension/scripts/release-utils.mjs`
- Create: `extension/tests/release-utils.test.ts`
- Modify: `extension/scripts/package.mjs`
- Modify: `packaging/build.py`
- Modify: `tests/test_packaging_build.py`

**Step 1: Write the failing tests**

Add Python coverage for prefixed backend tags:

```python
def test_make_bundle_version_strips_release_channel_prefix() -> None:
    assert build.make_bundle_version("backend-v0.1.3") == "0.1.3"


def test_make_archive_name_uses_user_facing_version() -> None:
    assert build.make_archive_name("backend-v0.1.3", "macos") == "OpenBiliClaw-macos-v0.1.3.zip"
```

Add Node coverage for prefixed extension tags:

```ts
test("normalizeReleaseVersion strips extension channel prefix", () => {
  assert.equal(normalizeReleaseVersion("extension-v0.1.3"), "v0.1.3");
});

test("makeExtensionArchiveName keeps user-facing version only", () => {
  assert.equal(
    makeExtensionArchiveName("extension-v0.1.3"),
    "openbiliclaw-extension-v0.1.3.zip",
  );
});
```

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_packaging_build.py -q
cd extension && node --test --experimental-strip-types tests/release-utils.test.ts
```

Expected: failures because prefixed tag normalization does not exist yet.

**Step 3: Write the minimal implementation**

Implement:

- a Python helper that reduces `backend-v0.1.3` to `v0.1.3` / `0.1.3` depending on output context
- a small extension-side helper module exporting:

```js
export function normalizeReleaseVersion(tagOrVersion) {
  // "extension-v0.1.3" -> "v0.1.3"
}

export function makeExtensionArchiveName(tagOrVersion) {
  return `openbiliclaw-extension-${normalizeReleaseVersion(tagOrVersion)}.zip`;
}
```

- update `package.mjs` to use the helper while preserving the current local default path that reads `manifest.json`

**Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_packaging_build.py -q
cd extension && node --test --experimental-strip-types tests/release-utils.test.ts
```

Expected: both pass.

**Step 5: Commit**

```bash
git add extension/scripts/release-utils.mjs extension/tests/release-utils.test.ts extension/scripts/package.mjs packaging/build.py tests/test_packaging_build.py
git commit -m "feat: normalize release channel tags"
```

### Task 2: Split GitHub Release Workflows By Channel

**Files:**
- Create: `.github/workflows/release-extension.yml`
- Modify: `.github/workflows/release-backend.yml`

**Step 1: Write the failing expectation as a checklist**

Create a local checklist in comments / notes before editing:

- backend workflow triggers only on `backend-v*`
- backend workflow still uploads macOS and Windows packages
- extension workflow triggers only on `extension-v*`
- extension workflow builds and uploads one extension zip

**Step 2: Run a quick inspection to capture current state**

Run:

```bash
sed -n '1,220p' .github/workflows/release-backend.yml
ls .github/workflows
```

Expected: only a backend workflow exists and it still listens on `v*`.

**Step 3: Write the minimal implementation**

Backend workflow changes:

- change trigger from `v*` to `backend-v*`
- keep build / verify / upload structure
- continue passing the full tag into `packaging/build.py`

Extension workflow contents:

- trigger on `extension-v*`
- `actions/checkout`
- `actions/setup-node`
- `npm ci` in `extension/`
- `npm run package`
- verify a single `openbiliclaw-extension-v*.zip` exists
- upload artifact
- publish a GitHub Release containing only that zip

**Step 4: Run local verification**

Run:

```bash
sed -n '1,220p' .github/workflows/release-backend.yml
sed -n '1,240p' .github/workflows/release-extension.yml
```

Expected: backend and extension release channels are fully separated.

**Step 5: Commit**

```bash
git add .github/workflows/release-backend.yml .github/workflows/release-extension.yml
git commit -m "feat: split extension and backend release workflows"
```

### Task 3: Update Download Documentation For Separate Channels

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/index.md`
- Modify: `docs/changelog.md`
- Modify: `docs/modules/extension.md`

**Step 1: Write the failing documentation expectation**

Capture the old behavior:

- plugin docs point to `releases/latest`
- backend docs point to `releases/latest`
- docs do not explain channel-specific tags

**Step 2: Verify the old wording**

Run:

```bash
rg -n "releases/latest|插件|后端|extension" README.md README_EN.md docs/index.md docs/changelog.md docs/modules/extension.md
```

Expected: mixed "latest release" wording appears in multiple files.

**Step 3: Write the minimal documentation update**

Update wording to explain:

- plugin downloads come from extension releases
- backend downloads come from backend releases
- historical mixed releases remain, but new releases follow channel tags
- unsigned backend packages may still trigger OS warnings

**Step 4: Run verification**

Run:

```bash
rg -n "releases/latest|extension-v|backend-v|GitHub Releases" README.md README_EN.md docs/index.md docs/changelog.md docs/modules/extension.md
```

Expected: no ambiguous mixed download entry remains.

**Step 5: Commit**

```bash
git add README.md README_EN.md docs/index.md docs/changelog.md docs/modules/extension.md
git commit -m "docs: clarify split release channels"
```

### Task 4: End-To-End Verification Of The New Release Channel Split

**Files:**
- Modify: none
- Verify: `.github/workflows/release-backend.yml`
- Verify: `.github/workflows/release-extension.yml`
- Verify: `packaging/build.py`
- Verify: `extension/scripts/package.mjs`

**Step 1: Run relevant automated tests**

Run:

```bash
uv run pytest tests/test_packaging_build.py -q
cd extension && npm test
```

Expected: relevant release-related tests pass.

**Step 2: Run local packaging commands**

Run:

```bash
uv run python packaging/build.py --clean --archive-version backend-v0.1.3
cd extension && npm run package
```

Expected:

- backend zip output is named `OpenBiliClaw-macos-v0.1.3.zip` on macOS
- extension zip output is named `openbiliclaw-extension-v0.1.0.zip` locally unless a prefixed release tag override is provided

**Step 3: Inspect git diff and status**

Run:

```bash
git status --short
git diff --stat
```

Expected: only release-channel split files are changed.

**Step 4: Commit final verification-safe state**

```bash
git add -A
git commit -m "chore: finalize split release channel rollout"
```
