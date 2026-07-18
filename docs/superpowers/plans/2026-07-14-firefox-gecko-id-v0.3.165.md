# Firefox Gecko ID and v0.3.165 Signed XPI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the Firefox package to a Gecko ID owned by the current AMO account and publish a traceable v0.3.165 signed XPI through both GitHub Release surfaces.

**Architecture:** Keep Chrome identity and runtime code unchanged. Lock the Firefox-only Gecko ID in the source manifest and an automated test, use `scripts/release.py` for all mechanical version fields, then publish one verified release commit through the existing tag-triggered workflows with AMO signing required.

**Tech Stack:** Firefox Manifest V3, Node test runner, `web-ext` 10, Mozilla AMO unlisted signing, Python release helper, uv, Ruff, MyPy, GitHub Actions, GitHub Releases.

## Global Constraints

- Firefox Gecko ID is exactly `openbiliclaw-firefox@whiteguo233.github.io`.
- Backend, extension, desktop, and aggregate release versions are exactly `0.3.165`.
- Do not modify Chrome's extension identity or Chrome Web Store listing.
- Do not rewrite or move any v0.3.164 tag or asset.
- The installable asset name is exactly `openbiliclaw-extension-v0.3.165-firefox.xpi`.
- AMO signing uses `web-ext sign --channel=unlisted`; an AMO failure fails the extension release.
- The unsigned `-firefox.zip` remains a development/temporary-load package and is never renamed to `.xpi`.
- README Chinese and English release callouts have the same one-item content and order.
- Architecture, CLI, config, and installer-flow documentation remain unchanged because no runtime wiring or user configuration changes.
- Preserve the pre-existing untracked `.playwright-cli/` directory.

---

### Task 1: Lock the Firefox package to the owned Gecko ID

**Files:**
- Modify: `extension/tests/manifest-assets.test.ts`
- Modify: `extension/manifest.firefox.json`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes: `browser_specific_settings.gecko.id` from `extension/manifest.firefox.json`.
- Produces: immutable Firefox identity `openbiliclaw-firefox@whiteguo233.github.io`, guarded by the extension test suite.

- [ ] **Step 1: Add the failing Gecko ID regression test**

Add this test to `extension/tests/manifest-assets.test.ts`:

```ts
test("Firefox manifest uses the project-owned AMO Gecko ID", () => {
  const manifest = JSON.parse(
    readFileSync(join(root, "manifest.firefox.json"), "utf8"),
  ) as {
    browser_specific_settings?: { gecko?: { id?: string } };
  };

  assert.equal(
    manifest.browser_specific_settings?.gecko?.id,
    "openbiliclaw-firefox@whiteguo233.github.io",
  );
});
```

- [ ] **Step 2: Run the targeted test and verify RED**

```bash
cd extension
node --test --experimental-strip-types tests/manifest-assets.test.ts
```

Expected: FAIL because the actual value is `openbiliclaw@whiteguo233.github.io` and the expected value is `openbiliclaw-firefox@whiteguo233.github.io`.

- [ ] **Step 3: Make the minimal manifest change**

In `extension/manifest.firefox.json`, replace only the Gecko ID:

```json
"id": "openbiliclaw-firefox@whiteguo233.github.io"
```

Do not change `strict_min_version`, `data_collection_permissions`, `gecko_android`, permissions, or host permissions.

- [ ] **Step 4: Run the targeted test and verify GREEN**

```bash
cd extension
node --test --experimental-strip-types tests/manifest-assets.test.ts
```

Expected: all tests in `manifest-assets.test.ts` pass.

- [ ] **Step 5: Synchronize extension module documentation**

Update the `Firefox 140+ 支持` row in `docs/modules/extension.md` to state that the unlisted AMO package uses stable Gecko ID `openbiliclaw-firefox@whiteguo233.github.io`, that the AMO account owning the ID must supply the signing credentials, and that signing failure fails the release instead of silently shipping only ZIP.

