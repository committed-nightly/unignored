"""Reading ignore files, and working out which ones git ever reads.

A `.gitignore` is only consulted for the directory it sits in and everything
below. If that directory is excluded, nothing below it can change: every path
underneath is ignored by the rule that excluded the directory, and a negation
inside cannot put one back, because git will not descend into an excluded
directory to find out. So the whole file is inert, whatever is in it.

That is the rule this module's walk is built on, and it is also why the walk
stops at an excluded directory instead of descending. A repository with
`node_modules/` in its ignore file should not cost a walk of node_modules to
check.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .gitcmd import Git

IGNORE_FILE = ".gitignore"
INFO_EXCLUDE = os.path.join(".git", "info", "exclude")

# Patterns holding one of these have a glob in them somewhere.
MAGIC = "*?["


@dataclass(frozen=True)
class Rule:
    """One usable line of an ignore file."""

    source: str  # repo-relative path of the file it came from
    line: int  # 1-based, counting blanks and comments, as git does
    text: str  # the line as written, trailing whitespace resolved
    negated: bool

    @property
    def body(self) -> str:
        """The pattern with any leading `!` removed."""
        return self.text[1:] if self.negated else self.text

    @property
    def key(self) -> tuple[str, int]:
        return (self.source, self.line)

    def __str__(self) -> str:
        return f"{self.source}:{self.line}: {self.text}"


@dataclass
class IgnoreFile:
    path: str  # repo-relative
    directory: str  # repo-relative directory it governs; "" for the root
    rules: list[Rule]
    unread_because: object | None = None  # a Decision, when git never reads this file

    @property
    def read(self) -> bool:
        return self.unread_because is None


def strip_trailing_space(line: str) -> str:
    """Drop trailing spaces and tabs, unless a backslash keeps them.

    gitignore(5): "Trailing spaces are ignored unless they are quoted with a
    backslash". An odd number of backslashes in front of a space quotes it.
    """
    end = len(line)
    while end > 0 and line[end - 1] in " \t":
        slashes = 0
        while slashes < end - 1 and line[end - 2 - slashes] == "\\":
            slashes += 1
        if slashes % 2 == 1:
            break
        end -= 1
    return line[:end]


def parse(text: str, source: str) -> list[Rule]:
    """The rules in one ignore file, keeping git's line numbering.

    Blank lines and comments are skipped but still counted, because git reports
    the real line number and a report you cannot jump to is half a report.
    """
    rules: list[Rule] = []
    for number, raw in enumerate(text.split("\n"), start=1):
        line = strip_trailing_space(raw.rstrip("\r"))
        if line == "" or line.startswith("#"):
            continue
        rules.append(
            Rule(
                source=source,
                line=number,
                text=line,
                negated=line.startswith("!"),
            )
        )
    return rules


def read_ignore_file(root: str, relpath: str) -> IgnoreFile | None:
    """The ignore file at `relpath`, or None if there is nothing there to judge.

    A file with no usable rules in it comes back as None rather than as an empty
    one. `git init` writes a `.git/info/exclude` that is four lines of comment,
    and counting that as an ignore file makes every clean repository report that
    it has one.
    """
    full = os.path.join(root, relpath)
    try:
        with open(full, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    rules = parse(raw.decode("utf-8", "replace"), relpath)
    if not rules:
        return None
    directory = os.path.dirname(relpath)
    if relpath == INFO_EXCLUDE:
        directory = ""  # info/exclude governs the whole working tree
    return IgnoreFile(path=relpath, directory=directory, rules=rules)


def literal_ancestors(rule: Rule) -> list[str]:
    """The directories a rule's own pattern pins it under, if any.

    `build/keep.txt` is pinned under `build`. `a/b/c.txt` is pinned under `a`
    and `a/b`. `*.keep`, `**/keep.txt` and a bare `keep.txt` are pinned under
    nothing -- they match at any depth, so no single directory can be blamed
    for them.

    Anything with a glob or a backslash in a component stops the walk there:
    the aim is a list of directories that certainly exist in the path, not a
    guess at what a glob might expand to.
    """
    body = rule.body
    if body.endswith("/"):
        body = body[:-1]
    parts = body.split("/")
    if parts and parts[0] == "":
        parts = parts[1:]  # a leading slash anchors, it is not a component
    if len(parts) < 2:
        return []  # no directory component at all

    ancestors: list[str] = []
    prefix = ""
    for part in parts[:-1]:
        if part == "" or "\\" in part or any(char in part for char in MAGIC):
            break
        prefix = f"{prefix}/{part}" if prefix else part
        ancestors.append(prefix)
    return ancestors


def _is_submodule(path: str) -> bool:
    return os.path.exists(os.path.join(path, ".git"))


def collect(root: str, git: Git) -> list[IgnoreFile]:
    """Every ignore file in the repository, marked read or unread.

    Breadth-first, one check-ignore call per level rather than per directory,
    and no descent past an excluded directory or into a submodule -- a
    submodule has its own ignore rules and its own index, and reporting on it
    from out here would get both wrong.
    """
    files: list[IgnoreFile] = []

    info = read_ignore_file(root, INFO_EXCLUDE)
    if info is not None:
        files.append(info)

    root_file = read_ignore_file(root, IGNORE_FILE)
    if root_file is not None:
        files.append(root_file)

    level = _subdirectories(root, "")
    while level:
        verdicts = git.check_ignore([f"{d}/" for d in level])
        next_level: list[str] = []
        for directory in level:
            decision = verdicts.get(f"{directory}/")
            excluded = decision if decision is not None and not decision.negated else None

            found = read_ignore_file(root, os.path.join(directory, IGNORE_FILE))
            if found is not None:
                found.unread_because = excluded
                files.append(found)

            if excluded is None:
                next_level.extend(_subdirectories(root, directory))
        level = next_level

    return files


def _subdirectories(root: str, directory: str) -> list[str]:
    full = os.path.join(root, directory) if directory else root
    try:
        entries = sorted(os.scandir(full), key=lambda e: e.name)
    except OSError:
        return []

    out: list[str] = []
    for entry in entries:
        if entry.name == ".git":
            continue
        if not entry.is_dir(follow_symlinks=False):
            continue
        child = os.path.join(directory, entry.name) if directory else entry.name
        if _is_submodule(entry.path):
            continue
        out.append(child)
    return out
