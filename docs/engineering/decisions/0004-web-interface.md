# ADR 0004: read-only web interface in its own container

Status: accepted. Date: 2026-10-27. Related tasks: E2.4.

## Context

Stage 2 of the [plan](../../plan.md) asks for a local web interface with a feed of matching apartments and an apartment page, reachable from the phone through the tunnel with Cloudflare Access in front. The plan proposes FastAPI with HTMX or a small React app. The labelling buttons (save, hide, confirmed real, fake, not the same apartment) belong to stage 3.

## Decision

- A `web` service in `compose.yaml`, built from the same image as `worker`, runs `hometracker web --host 0.0.0.0 --port 8080`. Compose publishes it only on `127.0.0.1:${WEB_LOCAL_PORT:-8080}`; outside access is a tunnel hostname behind Cloudflare Access, configured outside the repository ([ADR 0001](0001-docker-compose.md)). The app has no login of its own and must never be published without Access in front.
- FastAPI with Jinja2 templates rendered on the server, autoescaping on, no JavaScript in this step. The price chart is an inline SVG built on the server. HTMX is added in stage 3, when the pages get actions.
- The web app only reads the database. It calls `init_db` at startup like the worker (idempotent), so it also works when it starts first. It writes nothing until stage 3.
- Photos are served from the local copies by photo id (`/photo/{id}`), after checking that the stored path is inside `data/photos`. Pages never hotlink the platforms' image CDNs, so the phone does not contact the platforms when browsing.
- `./data` is mounted read-only into `web`.

## Alternatives and consequences

- A small React app: richer interactions, but a JavaScript toolchain and an API to maintain for a single user.
- Serving the pages from the worker process: one container less, but a slow collection run would block the pages.
- Consequences: the image grows by FastAPI, Uvicorn and Jinja2. On a brand-new PostgreSQL database, `web` and `worker` may run `create_all` at the same time; one of them can fail on its first start and is restarted by `restart: unless-stopped`. Listing data and photos are visible to anyone who passes Cloudflare Access, so the Access policy must stay restricted to the maintainer.

## Verification

Recorded in the PR that introduces it: page tests with FastAPI's test client, a browser check on synthetic demo data (`hometracker demo`), the Docker image serving the pages.
