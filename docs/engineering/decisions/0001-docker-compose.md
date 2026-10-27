# ADR 0001: Docker Compose on one machine, reached through a tunnel

Status: accepted. Date: 2026-10-27. Related tasks: E1, REPO. Recorded after the stage 1 implementation (commit `aa017f1`).

## Context

The tool must run on one personal machine, which is not always on, and be usable from the phone. The [plan](../../plan.md) settles the decisions: one `docker compose up`, containers `db`, `worker`, `ntfy` and later `web`, phone access through a Cloudflare tunnel.

## Decision

- `compose.yaml` runs `db` (PostgreSQL 16), `worker` (built from the `Dockerfile`, `hometracker loop`) and `ntfy`. `web` is added in stage 2.
- Published ports are bound to `127.0.0.1` only (`ntfy` on `NTFY_LOCAL_PORT`, default 8090; `web` planned on 8080). `db` is not published.
- Outside access uses two ingress rules added by hand to a tunnel the repository does not manage: an ntfy hostname → ntfy, protected by ntfy's own auth (`deny-all`, tokens), because the ntfy phone app cannot pass a Cloudflare Access login; a web hostname → web, behind Cloudflare Access restricted to the maintainer's identity. Hostnames and the tunnel itself stay in local configuration.
- ntfy uses `upstream-base-url: https://ntfy.sh` so iOS gets instant push; ntfy.sh receives only a message ID.
- Collection runs while the machine is awake. After a gap longer than two intervals the worker runs a catch-up with more result pages.

## Alternatives and consequences

- A small always-on server: no gaps, but new infrastructure and cost; the plan keeps it as the fallback if gaps become a problem.
- Scheduled jobs on the host (cron, launchd): no Docker, but persistent system configuration and no isolated Postgres.
- Consequences: listings that appear and disappear while the machine is off or asleep are missed. A misconfigured tunnel rule is the main exposure risk, so every port stays on loopback and the web hostname must have Access in front before the web container is published. `ntfy` uses the `latest` image tag.

## Verification

The stack definition is in `compose.yaml`. This record does not include a recorded `docker compose build`, a live worker run or a phone delivery; those are the stage 1 acceptance check in the plan and are still to be reported.
