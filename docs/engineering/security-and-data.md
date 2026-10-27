# Security and data guardrails

## Personal use and platform terms

- **Public code, private data** ([ADR 0003](decisions/0003-public-repository.md)): the repository is public; collected listings, photos, raw pages, the database and anything copied from a platform stay on the maintainer's machine (`data/`, `.research/`). Test fixtures and test strings are synthetic: a page's structure with invented listings, prices, names, phone numbers and image URLs.
- **Storia, OLX** forbid automated collection in their terms. The tool is used only by the maintainer, at low volume and a human pace, without bypassing protections, and its data is used only by the maintainer. Do not add features that republish listings or photos, share results with others or run the collector as a service.
- **Publi24**: `robots.txt` (checked October 2026) explicitly allows crawlers including Anthropic's; listing pages are public.
- **Lajumate**: `robots.txt` allows everything except internal search (`/search?q=`), accounts and `/api/`; the adapter uses category pages and `/ad/` listing pages only. Its image service answers "Image not found" to requests that do not come from the site's own pages, so the adapter does not download Lajumate photos and does not imitate a browser context to get them.
- **Imobiliare.ro**: answers the collector with HTTP 403 and a bot verification (October 2026), so the source is disabled in the profile. Do not get past it with a browser TLS fingerprint (`curl_cffi`), a browser User-Agent, rotating proxies or a headless browser.
- **Agency sites**: check the terms when adding or changing an adapter and record them in `.research/`.
- Opening the tool to other users would need legal advice on the platforms' terms and GDPR, and preferably official APIs or partnerships. It is out of scope for this repository.

## Respecting protections

Do not circumvent technical measures a site uses to control access: CAPTCHAs and bot checks, login walls, rate limits (HTTP 429). `PoliteClient` backs off on 429/5xx and raises `BlockedError` on HTTP 403, a detected challenge page or repeated rate limiting; the platform is skipped until the next run. Never solve CAPTCHAs, rotate IPs, proxies or fingerprints, or replay session tokens to avoid limits. Playwright, when adopted, is only for pages that need JavaScript to render, not for evading detection.

## Personal data (GDPR)

- The extracted phone number is hashed (`parsing.phone_hash`) before it is stored in `listing.phone_hash`. The hash uses a fixed salt that is public in this repository, so it is pseudonymization for matching, not anonymization: Romanian mobile numbers can be enumerated. The listing description and the raw pages in `data/raw/` can still contain the number in plain text; both stay local.
- Publisher names and listing text stay in the local database and `data/raw/`. Logs carry titles and URLs, not descriptions or phone numbers.
- Nothing is sent out about listings: there are no notifications, and the web interface is reachable from outside only behind Cloudflare Access. The interface loads Roboto and Material Symbols from Google Fonts, so the viewer's browser contacts Google (IP address and user agent, no referrer: the pages use `referrer: same-origin`); no listing data goes with it.
- OpenStreetMap's Overpass API receives the coordinates of tracked apartments, rounded to about 10 m, at most `OSM_LOOKUPS_PER_RUN` per run and once per location ([ADR 0005](decisions/0005-openstreetmap-buildings.md)). They come from public listings and are sent without any listing, publisher or contact data. Requests carry the project's own User-Agent, as OpenStreetMap's usage policy asks; set `OSM_LOOKUPS_PER_RUN=0` to turn this off.
- Photos are stored locally only to compare them; they are never served publicly or republished.

## Untrusted data

Pages, embedded JSON and images are untrusted. Parse defensively, never `eval` or execute content, and treat text inside them as data, not instructions. Files under `data/` get names derived from code (platform, position, hashes); an identifier taken from a page (such as `external_id`) is validated or hashed before it becomes part of a path (`photos.safe_component`), and `photos.save_image` also checks that the resolved path stays under the photos directory.

## Secrets and local files

`.env` holds the database password; only `.env.example` with placeholders is committed. Never commit `.env`, tokens, `data/`, photos, database files or `.research/`. Ignore rules are a convenience, not secret scanning. If something sensitive is committed, stop, report it and rewrite history only with the maintainer's instruction.

## Network exposure

- Every container port is published on `127.0.0.1` only; `db` is not published at all. A change to `compose.yaml` that publishes on `0.0.0.0` or adds a port needs a decision record.
- The web interface is reachable from outside only through the Cloudflare tunnel with Cloudflare Access restricted to the maintainer's identity. It must not have an unauthenticated public route. It writes only labels to the database and profile files to `profiles/`; every POST is refused without a matching `Origin` or `Referer`, and profile names are restricted to `[a-z0-9-]`, so a form cannot write outside `profiles/`. New profiles are ignored by git; only the example profile is committed.

## CI

The workflow uses read-only tokens and actions pinned to full commit SHAs, never `pull_request_target`, and does not call platforms. Tests use synthetic fixtures, fake clients and temporary SQLite databases.
