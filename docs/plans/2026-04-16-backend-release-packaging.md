# Backend Release Packaging Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a tag-driven GitHub Release pipeline that automatically builds and uploads macOS and Windows backend desktop packages.

**Architecture:** Keep the existing PyInstaller-based backend packaging entrypoints under `packaging/`, make the build script release-friendly and testable, and add a dedicated GitHub Actions workflow that builds per-platform archives and publishes them to Releases. Update user-facing docs so the backend download path matches the extension’s Release-based distribution.

**Tech Stack:** Python 3.11+, PyInstaller, GitHub Actions, Markdown docs

---

### Task 1: Make backend packaging deterministic and testable

**Files:**
- Modify: `packaging/build.py`
- Test: `tests/test_packaging_build.py`

**Step 1: Write the failing tests**

Add tests for the packaging helper behavior that the release workflow will rely on:

```python
def test_archive_name_includes_platform_and_version() -> None:
    assert build_module.make_archive_name("v0.1.1", "macos") == "OpenBiliClaw-macos-v0.1.1.zip"


def test_find_packaged_root_prefers_app_bundle_on_macos(tmp_path: Path) -> None:
    (tmp_path / "OpenBiliClaw.app").mkdir()
    (tmp_path / "OpenBiliClaw").mkdir()
    assert build_module.find_packaged_root(tmp_path, platform_name="Darwin") == tmp_path / "OpenBiliClaw.app"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_packaging_build.py -q`
Expected: FAIL because the helper functions do not exist yet.

**Step 3: Write minimal implementation**

Refactor `packaging/build.py` so it exposes small helpers the workflow can rely on:

- `find_packaged_root(dist_dir, platform_name=None) -> Path`
- `make_archive_name(version, target) -> str`
- optional archive creation helper that zips the packaged root into a caller-provided output path

Keep the existing `python packaging/build.py --clean` behavior intact for local maintainers.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_packaging_build.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add packaging/build.py tests/test_packaging_build.py
git commit -m "feat: make backend packaging release-friendly"
```

### Task 2: Add automated GitHub Release publishing for backend packages

**Files:**
- Create: `.github/workflows/release-backend.yml`
- Modify: `packaging/build.py`

**Step 1: Write the failing verification target**

Define the expected workflow shape up front in the file itself:

- trigger on `push.tags: v*`
- build on `macos-latest` and `windows-latest`
- call the packaging script
- create versioned zip archives
- publish them to the matching GitHub Release

Because this is workflow configuration, use a targeted structure check rather than a Python unit test:

Run: `rg -n "push:|tags:|macos-latest|windows-latest|upload-release-asset|gh release upload|softprops/action-gh-release" .github/workflows/release-backend.yml`
Expected: FAIL because the workflow file does not exist yet.

**Step 2: Run verification to confirm failure**

Run: `test -f .github/workflows/release-backend.yml`
Expected: FAIL (non-zero exit)

**Step 3: Write minimal implementation**

Create a dedicated release workflow that:

- runs only on version tags
- installs Python and project dependencies
- installs PyInstaller
- runs `python packaging/build.py --clean`
- archives the correct packaged root for each OS
- names archives with platform + tag
- publishes them to the Release attached to the tag

Prefer the built-in `GITHUB_TOKEN`; do not require manual upload steps.

**Step 4: Run verification to verify it passes**

Run: `sed -n '1,240p' .github/workflows/release-backend.yml`
Expected: Contains the tag trigger, both OS jobs, archive step, and release publish step.

**Step 5: Commit**

```bash
git add .github/workflows/release-backend.yml packaging/build.py
git commit -m "feat: automate backend release packaging"
```

### Task 3: Update user-facing release docs

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/index.md`
- Modify: `docs/changelog.md`

**Step 1: Write the failing doc verification**

Decide the exact strings users should be able to find:

- README 中文包含“从 Releases 下载后端”
- README 英文包含 “download the backend from Releases”
- docs index can point to the Release-based backend distribution path

Run:

```bash
rg -n "Releases|release|下载后端|download the backend" README.md README_EN.md docs/index.md docs/changelog.md
```

Expected: FAIL to find the new backend release wording in the right places.

**Step 2: Run verification to confirm failure**

Run the `rg` command above.
Expected: Existing hits mention extension release only, not backend release distribution.

**Step 3: Write minimal implementation**

Update docs so that:

- plugin and backend both point users to GitHub Releases where appropriate
- source-install / Docker / `install.sh` paths remain available as alternatives
- changelog records the new automated backend release capability

Keep wording explicit that the first release packages are unsigned and may show OS security warnings.

**Step 4: Run verification to verify it passes**

Run:

```bash
rg -n "Releases|release|下载后端|download the backend|未签名|unsigned" README.md README_EN.md docs/index.md docs/changelog.md
```

Expected: PASS with clear backend release references.

**Step 5: Commit**

```bash
git add README.md README_EN.md docs/index.md docs/changelog.md
git commit -m "docs: add backend release download guidance"
```

### Task 4: Run targeted verification for the release packaging change

**Files:**
- Verify: `tests/test_packaging_build.py`
- Verify: `.github/workflows/release-backend.yml`
- Verify: `README.md`
- Verify: `README_EN.md`
- Verify: `docs/index.md`
- Verify: `docs/changelog.md`

**Step 1: Run the packaging unit tests**

Run: `uv run pytest tests/test_packaging_build.py -q`
Expected: PASS

**Step 2: Run the packaging build locally on the current platform**

Run: `uv run python packaging/build.py --clean`
Expected: PASS and generate a local packaged backend under `dist/`

**Step 3: Verify the workflow file and docs together**

Run:

```bash
rg -n "push:|tags:|macos-latest|windows-latest|softprops/action-gh-release|Releases|下载后端|download the backend" \
  .github/workflows/release-backend.yml README.md README_EN.md docs/index.md docs/changelog.md
```

Expected: PASS with the intended release automation and user guidance.

**Step 4: Record the known unrelated baseline failures**

Run: `uv run pytest -q`
Expected: Still shows the existing unrelated failures outside packaging scope; do not treat them as regressions introduced by this work.

**Step 5: Commit**

```bash
git add .
git commit -m "feat: publish backend packages via github releases"
```
