# ADR 0001: Docker Compose on one machine, reached through a tunnel

Status: accepted, amended. Date: 2026-10-27. Related tasks: E1, REPO. Recorded after the stage 1 implementation (commit `aa017f1`). Amended 2026-10-27: notifications were dropped in favour of the web interface, and the `ntfy` container with them.

## Context

The tool must run on one personal machine, which is not always on, and be usable from outside, including the phone's browser. The [plan](../../plan.md) settles the decisions: one `docker compose up`, containers `db`, `worker` and `web`, outside access through a Cloudflare tunnel.

## Decision

- `compose.yaml` runs `db` (PostgreSQL 16), `worker` (built from the `Dockerfile`, `hometracker loop`) and `web` (the same image, `hometracker web`).
- Published ports are bound to `127.0.0.1` only (`web` on `WEB_LOCAL_PORT`, default 8080). `db` is not published.
- Outside access uses one ingress rule added by hand to a tunnel the repository does not manage: a web hostname → web, behind Cloudflare Access restricted to the maintainer's identity. Hostnames and the tunnel itself stay in local configuration.
- Collection runs while the machine is awake. After a gap longer than two intervals the worker runs a catch-up with more result pages.

## Alternatives and consequences

- A small always-on server: no gaps, but new infrastructure and cost; the plan keeps it as the fallback if gaps become a problem.
- Scheduled jobs on the host (cron, launchd): no Docker, but persistent system configuration and no isolated Postgres.
- Consequences: listings that appear and disappear while the machine is off or asleep are missed. A misconfigured tunnel rule is the main exposure risk, so every port stays on loopback and the web hostname must have Access in front before the web container is published.

## Verification

The stack definition is in `compose.yaml`. `docker compose build` and live worker and web runs were checked in the PRs that changed the stack (#12 onwards).
