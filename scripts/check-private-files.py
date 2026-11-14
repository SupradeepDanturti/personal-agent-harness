"""Reject accidentally tracked local credentials/state; never read their contents."""
from pathlib import PurePosixPath
import subprocess
import sys


def is_private(filename):
    path = PurePosixPath(filename)
    for part in path.parts:
        if part in {".assistant", ".venv", "node_modules"}:
            return True
        if part == ".dev.vars" or (part.startswith(".dev.vars.") and part != ".dev.vars.example"):
            return True
        if part == ".env" or (part.startswith(".env.") and part != ".env.example"):
            return True
        if part == ".docker.env" or (part.startswith(".docker.env.") and part != ".docker.env.example"):
            return True
    return path.suffix in {".pem", ".key", ".sqlite", ".sqlite3"}


def main():
    tracked = subprocess.check_output(["git", "ls-files", "-z"]).decode("utf-8").split("\0")
    blocked = [name for name in tracked if name and is_private(name)]
    if blocked:
        print("Private local files must not be committed:")
        print("\n".join(blocked))
        return 1
    print("No local credential or runtime files are tracked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
