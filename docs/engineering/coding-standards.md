# Coding standards

## Organization

One package, `src/hometracker`. Adapters live in `hometracker/adapters/` (one module per platform), the processing steps are top-level modules (`profiles`, `dedup`, `merge`, `pipeline`, `status`), and `cli.py` wires them into commands. See the [architecture](../architecture.md) for table ownership.

- An adapter keeps a platform's URL handling and parsing together. Parsing lives in `parse_search(html, url)` and `parse_detail(html, url)`, which take text and return `ListingData` without I/O, so they can be tested against fixtures. Fetching goes through the shared `PoliteClient`, never a client of its own.
- A new platform is a new module plus one line in `adapters/__init__.py`; it must not need changes in the pipeline.
- Keep each rule in one place: Romanian text interpretation in `parsing.py`, filters and scoring in `profiles.py`, "same apartment" in `dedup.py` (suggestions in `merge.py`), display formatting in `formatting.py`. The web interface reads and presents; it does not recompute scores or clusters.
- CLI commands are thin: parse options, call module functions, print. Long-running logic does not live in `cli.py`.
- Prefer plain functions, small dataclasses and pydantic models for validated input (profiles). No repository layer, dependency-injection framework or class hierarchy without a demonstrated need.

## Python

- Python 3.12, type hints on public functions, `X | None` style. Ruff (`uv run ruff format`, `uv run ruff check`) is the formatter and linter; the configuration is in `pyproject.toml`. Line length 120.
- Identifiers in English; comments, docstrings, log, CLI and interface text in Romanian, as in the existing modules.
- Validate at the boundary (pages, JSON, images, YAML profiles). A page that changed shape must show up: the adapter returns nothing or raises, the run is logged in `run_log` with its error, and the pipeline warns. Do not invent values to fill a missing field.
- Keep comments for reasons and non-obvious constraints (why a platform is parsed a certain way, why a threshold), not narration.
- Timestamps are timezone-aware UTC (`models.utcnow`). Prices keep the published value and currency; `price_eur` is the converted value used for filtering. Areas in m².

## Database and schema

- Models live in `models.py` (SQLAlchemy 2.0 typed mappings). Code must run on PostgreSQL (Docker) and SQLite (tests, local runs) until a decision record says otherwise.
- Use the ORM or SQLAlchemy Core expressions; never format values into SQL strings.
- Schema changes are additive and must work on an existing database: new tables come from `create_all`, new nullable columns from `models.add_missing_columns` at startup; anything else needs an explicit migration (see [architecture](../architecture.md#schema-evolution)). Add a test that upgrades an existing database (see `tests/test_recheck.py`).
- Uniqueness that the logic relies on (`(platform, external_id)`) is a database constraint, not only a check in code.
- Commit per listing in long runs, so a blocked platform or a crash keeps what was already processed.

## Adapters and collection

- Polite by default: one request at a time per host through `PoliteClient`, random delays (`REQUEST_DELAY_MIN/MAX`), a detail page only for new listings worth it, a bounded number of result pages per run.
- Use the pages as a browser would. Do not bypass protections; a detected challenge raises `BlockedError` and stops that platform until the next run, see [security and data](security-and-data.md).
- Archive raw HTML under `data/raw/` (done by `PoliteClient`), so parsers can be re-run on history after a fix.
- Record provenance: `platform`, `external_id`, `url`, `is_detail`, `first_seen`/`last_seen`, a `run_log` row per run.
- Platform-specific quirks (Storia mirrors on OLX, prices missing from Imobiliare.ro search pages) are handled and commented in the adapter that has them.

## Matching and dedup

- Hard filters exclude; preferences only order. Unknown values are handled explicitly by the profile (`allow_unknown_floor`), never by accident.
- Dedup must prefer a missed merge over a false one: two different apartments in one cluster hide one of them from the feed; suggest instead (`merge.py`) and let the maintainer merge. Thresholds are named constants with a comment on why.

## Dependencies

Add with `uv add`, commit `uv.lock`. Prefer the standard library and the existing stack (httpx, SQLAlchemy, pydantic, BeautifulSoup/lxml, Pillow, imagehash). A new platform, a new container, Playwright or PostGIS/pgvector needs a [decision record](decisions/template.md).
