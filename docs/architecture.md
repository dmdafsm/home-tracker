# Architecture

A single Python package (`src/hometracker`) with an argparse CLI, run by Docker Compose on a single machine. This page describes what is implemented (stage 1); the target design for later stages is in the [plan](plan.md).

## Containers

| Service | Image | Role | Published |
| --- | --- | --- | --- |
| `db` | `postgres:16-alpine` | the database (`pgdata` volume) | not published |
| `worker` | built from `Dockerfile` | `hometracker loop`: collection, matching, dedup, alerts | not published |
| `ntfy` | `binwiederhier/ntfy` | push notifications for the phone (`ntfydata` volume) | `127.0.0.1:8090` |
| `web` | planned (stage 2) | FastAPI interface | `127.0.0.1:8080` |

`./data` and `./profiles` are bind-mounted into the worker. Outside access is a tunnel configured outside this repository (for example Cloudflare Tunnel) that routes one hostname to ntfy and, later, one behind Cloudflare Access to the web interface. See [ADR 0001](engineering/decisions/0001-docker-compose.md).

## Data flow

```
profiles/*.yaml ──> pipeline ──> adapter.search(url) ──> PoliteClient ──> platform
                       │              │                     └──> data/raw/<host>/<day>/*.html.gz
                       │              └─ fetch_detail() for new listings that pass the filters
                       ▼
                 profiles.match() ──> photos (data/photos/, pHash/dHash) ──> dedup.assign_property()
                       │
                       ▼
                 listing / listing_snapshot / photo / property ──> alert ──> Notifier (ntfy, email)
```

- **Adapters** (`adapters/`, one module per platform, registered in `adapters/__init__.py`) turn search and detail pages into `ListingData` (`schema.py`). Parsing is done by `parse_search(html, url)` and `parse_detail(html, url)`, which do no I/O; fetching goes through `Adapter.search` / `fetch_detail` and the shared `PoliteClient` (`http.py`), which serializes requests per host, waits a random delay and archives the raw HTML.
- **Parsing helpers** (`parsing.py`) interpret Romanian text: price and currency, floor, area, rooms, balcony, phone numbers.
- **Profiles** (`profiles.py`) load `profiles/*.yaml` and compute hard filters plus a 0–100 preference score with reasons.
- **Dedup** (`dedup.py`) attaches a listing to an existing property when they share at least two informative photos (Hamming distance ≤ 8 on pHash), or one photo plus the same area and floor.
- **Pipeline** (`pipeline.py`) runs a profile across its sources, stores listings and snapshots, downloads photos for matching listings, assigns properties, sends alerts and warns when an adapter errors or returns nothing. After the searches it rechecks matched listings that have left the search results (see below).
- **Notifications** (`notify.py`): ntfy (JSON publish, token auth) and SMTP email.
- **CLI** (`cli.py`): `loop`, `run [--catchup]`, `dump <url>`, `test-notify`, `matches`.

## Tables and ownership

All tables are defined in `models.py` and written by the worker only.

| Table | Written by | Holds |
| --- | --- | --- |
| `listing` | `pipeline.py` | one listing on one platform, unique per `(platform, external_id)`; `phone_hash`, never the phone; `active`, `checked_at` and `removed_at` from the recheck |
| `listing_snapshot` | `pipeline.py` | the listing's price when first seen and at every change |
| `photo` | `pipeline.py` | downloaded photos: source URL, local path, pHash, dHash |
| `property` | `dedup.py` | the physical apartment (cluster); listings point to it through `listing.property_id` |
| `alert` | `pipeline.py` | sent notifications per profile, property and kind (`new`, `price_drop`) |
| `run_log` | `pipeline.py` | one row per profile and platform run: counts and errors, used for warnings and catch-up |

## Key conventions

- **Prices** are kept as published (`price`, `currency`) and converted to `price_eur` with the `EUR_RON` setting; filters and alerts use `price_eur`.
- **Floors** are strings: `basement`, `ground`, `raised_ground`, `1`, `2`, …, `attic`; `None` means unknown, which a profile may allow.
- **Unknown is not false**: `balcony`, `area_m2`, `rooms` and `floor` stay `None` when the page does not say.
- **Recheck**: a listing seen in search is confirmed by the search itself (`last_seen`). A listing that passed a profile (`property_id` set), is still `active` and has not been seen in search for `RECHECK_AFTER_HOURS` (24) gets its detail page fetched again, at most `RECHECK_PER_RUN` (10) per run and once per period (`checked_at`). A new price goes through the same snapshot and price-drop rules as the search. HTTP 404/410, or `Adapter.is_removed` on the page, marks the listing `active = False` with `removed_at`; it becomes active again if it reappears in search. Other errors leave it active and retry next period; a block stops the recheck for that platform. `is_removed` is still the default (`False`) for all three adapters until each is checked against a real expired listing.
- **Catch-up**: after a gap longer than two intervals (Mac asleep or off), the next run reads `PAGES_CATCHUP` result pages instead of `PAGES_NORMAL`.
- **Local files**: `data/raw/` (gzipped HTML), `data/photos/`, `data/debug/` (pages saved by `dump`). Without `DATABASE_URL`, the app uses SQLite at `data/hometracker.db`.

## Schema evolution

`init_db` calls `Base.metadata.create_all`, which creates missing tables, then `models.add_missing_columns`, which adds to existing tables the model columns they lack (`ALTER TABLE … ADD COLUMN`, checked with `sqlalchemy.inspect`, idempotent, tested on SQLite and PostgreSQL). It only handles columns that accept NULL and have no server default, and refuses anything else. Renames, type changes and required columns need an explicit migration or a documented rebuild from `data/raw/`. Adopting Alembic, PostGIS or pgvector is a decision record ([ADR 0002](engineering/decisions/0002-sqlalchemy-postgres.md)).
