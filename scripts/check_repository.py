"""Fast repository contracts. Standard library only; no network or application execution."""

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".md", ".json", ".py", ".yml", ".yaml", ".toml", ".html", ".txt", ".cfg"}
TEXT_NAMES = {".editorconfig", ".gitattributes", ".gitignore", ".python-version"}
REQUIRED_PATHS = (
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "README.md",
    "SECURITY.md",
    ".editorconfig",
    ".gitattributes",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "compose.yaml",
    "Dockerfile",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/workflows/verify.yml",
    ".github/rulesets/protected-branches.json",
    "docs/architecture.md",
    "docs/plan.md",
    "docs/engineering/coding-standards.md",
    "docs/engineering/testing-and-verification.md",
    "docs/engineering/security-and-data.md",
    "docs/engineering/branch-policy.md",
    "docs/engineering/code-review.md",
    "docs/engineering/task-readiness.md",
    "scripts/branch_policy.py",
    "scripts/install_hooks.py",
    "scripts/verify.py",
    ".githooks/pre-commit",
    ".githooks/pre-merge-commit",
    ".githooks/pre-push",
)
# Collected listings, photos and databases stay local; fixtures must stay small.
DATA_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".dump", ".gz", ".jpg", ".jpeg", ".webp"}
MAX_FILE_BYTES = 512 * 1024
MAX_FIXTURE_BYTES = 200 * 1024


def repository_files(root):
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return sorted(
        {
            Path(item.decode("utf-8"))
            for item in result.stdout.split(b"\0")
            if item and (root / item.decode("utf-8")).is_file()
        }
    )


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def markdown_targets(content):
    """Inline file links, excluding fenced examples and external/anchor links."""
    fence = None
    for line in content.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            current = marker.group(1)[0]
            fence = None if fence == current else (current if fence is None else fence)
            continue
        if fence:
            continue
        for match in re.finditer(r"!?\[[^\]]*\]\((<[^>]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)", line):
            target = match.group(1).strip("<>")
            if urlsplit(target).scheme or target.startswith(("#", "//")):
                continue
            target = unquote(target.split("#", 1)[0])
            if target:
                yield target


def check_text(path, content):
    errors = []
    if "\r" in content:
        errors.append(f"{path}: use LF line endings")
    # Saved HTML fixtures are kept byte-for-byte as downloaded.
    verbatim = path.suffix == ".html"
    if content and not content.endswith("\n") and not verbatim:
        errors.append(f"{path}: missing final newline")
    for number, line in enumerate(content.splitlines(), 1):
        if line.rstrip() != line and not verbatim and path.suffix != ".md":
            errors.append(f"{path}:{number}: trailing whitespace")
        if re.search(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----", line):
            errors.append(f"{path}:{number}: private-key marker must not be committed")
    return errors


def check_path(root, path):
    errors = []
    name = path.name.lower()
    if (name == ".env" or name.startswith(".env.")) and not name.endswith(".example"):
        errors.append(f"{path}: credential/local-settings file must not be committed")
    if name.endswith((".pem", ".key", ".p12", ".pfx")) or name in {"id_rsa", "id_ed25519"}:
        errors.append(f"{path}: key material must not be committed")
    if path.parts[0] in {"data", ".research", ".artifacts"} or path.suffix.lower() in DATA_SUFFIXES:
        errors.append(f"{path}: collected data and local output must not be committed")
    size = (root / path).stat().st_size
    limit = MAX_FIXTURE_BYTES if path.parts[:2] == ("tests", "fixtures") else MAX_FILE_BYTES
    if size > limit and path.name != "uv.lock":
        errors.append(f"{path}: {size} bytes exceeds {limit}; keep fixtures minimal and data out of Git")
    return errors


def check_workflow(path, content):
    errors = []
    if re.search(r"^\s*pull_request_target\s*:", content, re.MULTILINE):
        errors.append(f"{path}: do not run untrusted PR code through pull_request_target")
    for action, revision in re.findall(r"uses:\s*([^\s@]+)@([^\s#]+)", content):
        if not action.startswith("./") and not re.fullmatch(r"[0-9a-f]{40}", revision):
            errors.append(f"{path}: pin {action} to a full commit SHA")
    return errors


def check_repository(root):
    errors = [f"missing required file: {path}" for path in REQUIRED_PATHS if not (root / path).is_file()]
    for path in repository_files(root):
        errors.extend(check_path(root, path))
        if path.suffix not in TEXT_SUFFIXES and path.name not in TEXT_NAMES and path.parent != Path(".githooks"):
            continue
        try:
            content = (root / path).read_bytes().decode("utf-8")
            errors.extend(check_text(path, content))
            if path.suffix == ".md":
                for target in markdown_targets(content):
                    if not (root / path.parent / target).exists():
                        errors.append(f"{path}: missing local link {target}")
            elif path.suffix == ".json":
                read_json(root / path)
            if path.parent.as_posix() == ".github/workflows":
                errors.extend(check_workflow(path, content))
        except (ValueError, UnicodeError) as error:
            errors.append(f"{path}: invalid content: {error}")
    return errors


def main():
    try:
        errors = check_repository(ROOT)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"Repository validation failed: {error}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print("ERROR: " + error, file=sys.stderr)
        return 1
    print("PASS: repository paths, text, links, JSON, data/size and workflow contracts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
