# Security policy

This is a public repository for a personal tool maintained by one person. There are no releases, hosted deployments or users other than the maintainer. The repository contains code and documentation only; collected listings, photos and the database never leave the maintainer's machine ([ADR 0003](docs/engineering/decisions/0003-public-repository.md)).

## Reporting

Report vulnerabilities privately through the GitHub **Security** tab → **Report a vulnerability**. Do not open a public issue for a vulnerability. Use synthetic data in the report; do not attach a database, collected data, tokens or credentials.

## Scope

- The adapters: handling of downloaded HTML, JSON and images (parsing untrusted input, file paths, image decoding).
- The Docker setup: every container port is published on the host loopback only; the database is not published at all.
- Remote access: ntfy with `deny-all` default access and a token, the web interface behind Cloudflare Access.
- Notifications: what listing data is sent to ntfy (and through ntfy.sh for iOS push) and by email.
- The repository's CI workflow, hooks and dependency setup.

Out of scope: fake or wrong listings that match what the platform shows, and vulnerabilities in the platforms themselves.
