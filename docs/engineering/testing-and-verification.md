# Testing and verification

## Entry points

Run `python3 scripts/verify.py` from the repository root. It runs, in order:

1. `scripts/check_repository.py`: required files, text hygiene (LF, final newline, no trailing whitespace), local Markdown links, valid JSON, pinned workflow actions, no credential files, no data files (`data/`, databases, photos, gzipped archives) or oversized files committed.
2. The repository-guard tests in `scripts/tests/` (standard library `unittest`).
3. `uv sync --locked`, `uv run ruff format --check`, `uv run ruff check`, `uv run pytest`.

`python3 scripts/verify.py --repository-only` runs only steps 1–2, for documentation and tooling work; it does not verify the application. It stops on the first failed command and reports a missing `uv` as a failure, not a pass.

## Select checks by changed behavior

| Change | Evidence needed |
| --- | --- |
| Documentation | Repository checks; accurate claims about what is implemented. |
| Adapter (search or detail parsing) | Fixture test with a minimal synthetic page in `tests/fixtures/`: the real page's structure and quirks (embedded JSON keys, missing fields, Storia mirrors on OLX) with invented values, plus a changed-shape case. Then `hometracker dump "<url>"` on a real page and a spot check against the site; record the counts. |
| Text parsing (price, floor, area, balcony, phone) | Table-driven tests with real-world phrasings rewritten by hand, including the traps (`boxă la demisol`, `parter înalt`, prices in lei, `P/4`). |
| Profiles and scoring | Hand-built `ListingData` covering each filter boundary, unknown values and score reasons. |
| Dedup | Synthetic images (see `tests/test_pipeline.py`): same apartment on two platforms merges; a shared façade photo alone does not; different apartments stay apart. |
| Pipeline | End to end on a temporary SQLite database with a fake client: one apartment per physical apartment, price snapshots, `run_log` rows for the status page, reruns without duplicates. |
| Schema | The test database plus an existing-database migration test when a column is added (SQLite in the suite; check PostgreSQL once with a throwaway `postgres:16-alpine` container and report it). |
| Docker / compose | `docker compose config -q` (needs `.env`), `docker compose build`, and the worker's log after one run. |
| Web interface | Page tests with FastAPI's `TestClient` (`tests/test_web.py`), including escaping and the photo path check. Then start it on demo data (`DATA_DIR=data/demo uv run hometracker demo`, preview `web-demo`), open every changed page at desktop and phone width and in dark mode, check there are no console errors or horizontal scroll, and that the numbers match `hometracker property`. |

Use the minimum useful suite for the change; protect invariants and regressions rather than a coverage number.

## Fixtures and isolation

- Tests never touch `data/` or the network. Use `tmp_path`, SQLite and fake clients.
- Fixtures are minimal and synthetic: copy the structure of a search or detail page (script tags, JSON nesting, number and date formats, empty and extra fields), then replace every title, description, price, name, phone number, address and image URL. The repository is public; a saved real page would republish someone's listing ([ADR 0003](decisions/0003-public-repository.md)). Real pages saved with `hometracker dump` stay in `data/debug/` or `.research/`.
- Shared test helpers live in `tests/helpers.py`, the `setup` fixture in `tests/conftest.py`. Use deterministic dates and images; avoid `now()` in assertions unless the test controls it.

## Honest results

Record commands and outcomes. Separate passed, failed, not run and not applicable. A failing or skipped check is reported as such; never weaken a rule to get a pass. A narrow suppression (`# noqa: XXX`) needs a reason in the same line or the PR.
