# Code review

CI checks deterministic properties (format, lint, tests, repository contracts). Review checks behavior, assumptions and risks CI may miss. The author always inspects the final diff. Self-review is labelled as such; it is not an independent review.

## Local review

In Claude Code, run `/code-review` on the uncommitted changes or the branch diff, or ask a separate review agent. Tell the reviewer the task and the risk to inspect. For substantive changes check:

- matching: hard filters vs preferences, unknown values, price conversion, floor interpretation;
- dedup: false merges (shared façade or stock photos), missed merges, threshold changes;
- the feed: one apartment shown several times, a matching apartment missing, collection problems not shown on the status page;
- collection: idempotent reruns, request rate per platform, behavior when a page changes shape or a challenge appears;
- personal data, network exposure and the personal-use constraint ([security and data](security-and-data.md)).

The reviewer reports actionable findings with severity, file/line, trigger and consequence; if there are none, it says so and states its limits. Review the final changes after fixes.

## PR review

The PR is the canonical record once it exists: findings, dispositions and validation links belong there. An AI review is additional evidence; it does not authorize merge. With a sole maintainer, merge is the maintainer's explicit decision based on the evidence.

## Records

- Local review notes: `.artifacts/reviews/<task>/review.md`, using [the template](templates/code-review.md) (ignored by Git).
- Durable conclusions: a short summary in `docs/` or a [decision record](decisions/template.md).

A review that has not run must not be presented as completed.