- [ ] **Step 6: Record the unreleased fix in the current changelog block**

Add this bullet under the current v0.3.164 block; Task 2 will move it into the new v0.3.165 block before release:

```markdown
- **Firefox AMO 身份迁移**：Firefox manifest 的稳定 Gecko ID 改为 `openbiliclaw-firefox@whiteguo233.github.io`，避开旧 ID 已被其他 AMO 作者占用导致的 403；扩展测试锁定该 ID，启用签名时 AMO 失败会让 release 失败，不再把未签名 ZIP 当作正式安装包。
```

- [ ] **Step 7: Commit the identity fix**

```bash
git add extension/tests/manifest-assets.test.ts extension/manifest.firefox.json docs/modules/extension.md docs/changelog.md
git commit -m "fix(extension): move Firefox signing to owned Gecko ID"
```

Expected: one focused commit containing the test, manifest, module documentation, and changelog entry.

---

### Task 2: Prepare the v0.3.165 release source

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/openbiliclaw/__init__.py`
- Modify: `docs/index.html`
- Modify: `uv.lock`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `packaging/openbiliclaw.iss`
- Modify: `extension/manifest.json`
- Modify: `extension/package.json`
- Modify: `extension/package-lock.json`
- Modify: `docs/changelog.md`
- Existing: `docs/superpowers/specs/2026-07-14-firefox-gecko-id-design.md`
- Existing: `docs/superpowers/plans/2026-07-14-firefox-gecko-id-v0.3.165.md`

**Interfaces:**
- Consumes: the tested Firefox ID commit from Task 1 and synchronized v0.3.164 mechanical fields.
- Produces: a release commit whose enforced backend and extension fields are both `0.3.165` and whose user-facing release copy describes the signed Firefox XPI.

- [ ] **Step 1: Run the mechanical release bump**

```bash
uv run python scripts/release.py --bump 0.3.165 --extension 0.3.165
```

Expected: output contains `backend: 0.3.165` and `extension: 0.3.165`; `uv.lock` is regenerated.

- [ ] **Step 2: Create the v0.3.165 changelog block**

Move the Firefox AMO identity bullet from the v0.3.164 block into this new top block in `docs/changelog.md`:

```markdown
## v0.3.165 / extension v0.3.165 / desktop v0.3.165：Firefox 签名安装修复（2026-07-14）

后端源码走 `backend-v0.3.165`，浏览器插件走 `extension-v0.3.165`，桌面安装包走 `desktop-v0.3.165`。

- **Firefox 正式 XPI 恢复发布**：Firefox manifest 的稳定 Gecko ID 改为 `openbiliclaw-firefox@whiteguo233.github.io`，避开旧 ID 已被其他 AMO 作者占用导致的 403；扩展测试锁定该 ID，发布链路通过当前 AMO 账号做 unlisted 签名并要求产出 `openbiliclaw-extension-v0.3.165-firefox.xpi`，签名失败会直接阻止 release。
```

- [ ] **Step 3: Replace the bilingual README release callouts**

Set the Chinese block to:

```markdown
📌 最新版本：**v0.3.165（2026-07-14）**

- **Firefox 可直接安装正式 XPI** —— Firefox 扩展迁移到项目自有 AMO 身份并恢复 Mozilla 签名发布，普通 Firefox Release / Beta 可从 Latest Release 直接安装。

完整变更详见 [docs/changelog.md](docs/changelog.md)。
```

Set the English block to:

```markdown
📌 Latest: **v0.3.165 (2026-07-14)**

- **Firefox now has a directly installable XPI** — the Firefox extension moved to a project-owned AMO identity and resumes Mozilla-signed releases for regular Firefox Release and Beta.

