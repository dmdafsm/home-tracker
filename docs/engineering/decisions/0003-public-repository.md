# ADR 0003: public repository, private data, synthetic fixtures

Status: accepted. Date: 2026-10-27. Related tasks: REPO, E1.

## Context

The GitHub repository `dmdafsm/home-tracker` is public. The plan asks for a test per adapter "on a saved page", so that a platform's structure change is caught early. A saved real page contains someone's listing: description, photo URLs, often a name and a phone number, and the platform's content.

## Decision

The repository contains code and documentation only. Collected listings, photos, raw pages, the database and anything copied from a platform stay on the maintainer's machine (`data/`, `.research/`, both ignored). Adapter tests use synthetic fixtures: the real page's structure and quirks with invented titles, prices, names, phone numbers, addresses and image URLs. Real pages saved with `hometracker dump` stay in `data/debug/` and are used for the local real-page check. `scripts/check_repository.py` rejects committed data files, databases, photos, gzipped archives and oversized fixtures.

## Alternatives and consequences

- Make the repository private: real fixtures would be possible, but the code would not be shareable and a later visibility change would expose them.
- Commit real pages: simplest tests, but republishes third-party listings and personal data.
- Consequences: each adapter change needs a hand-made synthetic fixture and one local check on a real page. The search profile in `profiles/` is public; it holds criteria, not personal data.

## Verification

Current adapter tests build synthetic HTML inline (`tests/test_adapters.py`); `tests/fixtures/` holds no real pages. `python3 scripts/verify.py` runs the repository checks.
