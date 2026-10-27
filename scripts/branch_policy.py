"""Local branch guards and a PR metadata check; server-side rules remain authoritative."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTECTED_NAMES = {"main", "master"}


def git_output(root, *arguments):
    result = subprocess.run(["git", *arguments], cwd=root, text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else None


def protected_names(root, remote="origin"):
    names = set(PROTECTED_NAMES)
    reference = git_output(root, "symbolic-ref", "--quiet", f"refs/remotes/{remote}/HEAD")
    prefix = f"refs/remotes/{remote}/"
    if reference and reference.startswith(prefix):
        names.add(reference[len(prefix) :])
    return names


def commit_error(root):
    branch = git_output(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch:
        return (
            "Commits require a named task branch; "
            "create a <type>/<topic> branch (feat/, fix/, chore/, ...) before committing."
        )
    if branch in protected_names(root):
        return f"Direct commits on {branch} are blocked. Use a task branch and integrate through a PR."
    return None


def push_error(root, updates, remote="origin"):
    protected = {f"refs/heads/{name}" for name in protected_names(root, remote)}
    for line in updates.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 4:
            return "Unexpected pre-push input; cannot validate destination refs."
        if parts[2] in protected:
            return f"Direct push/delete to {parts[2]} is blocked. Push the task branch and open a PR."
    return None


def pr_error(event):
    default = event.get("repository", {}).get("default_branch")
    protected = PROTECTED_NAMES | ({default} if default else set())
    pull_request = event.get("pull_request", {})
    head = pull_request.get("head", {}).get("ref")
    base = pull_request.get("base", {}).get("ref")
    if not head or not base:
        return "Missing PR head/base metadata."
    if head in protected:
        return "PR changes must come from a task branch, not main/master/the default branch."
    if base not in protected:
        return "Integration PRs must target the default branch, main or master."
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("commit", "push", "pr"))
    parser.add_argument("--remote", default="origin")
    arguments = parser.parse_args()
    try:
        if arguments.mode == "commit":
            error = commit_error(ROOT)
        elif arguments.mode == "push":
            error = push_error(ROOT, sys.stdin.read(), arguments.remote)
        else:
            # A push to main after a legitimate PR merge must not fail this check.
            if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
                print("NOT APPLICABLE: PR branch policy (not a pull_request event)")
                return 0
            path = os.environ.get("GITHUB_EVENT_PATH")
            if not path:
                raise ValueError("GITHUB_EVENT_PATH is required for the PR policy check")
            error = pr_error(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as error:
        print(f"FAIL: branch policy could not be checked: {error}", file=sys.stderr)
        return 1
    if error:
        print("BLOCKED: " + error, file=sys.stderr)
        return 1
    print("PASS: branch policy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
