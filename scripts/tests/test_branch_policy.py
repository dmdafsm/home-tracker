"""Exercise branch guards against disposable repositories, never the real origin."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import branch_policy as policy
import install_hooks


class BranchGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "work"
        self.root.mkdir()
        environment = patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        environment.start()
        self.addCleanup(environment.stop)
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Guardrail test")
        self.git("config", "user.email", "guardrail@example.invalid")
        self.git("commit", "--allow-empty", "-m", "Fixture base")
        (self.root / "scripts").mkdir()
        shutil.copy(policy.ROOT / "scripts/branch_policy.py", self.root / "scripts/branch_policy.py")
        shutil.copytree(policy.ROOT / ".githooks", self.root / ".githooks")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()

    def git(self, *arguments, check=True):
        return subprocess.run(["git", *arguments], cwd=self.root, text=True, capture_output=True, check=check)

    def test_commits_on_main_and_master_are_blocked(self):
        self.git("branch", "master")
        install_hooks.install(self.root)
        for branch in ("main", "master"):
            with self.subTest(branch=branch):
                self.git("switch", branch)
                result = self.git("commit", "--allow-empty", "-m", "Must fail", check=False)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("BLOCKED", result.stderr)
                self.assertEqual(self.base, self.git("rev-parse", "HEAD").stdout.strip())

    def test_task_branch_commit_succeeds(self):
        install_hooks.install(self.root)
        self.git("switch", "-c", "feat/test")
        self.git("commit", "--allow-empty", "-m", "Allowed task commit")
        self.assertNotEqual(self.base, self.git("rev-parse", "HEAD").stdout.strip())

    def test_amend_on_main_is_blocked(self):
        install_hooks.install(self.root)
        result = self.git("commit", "--amend", "--allow-empty", "-m", "Must fail", check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("BLOCKED", result.stderr)
        self.assertEqual(self.base, self.git("rev-parse", "HEAD").stdout.strip())

    def test_detached_commit_is_blocked(self):
        install_hooks.install(self.root)
        self.git("switch", "--detach")
        result = self.git("commit", "--allow-empty", "-m", "Must fail", check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("named task branch", result.stderr)

    def test_merge_commit_on_main_is_blocked(self):
        self.git("switch", "-c", "feat/test")
        self.git("commit", "--allow-empty", "-m", "Task change")
        self.git("switch", "main")
        install_hooks.install(self.root)
        result = self.git("merge", "--no-ff", "feat/test", "-m", "Must fail", check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("BLOCKED", result.stderr)
        self.assertEqual(self.base, self.git("rev-parse", "HEAD").stdout.strip())

    def test_nonstandard_default_branch_is_protected(self):
        self.git("branch", "trunk")
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        self.git("switch", "trunk")
        self.assertIn("trunk", policy.commit_error(self.root))
        self.assertIn("refs/heads/trunk", policy.push_error(self.root, "HEAD abc refs/heads/trunk def"))

    def test_push_checks_destination_using_real_local_remote(self):
        remote = Path(self.directory.name) / "origin.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        self.git("remote", "add", "origin", str(remote))
        self.git("switch", "-c", "feat/test")
        install_hooks.install(self.root)
        self.git("push", "origin", "HEAD:refs/heads/feat/test")
        for branch in ("main", "master"):
            with self.subTest(branch=branch):
                result = self.git("push", "origin", "HEAD:refs/heads/" + branch, check=False)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("BLOCKED", result.stderr)
                self.assertEqual("", self.git("ls-remote", "origin", "refs/heads/" + branch).stdout)
        self.git("tag", "main")
        self.git("push", "origin", "refs/tags/main")

    def test_deletion_and_mixed_ref_pushes_are_blocked(self):
        zero = "0" * 40
        self.assertIsNotNone(policy.push_error(self.root, f"(delete) {zero} refs/heads/main abc"))
        self.assertIsNotNone(
            policy.push_error(self.root, "refs/heads/task abc refs/heads/task def\nHEAD abc refs/heads/master def")
        )

    def test_malformed_push_input_fails(self):
        self.assertIsNotNone(policy.push_error(self.root, "unexpected input"))

    def test_install_is_idempotent(self):
        install_hooks.install(self.root)
        install_hooks.install(self.root)
        self.assertEqual(".githooks", self.git("config", "--get", "core.hooksPath").stdout.strip())

    def test_existing_configured_hook_system_is_preserved(self):
        self.git("config", "core.hooksPath", "custom-hooks")
        with self.assertRaisesRegex(ValueError, "Existing core.hooksPath"):
            install_hooks.install(self.root)
        self.assertEqual("custom-hooks", self.git("config", "--get", "core.hooksPath").stdout.strip())

    def test_existing_executable_hook_is_preserved(self):
        hook = self.root / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "Existing active hooks"):
            install_hooks.install(self.root)
        self.assertEqual("#!/bin/sh\nexit 0\n", hook.read_text(encoding="utf-8"))


class PullRequestPolicyTests(unittest.TestCase):
    def event(self, head="feat/test", base="main", default="main"):
        return {
            "repository": {"default_branch": default},
            "pull_request": {"head": {"ref": head}, "base": {"ref": base}},
        }

    def test_task_to_integration_pr_is_allowed(self):
        self.assertIsNone(policy.pr_error(self.event()))
        self.assertIsNone(policy.pr_error(self.event(base="trunk", default="trunk")))

    def test_protected_head_and_unexpected_base_are_blocked(self):
        self.assertIsNotNone(policy.pr_error(self.event(head="main")))
        self.assertIsNotNone(policy.pr_error(self.event(base="another-task")))
        self.assertIsNotNone(policy.pr_error({}))

    def test_post_merge_push_does_not_fail_pr_check(self):
        result = subprocess.run(
            [sys.executable, str(policy.ROOT / "scripts/branch_policy.py"), "pr"],
            env={**os.environ, "GITHUB_EVENT_NAME": "push"},
            text=True,
            capture_output=True,
        )
        self.assertEqual(0, result.returncode)
        self.assertIn("NOT APPLICABLE", result.stdout)

    def test_pr_cli_uses_event_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.json"
            path.write_text(json.dumps(self.event(head="master")), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(policy.ROOT / "scripts/branch_policy.py"), "pr"],
                env={**os.environ, "GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(path)},
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("BLOCKED", result.stderr)


if __name__ == "__main__":
    unittest.main()
