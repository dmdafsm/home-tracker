"""Repository contract checks against disposable repositories, including failure cases."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_repository as checks


class RepositoryContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        environment = patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        environment.start()
        self.addCleanup(environment.stop)
        subprocess.run(["git", "init", "--initial-branch=main"], cwd=self.root, check=True, capture_output=True)
        for path in checks.REQUIRED_PATHS:
            self.write(path, "{}\n" if path.endswith(".json") else "ok\n")

    def write(self, path, content, binary=False):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            target.write_bytes(content)
        else:
            target.write_bytes(content.encode("utf-8"))

    def errors(self):
        return checks.check_repository(self.root)

    def test_minimal_repository_passes(self):
        self.assertEqual([], self.errors())

    def test_missing_required_file_fails(self):
        (self.root / "AGENTS.md").unlink()
        self.assertIn("missing required file: AGENTS.md", self.errors())

    def test_text_hygiene(self):
        self.write("a.py", "x = 1  \n")
        self.write("b.md", "no newline")
        self.write("c.yml", "a: 1\r\n")
        errors = "\n".join(self.errors())
        self.assertIn("a.py:1: trailing whitespace", errors)
        self.assertIn("b.md: missing final newline", errors)
        self.assertIn("c.yml: use LF line endings", errors)

    def test_broken_markdown_link_fails_but_fenced_and_external_links_pass(self):
        self.write(
            "docs/x.md",
            "[ok](../README.md) [web](https://example.com) [gone](missing.md)\n```\n[ignored](nowhere.md)\n```\n",
        )
        errors = self.errors()
        self.assertIn("docs/x.md: missing local link missing.md", errors)
        self.assertEqual(1, sum("missing local link" in e for e in errors))

    def test_data_and_credentials_are_rejected(self):
        self.write("data/hometracker.db", b"\0", binary=True)
        self.write("photo.jpg", b"\0", binary=True)
        self.write(".env", "TOKEN=x\n")
        self.write("key.txt", "-----BEGIN " + "PRIVATE KEY-----\n")
        errors = "\n".join(self.errors())
        self.assertIn("data/hometracker.db: collected data", errors)
        self.assertIn("photo.jpg: collected data", errors)
        self.assertIn(".env: credential", errors)
        self.assertIn("private-key marker", errors)

    def test_ignored_files_are_not_checked(self):
        self.write(".gitignore", "data/\n")
        self.write("data/hometracker.db", b"\0", binary=True)
        self.assertEqual([], self.errors())

    def test_oversized_fixture_fails(self):
        self.write("tests/fixtures/page.html", "x" * (checks.MAX_FIXTURE_BYTES + 1) + "\n")
        self.assertTrue(any("exceeds" in e for e in self.errors()))

    def test_workflow_actions_must_be_pinned(self):
        self.write(
            ".github/workflows/verify.yml",
            "steps:\n  - uses: actions/checkout@v4\n  - uses: actions/setup-python@" + "a" * 40 + " # v7\n",
        )
        errors = self.errors()
        self.assertIn(".github/workflows/verify.yml: pin actions/checkout to a full commit SHA", errors)
        self.assertEqual(1, sum("pin " in e for e in errors))

    def test_pull_request_target_is_rejected(self):
        self.write(".github/workflows/verify.yml", "on:\n  pull_request_target:\n")
        self.assertTrue(any("pull_request_target" in e for e in self.errors()))

    def test_duplicate_json_keys_fail(self):
        self.write("x.json", '{"a": 1, "a": 2}\n')
        self.assertTrue(any("duplicate JSON key" in e for e in self.errors()))


if __name__ == "__main__":
    unittest.main()
