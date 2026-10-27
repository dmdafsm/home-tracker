# ADR 0005: OpenStreetMap buildings for the construction year

Status: accepted. Date: 2026-10-27. Related tasks: E4.1.

## Context

Stage 4 of the [plan](../../plan.md) estimates the construction year of each apartment's block. OpenStreetMap has `start_date` and `building:levels` on some buildings: high confidence where present, low coverage. Listings with coordinates (mostly Storia) can be matched to the nearest building.

## Decision

- The worker queries the public Overpass API (`OVERPASS_URL`, default `https://overpass-api.de/api/interpreter`) for buildings within 30 m of a matched apartment's coordinates, at most `OSM_LOOKUPS_PER_RUN` (default 5, 0 disables) per run, through the same polite client (one request at a time per host, random delays, the project's User-Agent).
- Results are cached in `osm_building`, keyed by coordinates rounded to 4 decimals (about 10 m), including "nothing found", and never refetched automatically. The web app and the daily summary only read the cache.
- The year comes from the nearest building with `start_date` (`1978`, `1978-05`, `~1975`, `1970s`, `C20` are understood); `building:levels` is kept as information.
- Pages that show OSM data credit "© OpenStreetMap contributors" (ODbL).

## Alternatives and consequences

- Download an OSM extract for Cluj once and query it locally: no repeated requests, but a new data pipeline and a large file to keep up to date.
- Nominatim reverse geocoding: returns an address, not building tags.
- Consequences: Overpass receives the approximate coordinates of apartments the tool tracks; they come from public listings and identify no person. The public instance has usage limits (roughly 10,000 queries a day, a few concurrent); five lookups per run stay far below them. Coverage of `start_date` in Cluj is partial, so most estimates still come from listings and typology.

## Verification

Recorded in the PR that introduces it: unit tests on synthetic Overpass responses (parsing, nearest building, cache, limits) and one live query near Gheorgheni.
