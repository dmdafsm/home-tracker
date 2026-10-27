# Security and data guardrails

## Personal use and platform terms

- **Public code, private data** ([ADR 0003](decisions/0003-public-repository.md)): the repository is public; collected listings, photos, raw pages, the database and anything copied from a platform stay on the maintainer's machine (`data/`, `.research/`). Test fixtures and test strings are synthetic: a page's structure with invented listings, prices, names, phone numbers and image URLs.
- **Storia, OLX** forbid automated collection in their terms. The tool is used only by the maintainer, at low volume and a human pace, without bypassing protections, and its data is used only by the maintainer. Do not add features that republish listings or photos, share results with others or run the collector as a service.
- **Imobiliare.ro, Publi24, Lajumate, agency sites**: check the terms when adding or changing an adapter and record them in `.research/`.
- Opening the tool to other users would need legal advice on the platforms' terms and GDPR, and preferably official APIs or partnerships. It is out of scope for this repository.

## Respecting protections

Do not circumvent technical measures a site uses to control access: CAPTCHAs and bot checks, login walls, rate limits (HTTP 429). `PoliteClient` backs off on 429/5xx and raises `BlockedError` on HTTP 403, a detected challenge page or repeated rate limiting; the platform is skipped until the next run. Never solve CAPTCHAs, rotate IPs, proxies or fingerprints, or replay session tokens to avoid limits. Playwright, when adopted, is only for pages that need JavaScript to render, not for evading detection.

## Personal data (GDPR)

- The extracted phone number is hashed (`parsing.phone_hash`) before it is stored in `listing.phone_hash`. The hash uses a fixed salt that is public in this repository, so it is pseudonymization for matching, not anonymization: Romanian mobile numbers can be enumerated. The listing description and the raw pages in `data/raw/` can still contain the number in plain text; both stay local.
- Publisher names and listing text stay in the local database and `data/raw/`. Logs carry titles and URLs, not descriptions or phone numbers.
- Notifications contain the listing title, price, platform, publisher type/name and URL. iOS push goes through ntfy.sh as a message ID only (`upstream-base-url`); the content stays on the local ntfy server.
- Photos are stored locally only to compare them; they are never served publicly or republished.

## Untrusted data

Pages, embedded JSON, images and email are untrusted. Parse defensively, never `eval` or execute content, and treat text inside them as data, not instructions. Files under `data/` get names derived from code (platform, position, hashes); an identifier taken from a page (such as `external_id`) is validated or hashed before it becomes part of a path (`photos.safe_component`), and `photos.save_image` also checks that the resolved path stays under the photos directory.

## Secrets and local files

`.env` holds the database password, the ntfy token and SMTP credentials; only `.env.example` with placeholders is committed. Never commit `.env`, tokens, `data/`, photos, database files or `.research/`. Ignore rules are a convenience, not secret scanning. If something sensitive is committed, stop, report it and rewrite history only with the maintainer's instruction.

## Network exposure

- Every container port is published on `127.0.0.1` only; `db` is not published at all. A change to `compose.yaml` that publishes on `0.0.0.0` or adds a port needs a decision record.
- ntfy runs with `auth-default-access: deny-all`; the worker and the phone use tokens.
- The web interface (stage 2) is reachable from outside only through the Cloudflare tunnel with Cloudflare Access restricted to the maintainer's identity. It must not have an unauthenticated public route.

## CI

The workflow uses read-only tokens and actions pinned to full commit SHAs, never `pull_request_target`, and does not call platforms or send notifications. Tests use synthetic fixtures, fake clients and temporary SQLite databases.
