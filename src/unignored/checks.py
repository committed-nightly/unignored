"""The four things that make an ignore rule do nothing.

Every one of them is decided, not guessed. A rule that simply matches no file in
the working tree is not in here and never will be -- that is a rule for a build
directory you have not built yet, and reporting it would make the other four
findings worth less.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .gitcmd import Decision, Git
from .rules import IgnoreFile, Rule, literal_path

TRACKED = "tracked"
UNREACHABLE_NEGATION = "unreachable-negation"
UNREAD_FILE = "unread-file"
SHADOWED = "shadowed"


@dataclass
class Finding:
    check: str
    source: str
    line: int | None  # None when the finding is about a whole file
    text: str
    summary: str
    paths: list[str] = field(default_factory=list)
    extra: int = 0  # paths not listed
    fix: str = ""

    @property
    def where(self) -> str:
        return self.source if self.line is None else f"{self.source}:{self.line}"

    @property
    def sort_key(self) -> tuple[str, int]:
        return (self.source, -1 if self.line is None else self.line)

    def as_dict(self) -> dict:
        out = {
            "check": self.check,
            "source": self.source,
            "line": self.line,
            "text": self.text,
            "summary": self.summary,
            "fix": self.fix,
        }
        if self.paths:
            out["paths"] = self.paths
            out["more_paths"] = self.extra
        return out


SAMPLE = 5


def tracked(git: Git, max_paths: int = SAMPLE) -> list[Finding]:
    """Rules that match a file git already tracks.

    Tracking wins. Once a path is in the index, no ignore rule touches it: it
    keeps showing up in `git status`, its changes keep getting committed, and
    the rule that was supposed to stop that does nothing at all for it.

    This is the one finding that is about a pair rather than a rule -- the same
    rule may be doing useful work on other, untracked paths. So the report names
    the files, not just the line.
    """
    paths = git.tracked_files()
    verdicts = git.check_ignore(paths)

    hits: dict[tuple[str, int], tuple[Decision, list[str]]] = {}
    for path in paths:
        decision = verdicts.get(path)
        if decision is None or decision.negated:
            continue
        hits.setdefault(decision.key, (decision, []))[1].append(path)

    findings = []
    for decision, matched in hits.values():
        shown = sorted(matched)[:max_paths]
        count = len(matched)
        findings.append(
            Finding(
                check=TRACKED,
                source=decision.source,
                line=decision.line,
                text=decision.pattern,
                summary=(
                    f"does nothing for {count} file{'s' if count != 1 else ''} "
                    "git already tracks"
                ),
                paths=shown,
                extra=count - len(shown),
                fix=(
                    f"an ignore rule has no effect on a tracked path. "
                    f"`git rm --cached -- {shown[0]}` to stop tracking it, or drop the rule"
                ),
            )
        )
    return findings


def unreachable_negations(git: Git, files: list[IgnoreFile]) -> list[Finding]:
    """`!` rules that never put back the file they name.

    gitignore(5) puts it plainly: "It is not possible to re-include a file if a
    parent directory of that file is excluded". Git does not descend into an
    excluded directory, so it never gets as far as the negation inside it. The
    rule is not overridden. It is not reached.

    Nothing here works that out for itself. A negation that names exactly one
    path gets that path handed to check-ignore, and git says which rule decides
    it -- git applies its own descend rule while answering, which is why this is
    asked as a question about a file and not about a directory. If the answer is
    any rule other than this one, this one does nothing for the only path it
    names.
    """
    candidates: list[tuple[Rule, str]] = []
    for ignore_file in files:
        if not ignore_file.read:
            continue  # the whole file is already a finding of its own
        for rule in ignore_file.rules:
            if not rule.negated:
                continue
            named = literal_path(rule)
            if named is None:
                continue
            if ignore_file.directory:
                named = f"{ignore_file.directory}/{named}"
            candidates.append((rule, named))

    if not candidates:
        return []

    verdicts = git.check_ignore(sorted({path for _, path in candidates}))

    findings = []
    for rule, named in candidates:
        decision = verdicts.get(named)
        if decision is not None and decision.key == rule.key:
            continue  # this rule decides its own path: it is working
        if decision is None:
            continue  # nothing ignores the path, so nothing needed re-including
        findings.append(
            Finding(
                check=UNREACHABLE_NEGATION,
                source=rule.source,
                line=rule.line,
                text=rule.text,
                summary=(
                    f"never re-includes {named}: "
                    f"{decision.source}:{decision.line}: {decision.pattern} decides it instead"
                ),
                fix=(
                    "git takes the last pattern that matches, and it does not descend "
                    "into an excluded directory at all -- so a negation below one is "
                    "never reached. Re-including a file means un-excluding every "
                    "directory on the way down to it first: `build/*` rather than "
                    "`build/`, then this line"
                ),
            )
        )
    return findings


def is_self_ignoring(ignore_file: IgnoreFile) -> bool:
    """A `.gitignore` whose whole content is `*`.

    pytest writes one of these into `.pytest_cache/`, cargo writes one into
    `target/`, and several other tools do the same. The file exists so that the
    directory ignores itself whether or not anything outside mentions it. When
    the outer ignore file does mention it, this one is indeed never read -- and
    saying so is telling someone to delete a fallback that costs nothing and
    works the moment the outer rule is removed.

    Found by running this tool on its own repository after the tests had left a
    .pytest_cache behind.
    """
    return [rule.text for rule in ignore_file.rules] == ["*"]


def unread_files(files: list[IgnoreFile]) -> list[Finding]:
    """Ignore files inside an excluded directory.

    Nothing in them can matter. Every path below an excluded directory is already
    ignored by the rule that excluded it, and a negation cannot reach down there
    either, so no line in the file can change any outcome.
    """
    findings = []
    for ignore_file in files:
        if ignore_file.read or is_self_ignoring(ignore_file):
            continue
        blocker = ignore_file.unread_because
        count = len(ignore_file.rules)
        findings.append(
            Finding(
                check=UNREAD_FILE,
                source=ignore_file.path,
                line=None,
                text=f"{count} rule{'s' if count != 1 else ''}",
                summary=(
                    f"is never read: {ignore_file.directory}/ is excluded by "
                    f"{blocker.source}:{blocker.line}: {blocker.pattern}"
                ),
                fix=(
                    f"everything under {ignore_file.directory}/ is ignored whatever this "
                    f"file says. Move the rules up to an ignore file git reads, or stop "
                    f"excluding {ignore_file.directory}/"
                ),
            )
        )
    return findings


def shadowed(files: list[IgnoreFile]) -> list[Finding]:
    """Rules a later line in the same file always beats.

    The last pattern to match a path decides it. So if a later line in the same
    file has the same pattern body, the earlier one can never be the last match
    for anything -- whether the later line agrees with it or negates it. Same
    body only: nothing here tries to work out whether one glob covers another.
    """
    findings = []
    for ignore_file in files:
        if not ignore_file.read:
            continue
        last: dict[str, Rule] = {}
        for rule in ignore_file.rules:
            last[rule.body] = rule
        for rule in ignore_file.rules:
            winner = last[rule.body]
            if winner.line == rule.line:
                continue
            verb = "repeats" if winner.text == rule.text else "is reversed by"
            findings.append(
                Finding(
                    check=SHADOWED,
                    source=rule.source,
                    line=rule.line,
                    text=rule.text,
                    summary=(
                        f"never decides anything: line {winner.line} "
                        f"{verb} it, and the last match wins"
                    ),
                    fix=f"delete this line; `{winner.text}` on line {winner.line} is the one in effect",
                )
            )
    return findings


def scan(git: Git, files: list[IgnoreFile]) -> list[Finding]:
    findings = (
        tracked(git)
        + unreachable_negations(git, files)
        + unread_files(files)
        + shadowed(files)
    )
    return sorted(findings, key=lambda f: f.sort_key)
