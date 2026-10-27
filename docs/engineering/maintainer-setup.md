# GitHub settings outside the working tree

Repository files provide instructions and a workflow; they do not enable server-side protections.

The default branch is main. [Branch policy](branch-policy.md) describes the local hooks, the CI branch guard and the importable [ruleset](../../.github/rulesets/protected-branches.json). After the first push and a successful workflow run, configure the default branch ruleset as the repository plan allows:

- Require a pull request and resolved conversations. With a sole maintainer, keep review evidence and explicit owner merge judgment instead of an impossible self-approval gate. See [review process](code-review.md).
- Require the `Repository verification` check from the `Verify` workflow.
- Prevent force pushes and deletion of the default branch, and limit bypass rights deliberately.
- Enable secret scanning push protection, private vulnerability reporting and dependency alerts. The repository is public ([ADR 0003](decisions/0003-public-repository.md)): a pushed `.env`, ntfy token or SMTP password is exposed immediately.
- Before adding files derived from a platform (fixtures, examples, screenshots), check [security and data](security-and-data.md).

Workflow permissions are contents:read. Verification needs no deployment, external message or production secret.

The workflow must run on pull requests and pushes. Avoid path filters on a required workflow, which can leave its required check missing. A merge queue needs the merge_group trigger.

Dependabot monitors GitHub Actions, the uv lockfile and the Dockerfile base images. Review updates and lockfile changes instead of auto-merging them. The `ntfy` image in `compose.yaml` uses the `latest` tag and is not covered; pin it when a reproducible stack matters.

Outside GitHub, on the host machine: the tunnel ingress rules and the Cloudflare Access policy for the web hostname are configured by hand (see the [README](../../README.md)); this repository does not manage them.

Local validation cannot prove GitHub-hosted execution or active branch protection; check an actual run after publishing.
