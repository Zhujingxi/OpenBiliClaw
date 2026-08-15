# YouTube Content Provider

`content/providers/youtube/` provides anonymous search, single-video fetch, channel creator pages, strict projections, and Google Takeout parsing. Production Composition registers it under provider id `youtube`; no API key or account cookie is required for public reads.

`YtDlpYouTubeTransport` delegates YouTube extraction to the actively maintained `yt-dlp` library instead of pinning private InnerTube keys, client versions, or renderer shapes in OpenBiliClaw. Its synchronous extraction API runs through `anyio.to_thread`; search and creator pages use flat extraction while fetch resolves complete metadata. Only normalized identity/title/channel/date/duration/view/thumbnail fields enter strict `YouTubePage` models. Results are bounded to 50 and one-shot, so `next_cursor` is honestly absent rather than fabricated. yt-dlp/network failures are converted to safe typed integration errors without exposing upstream response bodies.

YouTube removed its generic Trending page in July 2025. The provider therefore does **not** advertise `FEED`: mapping it to search or music-only charts would misrepresent product semantics. Search, fetch, creator, and projection remain available; generic discovery must consult the manifest rather than schedule a feed job for this provider.

`takeout.py` reads an extracted directory or zip containing watch-history HTML/JSON, subscriptions CSV, and liked-videos CSV into typed parse results. Missing files allow partial import and schema errors produce safe warnings. Production `openbiliclaw import youtube TAKEOUT_PATH` now admits watch history through the external-evidence workflow; subscriptions and likes remain parser-visible but are reported as ignored rather than being mislabeled as saves.

Browser-session access, write actions, and media downloads are outside the manifest.
