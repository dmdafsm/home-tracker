# Task branches and PR-only integration

The default branch is main. Protect main, master and the resolved default branch. A task may contain several commits on one task branch; it does not need a new branch for each commit. After PR integration those commits can be reachable from the default branch, which is expected Git behavior.

## Local setup and use

Run once per clone: `python3 scripts/install_hooks.py`. It sets core.hooksPath locally and preserves an existing hook system by refusing to overwrite a different active configuration. Integrate existing hooks deliberately if a conflict is reported. Hooks are not activated automatically by cloning files.

Before editing, inspect the branch and use a task branch named `<type>/<topic>`, with the same type as the commit message: `feat/` for new behavior, `fix/` for bug fixes, `chore/` for tooling and maintenance, `docs/` for documentation, `refactor/` and `test/` for those changes. Examples: `feat/publi24-adapter`, `fix/olx-floor`. The guards do not check the prefix; it is a naming convention. Creating/switching a branch preserves ordinary uncommitted work, but inspect shared changes first and do not overwrite unrelated work. A named branch is required for commits; create one before committing from a detached worktree.

The installed guards:

- pre-commit blocks direct commits on main/master/the resolved default branch, including amend operations.
- pre-merge-commit blocks locally created merge commits on those branches.
- pre-push checks destination refs, blocking direct updates, creation and deletion of protected branches even through a refspec such as task:main. Tags are not branches.

`python3 scripts/branch_policy.py commit` checks the current branch without creating a commit. The policy is local and bypassable through Git options/configuration; it is an accident-prevention guard, not the server's security boundary. Fast-forward/reset/ref updates can also change local refs without creating a commit, so do not claim every ref movement is intercepted.

## CI and server enforcement

CI validates PR head/base metadata: integration PRs originate from task branches and target main/master/the default branch. CI does not reject the ordinary push event emitted after a legitimate PR merge. A workflow runs after an update and cannot retroactively prevent a direct push.

The authoritative enforcement is an active GitHub branch ruleset requiring PRs, successful verification, no force push and no deletion, with bypass rights restricted. [GitHub ruleset rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

The importable [ruleset template](../../.github/rulesets/protected-branches.json) requires PRs and the Repository verification check, covers the current default plus main/master and declares no bypass actors. It uses zero formal approvals for the current sole-maintainer-compatible baseline; increase the approval count when an eligible independent reviewer is available. Review evidence and owner merge judgment still apply. After the first successful workflow run, verify/select GitHub Actions as the expected check source in the UI.

Import and review it in GitHub repository Settings → Rules → Rulesets. Availability depends on the repository plan/permissions. The file's presence does not activate it, and changing its enforcement field does not change a remote ruleset. Keep the remote ruleset and template synchronized after authorized changes.

Official Git behavior: [hooks](https://git-scm.com/docs/githooks), [core.hooksPath](https://git-scm.com/docs/git-config#Documentation/git-config.txt-corehooksPath). Local hook tests use disposable Git repositories and filesystem remotes, not the real origin.
