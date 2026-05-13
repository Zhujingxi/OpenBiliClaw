# GitHub Pages Homepage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and deploy a concise GitHub Pages homepage for OpenBiliClaw from the existing video-intro content, with installation, extension, and GitHub links visible in the first viewport.

**Architecture:** Use a static `docs/index.html` served by GitHub Pages from the repository `main` branch `/docs` directory. Keep existing markdown documentation available via `docs/index.md`, and use only repo-local assets from `docs/images/` so the site works without a frontend build step.

**Tech Stack:** Static HTML/CSS/JavaScript, GitHub Pages, existing PNG screenshots.

---

### Task 1: Static Homepage

**Files:**
- Create: `docs/index.html`
- Create: `docs/.nojekyll`
- Keep: `docs/index.md`

**Steps:**
1. Add a static landing page with hero, CTAs, install prompt, value props, learning loop, source support, screenshots, and technical summary.
2. Link primary CTAs to GitHub repository, latest extension release, and the README install section.
3. Reference screenshots using local `images/*.png` paths.

### Task 2: Local Verification

**Files:**
- Verify: `docs/index.html`

**Steps:**
1. Serve `docs/` on localhost with `python3 -m http.server`.
2. Use Playwright to check console errors, desktop screenshot, mobile screenshot, and horizontal overflow.
3. Fix any layout, contrast, broken image, or broken link issue found locally.

### Task 3: Publish

**Files:**
- Commit: `docs/index.html`, `docs/.nojekyll`, this plan.

**Steps:**
1. Commit the homepage change.
2. Push `main`.
3. Enable GitHub Pages with `main` + `/docs`.
4. Set repository homepage URL to the Pages URL.
5. Read back Pages configuration and repo metadata.
