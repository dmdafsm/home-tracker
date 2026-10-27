# Task readiness: analysis and architecture check

A short pass done by whoever implements the task, before editing. Not a separate meeting, agent or approval gate.

## Analysis pass

For a feature or data task, establish:

1. What the result changes for the maintainer (for example "the feed shows the price history of a saved apartment") and the observable output (an interface screen, a CLI command, a table).
2. The relevant [plan](../plan.md) stage and its open points; reduce the task to a coherent, implementable behavior.
3. Concrete examples of input and expected output, including failure cases: a listing without floor or price, a price in lei, the same apartment from an agency and the owner, a façade photo reused for another apartment, a page that changed shape, a CAPTCHA.
4. The platform's terms, access limits and protections ([security and data](security-and-data.md)).
5. What evidence will establish the result: fixture tests, a pipeline test, a `dump` on a real page, a browser check.

Small fixes need a few sentences in the PR. New analysis or open questions go into a local note under `.research/tasks/` using [the task brief template](templates/task-brief.md).

Do not invent a matching or dedup rule to avoid a clarification. Ask only when the missing decision materially changes results; continue independent work meanwhile.

## Architecture impact

Record one of:

- **Existing pattern**: name the owning module/table; no decision record needed.
- **Material change**: write the smallest useful [decision record](decisions/template.md) first. Examples: a new platform, a new container or exposed hostname, Playwright, PostGIS/pgvector or a migration tool, a change to how listings are grouped into apartments, anything touching the personal-use constraint or personal data.
- **Missing decision**: name it and continue the work that does not depend on it.

## Before handoff

Run the applicable verification and real-data checks, inspect the diff, follow [code review](code-review.md) and record what is still limited.
