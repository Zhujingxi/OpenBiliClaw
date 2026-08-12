# Chrome Web Store listing

Maintainer source for the current reduced extension listing.

## Links

- Store item: <https://chromewebstore.google.com/detail/openbiliclaw/cdfjfkdjjhdaccbldipkjhpibnfbiamg>
- Project: <https://whiteguo233.github.io/OpenBiliClaw/>
- Support: <https://github.com/whiteguo233/OpenBiliClaw/issues>
- Privacy: <https://github.com/whiteguo233/OpenBiliClaw/blob/main/docs/privacy.md>

## Short description

```text
OpenBiliClaw local backend companion: private cross-source recommendations, profile, and Assistant.
```

## Detailed description

```text
OpenBiliClaw is a local-first, open-source personalized content discovery application. This extension is its compact browser-side presentation client.

It connects only to the OpenBiliClaw backend address configured by the user, shows typed recommendation/profile/Assistant data, and stores the backend URL plus an opaque device token.

The extension does not read website Cookie values, collect browsing behavior, inject provider task scripts, execute cross-site content requests, or serve models. Content providers and external model services are connected by the backend through their explicit typed contracts.

Supported backend content providers include B站, 小红书, 抖音, YouTube, X, 知乎, Reddit, Linux.do, Bangumi, and V2EX. Individual capabilities depend on each provider's current manifest; degraded providers fail closed rather than emulating unsupported access.

User data remains in the configured local backend data directory. OpenBiliClaw does not send it to a developer-operated analytics service. A user-configured external model service receives only the bounded content needed for that model request.

Install and start the backend first, then open the extension and configure its backend URL/device token.
```

## Privacy declarations

- Data usage: backend connection URL and opaque device token only.
- Host access: configured local backend only.
- No website Cookie access.
- No browsing-history or page-content collection.
- No advertising, analytics, sale, or unrelated transfer of user data.
- No provider task/background-tab execution.

## Screenshot upload order

Upload these 1280×800 PNG files in this order:

1. `01-seven-platform-recommendations.png`
2. `02-three-surfaces.png`
3. `03-truthful-status-local-data.png`

Repository path: `docs/images/chrome-web-store/`.

Generate/update the reviewed local demo assets with:

```bash
python scripts/capture_chrome_webstore_ui.py \
  --output-dir docs/images/chrome-web-store/source
```

The screenshot scripts must use a local, synthetic, redacted demo service. Never use a real `config.toml`, credential store, database, Cookie, account identity, or user profile to create store material. Homepage screenshots under `docs/images/` are separate assets and must not be overwritten by this workflow.
