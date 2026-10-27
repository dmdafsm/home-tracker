## Problem and result

Related task: En.n / REPO. Describe the trigger, resulting behavior and acceptance criteria.

Architecture impact: existing pattern (owning module/table), or material change with a linked decision record.

## Validation

| Command or check | Actual result |
| --- | --- |
| `python3 scripts/verify.py` | Passed / failed / not run, with reason |
| Real-page check (`hometracker dump`, worker run, spot check) | Result or not applicable, with reason |
| Notification check (`hometracker test-notify`) | Result or not applicable, with reason |
| Web interface browser check | Result or not applicable, with reason |

## Data and platform impact

Affected platforms, terms and request volume; schema changes (additive or migration/rebuild procedure); alert and dedup behavior; personal data and network exposure. Use "not applicable" with a reason when appropriate.

## Review notes

Material tradeoffs, dependency changes, known limitations. Review status: self-review / independent review / not run, with scope.

- [ ] The diff is focused; docs and `uv.lock` match the implementation.
- [ ] Acceptance criteria have evidence; incomplete work is stated explicitly.
- [ ] No secrets, collected data, photos or real platform pages; fixtures are synthetic.
