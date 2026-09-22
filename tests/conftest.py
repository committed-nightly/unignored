from __future__ import annotations

import os
import subprocess

import pytest

from unignored.gitcmd import Git


class Repo:
    """A throwaway git repository built out of a dict."""

    def __init__(self, path: str) -> None:
        self.path = path

    def write(self, relpath: str, content: str) -> None:
        full = os.path.join(self.path, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)

    def mkdir(self, relpath: str) -> None:
        os.makedirs(os.path.join(self.path, relpath), exist_ok=True)

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", self.path, *args],
            capture_output=True,
            check=True,
        )
        return proc.stdout.decode()

    def track(self, *paths: str) -> None:
        """Commit paths, ignore rules or no ignore rules."""
        self.git("add", "-f", *paths)
        self.git("commit", "-qm", "tracked")

    @property
    def handle(self) -> Git:
        return Git(self.path)


@pytest.fixture
def repo(tmp_path) -> Repo:
    path = str(tmp_path / "repo")
    os.makedirs(path)
    subprocess.run(["git", "-C", path, "init", "-q"], check=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "Test"], check=True)
    # Whatever the machine running the tests has in its own global excludes must
    # not reach into these repositories.
    subprocess.run(["git", "-C", path, "config", "core.excludesFile", "/dev/null"], check=True)
    return Repo(path)
