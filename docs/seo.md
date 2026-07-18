# GitHub Pages SEO maintenance

The public landing page is [`docs/index.html`](index.html), published at
<https://whiteguo233.github.io/OpenBiliClaw/>. Its title, description and Open Graph
copy must describe the current vNext product surface only.

## Search-console setup

1. Add `https://whiteguo233.github.io/OpenBiliClaw/` as a URL-prefix property in
   Google Search Console or Bing Webmaster Tools.
2. Put the verified ownership value in the corresponding commented meta tag in
   `docs/index.html`. Do not commit placeholder or unverified values.
3. Submit `https://whiteguo233.github.io/OpenBiliClaw/sitemap.xml`.
4. Request indexing for the landing-page URL after deployment.

GitHub Pages serves this repository from a subpath, so the repository cannot control
`https://whiteguo233.github.io/robots.txt`. [`robots.txt`](robots.txt) is retained as a
portable copy; explicit sitemap submission is the reliable discovery path.

## Release checklist

When product framing, supported sources or installation changes:

- update the title, description and Open Graph text in `docs/index.html`;
- keep claims aligned with `README.md`, `README_EN.md` and `docs/spec.md`;
- update `<lastmod>` in [`sitemap.xml`](sitemap.xml) to the release date;
- deploy Pages and verify the homepage and sitemap return `200`;
- run Lighthouse or another HTML/SEO validator after deployment.

The landing page intentionally has no version-specific JSON-LD or legacy screenshots,
so a code release does not publish stale runtime or UI claims through structured data.
