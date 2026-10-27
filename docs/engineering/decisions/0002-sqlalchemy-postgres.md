# ADR 0002: SQLAlchemy models on PostgreSQL, SQLite for tests

Status: accepted. Date: 2026-10-27. Related tasks: E1, E2. Recorded after the stage 1 implementation (commit `aa017f1`).

## Context

The plan specifies PostgreSQL, with PostGIS for coordinates and pgvector for photo similarity in later stages. Tests and quick local runs should not need a running Postgres.

## Decision

- Tables are SQLAlchemy 2.0 typed models in `models.py`, created by `init_db` (`create_all`).
- Docker uses PostgreSQL (`DATABASE_URL`, psycopg 3). Without `DATABASE_URL`, the app uses SQLite at `data/hometracker.db`; tests use temporary SQLite databases.
- Code must stay portable across both until a later decision drops SQLite.
- There is no migration tool yet. `create_all` creates new tables only; new columns on existing tables need an idempotent startup migration (see [architecture](../../architecture.md#schema-evolution)).

## Alternatives and consequences

- Raw SQL with psycopg: fewer abstractions, but two dialects to maintain by hand for tests.
- PostgreSQL in tests (testcontainers or a service in CI): exact dialect, but slower tests and Docker in CI.
- Consequences: PostGIS and pgvector (stage 2) have no SQLite equivalent. Adopting them requires a new decision: Postgres-backed tests for those queries, a fallback in Python for SQLite, or dropping SQLite. Adopting Alembic is the expected answer once columns start changing often.

## Verification

`uv run pytest` runs the pipeline end to end on SQLite (`tests/test_pipeline.py`). The PostgreSQL path is exercised only by the Docker stack; no automated test runs against Postgres.
