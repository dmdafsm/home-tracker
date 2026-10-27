"""Install the repository hooks locally without replacing an existing hook system."""

import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ("pre-commit", "pre-merge-commit", "pre-push")


def install(root):
    result = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=root, text=True, capture_output=True)
    configured = result.stdout.strip() if result.returncode == 0 else None
    target = (root / ".githooks").resolve()
    if configured:
        current = Path(configured).expanduser()
        if not current.is_absolute():
            current = root / current
        if current.resolve() != target:
            raise ValueError(f"Existing core.hooksPath {configured!r}; integrate it rather than replacing it")
    else:
        path = subprocess.check_output(["git", "rev-parse", "--git-path", "hooks"], cwd=root, text=True).strip()
        existing = Path(path) if Path(path).is_absolute() else root / path
        active = (
            [
                entry.name
                for entry in existing.iterdir()
                if entry.is_file() and not entry.name.endswith(".sample") and os.access(entry, os.X_OK)
            ]
            if existing.is_dir()
            else []
        )
        if active:
            raise ValueError("Existing active hooks must be integrated first: " + ", ".join(sorted(active)))
    for name in HOOKS:
        path = target / name
        if not path.is_file():
            raise ValueError(f"Missing repository hook: {path}")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    subprocess.run(["git", "config", "--local", "core.hooksPath", ".githooks"], cwd=root, check=True)


def main():
    try:
        install(ROOT)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Hook installation failed: {error}", file=sys.stderr)
        return 1
    print("Installed local branch guards through core.hooksPath=.githooks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