Full changelog: [docs/changelog.md](docs/changelog.md).
```

- [ ] **Step 4: Verify mechanical release consistency**

```bash
uv run python scripts/release.py --check
uv run pytest tests/test_release_script.py tests/test_release_consistency.py tests/test_aggregate_release_workflow.py tests/test_github_workflows.py -q
```

Expected: both commands exit 0 and report backend/extension `0.3.165`.

- [ ] **Step 5: Inspect the release diff and commit**

```bash
git diff --check
git diff --stat
git status --short
git add pyproject.toml src/openbiliclaw/__init__.py docs/index.html uv.lock README.md README_EN.md packaging/openbiliclaw.iss extension/manifest.json extension/package.json extension/package-lock.json docs/changelog.md docs/superpowers/plans/2026-07-14-firefox-gecko-id-v0.3.165.md
git commit -m "chore(release): v0.3.165"
```

Expected: release fields and documentation are committed; `.playwright-cli/` remains untracked and unstaged.

---

### Task 3: Run full local verification and merge to main

**Files:**
- Verify: all files changed by Tasks 1 and 2

**Interfaces:**
- Consumes: the feature and release commits on `codex/firefox-gecko-id-v0.3.165`.
- Produces: a locally and remotely verified `main` release commit suitable for immutable tags.

- [ ] **Step 1: Run the complete extension verification**

```bash
cd extension
npm ci
npm test
npm run typecheck
npm run build:firefox
npx web-ext lint --source-dir=dist-firefox
```

Expected: npm tests and typecheck pass; Firefox build succeeds; `web-ext lint` exits 0 with no errors. Existing `innerHTML` warnings may remain but must not increase.

- [ ] **Step 2: Run repository verification**

```bash
cd /Users/white/workspace/OpenBiliClaw
uv run python scripts/release.py --check
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest -q
git diff --check
```

Expected: every command exits 0; pytest reports no failures.

- [ ] **Step 3: Review exact source changes**

```bash
git status --short
git diff main...HEAD --check
git diff main...HEAD --stat
git log --oneline main..HEAD
```

Expected: only the approved design, Firefox ID/test/docs, and v0.3.165 release files differ from main; `.playwright-cli/` remains untouched.

- [ ] **Step 4: Merge and push the release commit**

```bash
git switch main
git pull --ff-only origin main
git merge --no-ff codex/firefox-gecko-id-v0.3.165 -m "merge: release v0.3.165 Firefox signed XPI"
git push origin main
```

Expected: `origin/main` points to the merge commit containing both implementation commits and the design commit.

- [ ] **Step 5: Wait for main CI**

Find the main push run for the merge SHA with `gh run list --commit <merge-sha>`, then watch every required GitHub Actions run with `gh run watch <run-id> --exit-status`.

Expected: main CI completes successfully before any release tag is pushed.

---

### Task 4: Publish all v0.3.165 release channels

**Files:**
- Verify: `.github/workflows/release-backend.yml`
- Verify: `.github/workflows/release-docker.yml`
- Verify: `.github/workflows/release-extension.yml`
- Verify: `.github/workflows/release-desktop.yml`
- Verify: `.github/workflows/verify-release-completeness.yml`

**Interfaces:**
- Consumes: the green v0.3.165 merge commit on `origin/main` and repository AMO secrets.
- Produces: backend/Docker tags, signed extension assets, desktop installers, and an aggregate Latest Release at the same source SHA.

- [ ] **Step 1: Reconfirm signing configuration without reading secret values**

```bash
gh variable get FIREFOX_SIGNING_ENABLED --repo whiteguo233/OpenBiliClaw
gh secret list --repo whiteguo233/OpenBiliClaw | rg '^AMO_JWT_(ISSUER|SECRET)\b'
```

Expected: variable value `true`; both secret names are present.

- [ ] **Step 2: Create and push component tags individually**

```bash
release_sha="$(git rev-parse origin/main)"
git tag backend-v0.3.165 "$release_sha" && git push origin backend-v0.3.165
git tag extension-v0.3.165 "$release_sha" && git push origin extension-v0.3.165
git tag desktop-v0.3.165 "$release_sha" && git push origin desktop-v0.3.165
git tag openbiliclaw-v0.3.165 "$release_sha" && git push origin openbiliclaw-v0.3.165
```

Expected: all four remote tags resolve to the same release SHA. Push each tag with a separate command so GitHub emits every workflow event.

- [ ] **Step 3: Watch the extension workflow first**

Locate the `Release Extension Package` run for `extension-v0.3.165` and watch it to completion.

Expected log sequence: credentials detected, `Waiting for validation`, `Waiting for approval`, signed XPI downloaded, archives verified, extension release uploaded, aggregate release synchronized. Any HTTP 401/403/409 or missing XPI stops the release audit.

- [ ] **Step 4: Watch the remaining channel workflows**

Watch backend validation, multi-architecture Docker publishing, desktop installer publishing, aggregate synchronization, and release completeness runs for v0.3.165.

Expected: every required workflow concludes `success`. If the aggregate-tag event races before component assets, its built-in polling must eventually find the backend tag, extension ZIP/XPI release, desktop release, and aggregate assets.

---

### Task 5: Verify the signed XPI and release surfaces

**Files:**
- Inspect: downloaded `openbiliclaw-extension-v0.3.165-firefox.xpi`

**Interfaces:**
- Consumes: completed v0.3.165 GitHub Actions workflows and Release assets.
- Produces: evidence that issue #71's requested installable XPI exists, is Mozilla-signed, and contains the intended manifest identity/version.

- [ ] **Step 1: Require the exact assets in the extension release**

```bash
gh release view extension-v0.3.165 --repo whiteguo233/OpenBiliClaw --json assets --jq '.assets[].name'
```

Expected exact names include:

```text
openbiliclaw-extension-v0.3.165.zip
openbiliclaw-extension-v0.3.165-firefox.zip
openbiliclaw-extension-v0.3.165-firefox.xpi
```

- [ ] **Step 2: Require the signed XPI in the aggregate release**

```bash
gh release view openbiliclaw-v0.3.165 --repo whiteguo233/OpenBiliClaw --json assets --jq '.assets[].name'
```

Expected: `openbiliclaw-extension-v0.3.165-firefox.xpi` is present alongside same-version extension assets.

- [ ] **Step 3: Download and inspect the XPI**

```bash
rm -rf /tmp/openbiliclaw-firefox-v0.3.165
mkdir -p /tmp/openbiliclaw-firefox-v0.3.165
gh release download extension-v0.3.165 --repo whiteguo233/OpenBiliClaw --pattern 'openbiliclaw-extension-v0.3.165-firefox.xpi' --dir /tmp/openbiliclaw-firefox-v0.3.165
unzip -t /tmp/openbiliclaw-firefox-v0.3.165/openbiliclaw-extension-v0.3.165-firefox.xpi
unzip -p /tmp/openbiliclaw-firefox-v0.3.165/openbiliclaw-extension-v0.3.165-firefox.xpi manifest.json | jq '{version, gecko_id: .browser_specific_settings.gecko.id}'
unzip -l /tmp/openbiliclaw-firefox-v0.3.165/openbiliclaw-extension-v0.3.165-firefox.xpi | rg 'META-INF/(manifest\.mf|mozilla\.sf|mozilla\.rsa)'
```

Expected: ZIP integrity passes; manifest reports version `0.3.165` and Gecko ID `openbiliclaw-firefox@whiteguo233.github.io`; Mozilla signature metadata exists.

- [ ] **Step 4: Final repository and release audit**

```bash
git fetch origin --tags
git rev-parse origin/main backend-v0.3.165 extension-v0.3.165 desktop-v0.3.165 openbiliclaw-v0.3.165
git status --short --branch
```

Expected: `origin/main` and all four release tags resolve to the same intended merge commit; no tracked local changes remain and `.playwright-cli/` is still untouched. Keep issue #71 open only if any required evidence above is missing.
