"""One verification entry point: repository contracts, guard tests, then the Python application."""

import argparse
import os
import shutil
import subprocess
import sys

from check_repository import ROOT


class VerificationError(Exception):
    """A required verification condition was not met."""


def run(command):
    print("RUN: " + " ".join(str(item) for item in command), flush=True)
    environment = {**os.environ, "CI": "true"}
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def verify_application():
    if not shutil.which("uv"):
        raise VerificationError("Required tool is unavailable: uv (https://docs.astral.sh/uv/)")
    run(["uv", "sync", "--locked"])
    run(["uv", "run", "--locked", "ruff", "format", "--check"])
    run(["uv", "run", "--locked", "ruff", "check"])
    run(["uv", "run", "--locked", "pytest", "-q"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-only", action="store_true", help="Do not claim application verification")
    arguments = parser.parse_args()
    try:
        run([sys.executable, "scripts/check_repository.py"])
        run([sys.executable, "-m", "unittest", "discover", "-s", "scripts/tests", "-v"])
        if arguments.repository_only:
            print("NOT RUN: application verification (--repository-only explicitly requested)")
        else:
            verify_application()
    except (VerificationError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: all applicable verification completed; see explicit NOT RUN entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
