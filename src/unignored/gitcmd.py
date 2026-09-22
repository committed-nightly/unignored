"""Talking to git.

Every question this tool asks about ignore rules is answered by git itself,
through `git check-ignore --no-index -v`. That is not laziness. gitignore
matching has enough corners in it -- `**`, the anchoring rule for a mid-pattern
slash, trailing-space escapes, the difference between `foo` and `foo/` -- that a
second implementation would disagree with git somewhere, and a linter that
disagrees with the thing it is linting is worse than no linter.

Two flags matter and both were arrived at the hard way:

`--no-index` -- without it, check-ignore stays silent about any path in the
index, which is exactly the set of paths the tracked check is about.

Directories are passed *without* a trailing slash, and that is the flag's one
real trap. A trailing slash makes git treat the string as a directory, which is
the only way a `build/` rule will match a path that is not on disk -- but it
also lets the `*` in a `build/*` rule match the empty string after it:

    $ git check-ignore -v --no-index build/    ->  .gitignore:1:build/*
    $ git check-ignore -v --no-index build     ->  (nothing)

`build/*` excludes the contents of build, not build itself, and the difference
between those two is the entire subject of this tool. So every directory asked
about here is one that exists on disk, where git stats it and needs no hint.
Questions about directories that do not exist are asked a different way -- see
`checks.unreachable_negations`, which probes the file instead.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


class GitError(RuntimeError):
    """git was missing, or said no to something we needed."""


@dataclass(frozen=True)
class Decision:
    """The ignore rule that decided a path, as git reports it."""

    source: str
    line: int
    pattern: str

    @property
    def negated(self) -> bool:
        return self.pattern.startswith("!")

    @property
    def key(self) -> tuple[str, int]:
        return (self.source, self.line)

    def __str__(self) -> str:
        return f"{self.source}:{self.line}: {self.pattern}"


class Git:
    """A git repository, reachable through the git binary."""

    def __init__(self, root: str, env: dict[str, str] | None = None) -> None:
        self.root = root
        self._env = env

    def _run(self, args: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                ["git", "-C", self.root, *args],
                input=stdin,
                capture_output=True,
                env=self._env,
            )
        except FileNotFoundError as exc:  # no git on PATH
            raise GitError("git is not installed, or not on PATH") from exc

    def check(self, args: list[str], stdin: bytes | None = None) -> str:
        proc = self._run(args, stdin=stdin)
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip()
            raise GitError(f"`git {' '.join(args)}` failed: {detail}")
        return proc.stdout.decode("utf-8", "replace")

    def tracked_files(self) -> list[str]:
        out = self.check(["ls-files", "-z"])
        return [p for p in out.split("\0") if p]

    def excludes_file(self) -> str | None:
        """core.excludesFile, if one is configured. Empty config exits 1."""
        proc = self._run(["config", "--get", "core.excludesFile"])
        if proc.returncode != 0:
            return None
        value = proc.stdout.decode("utf-8", "replace").strip()
        return value or None

    def check_ignore(self, paths: list[str]) -> dict[str, Decision | None]:
        """Ask git which rule decides each path. None where no rule matches.

        Pass directories with a trailing slash; see the module docstring.

        The output of `-v -n -z` is four NUL-terminated fields per path --
        source, line, pattern, path -- with the first three empty when nothing
        matched. `-n` is what guarantees one record per input, so the answer can
        be keyed back to the question rather than to a position.
        """
        if not paths:
            return {}

        # check-ignore exits 1 when nothing matched, which is an answer and not a
        # failure. Only 128 and above mean it could not look.
        proc = self._run(
            ["check-ignore", "--no-index", "-v", "-n", "-z", "--stdin"],
            stdin=("\0".join(paths) + "\0").encode(),
        )
        if proc.returncode > 1:
            detail = proc.stderr.decode("utf-8", "replace").strip()
            raise GitError(f"`git check-ignore` failed: {detail}")
        out = proc.stdout.decode("utf-8", "replace")

        fields = out.split("\0")
        if fields and fields[-1] == "":
            fields.pop()
        if len(fields) % 4 != 0:
            raise GitError(
                f"check-ignore returned {len(fields)} fields for {len(paths)} paths, "
                "which is not a multiple of four"
            )

        answers: dict[str, Decision | None] = {}
        for source, line, pattern, path in zip(*[iter(fields)] * 4):
            if source == "":
                answers[path] = None
            else:
                answers[path] = Decision(source=source, line=int(line), pattern=pattern)
        return answers

    def is_excluded(self, directory: str) -> Decision | None:
        """The rule excluding this directory, if it is excluded.

        `directory` is repo-relative and must exist on disk. A negation that
        wins is not an exclusion, so it comes back as None.
        """
        path = directory.rstrip("/")
        answer = self.check_ignore([path]).get(path)
        if answer is None or answer.negated:
            return None
        return answer


def discover(path: str) -> str:
    """The top level of the working tree containing `path`."""
    git = Git(path)
    proc = git._run(["rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(detail or f"{path} is not inside a git repository")
    root = proc.stdout.decode("utf-8", "replace").strip()
    if not root:
        raise GitError(f"{path} has no working tree (a bare repository has nothing to ignore)")
    return root
