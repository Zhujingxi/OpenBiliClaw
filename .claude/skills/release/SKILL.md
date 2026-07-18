---
name: release
description: Use when releasing a version, bumping project versions, or pushing release tags.
---

# Release OpenBiliClaw

Follow all six steps in order. Stop when any gate fails; never publish a partially verified
release.

## 1. Run pre-flight checks

Require a clean worktree on `main`, pull the latest commit, and run the full test suite:

```bash
git status --short
git branch --show-current
git pull --ff-only
.venv/bin/python -m pytest -q
```

Do not continue until `git status --short` is empty and the branch is `main`.

## 2. Bump mechanical versions

Use the repository tool as the only writer for mechanical version fields:

```bash
.venv/bin/python scripts/release.py --bump X.Y.Z
```

When the extension version changes, include `--extension X.Y.Z`; the flag may also be used
alone for an extension-only release. Never hand-edit mechanical version fields. The changelog
heading and README callout content in the next steps are deliberate hand edits.

## 3. Update the changelog

Hand-edit the top of `docs/changelog.md` with `## vX.Y.Z: theme (YYYY-MM-DD)` and the release's
verified changes. The final `scripts/release.py --check` warns if this heading is missing.

## 4. Replace the README callouts

Hand-edit the 📌 callouts under the recent-updates sections. Follow
[CLAUDE.md rule 10](../../../CLAUDE.md#documentation-requirements); the three hard limits are:

- Use at most four bullets.
- Replace the previous callout; never append another version callout.
- Keep `README.md` and `README_EN.md` in lockstep with the same items and order.

Then verify every mechanical field and both user-facing version headers:

```bash
.venv/bin/python scripts/release.py --check
```

## 5. Commit and push tags individually

Review the release-only diff, stage only those files, and commit:

```bash
git commit -m "chore: release X.Y.Z" -- <release-paths>
```

Create the applicable `backend-vX.Y.Z` and `extension-vX.Y.Z` channel tags, plus the aggregate
`openbiliclaw-vX.Y.Z` tag. Push **one tag per `git push`** invocation, with the aggregate tag
last so its completeness workflow can observe the component releases. A multi-tag push can omit
GitHub tag events and leave release workflows silently dead; never combine tag refspecs in one
push.

```bash
git push origin backend-vX.Y.Z
# If the extension version changed:
git push origin extension-vX.Y.Z
# Push the aggregate tag last:
git push origin openbiliclaw-vX.Y.Z
```

## 6. Verify publication

Run `gh run list --limit 10` and confirm that each pushed tag triggered exactly one run of its
corresponding backend-container, extension-package, or aggregate-completeness workflow. Inspect
failures before retrying or pushing another tag. After the container workflow succeeds, confirm
the GHCR package is public; new packages may require their visibility to be changed to public
manually.
