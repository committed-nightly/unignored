"""Command line entry point.

Exit codes, because the reason to run this in CI is to fail a build:

    0  every rule in every ignore file can still decide something
    1  at least one of them cannot
    2  nothing was checked

2 is not 1 and is emphatically not 0. Not a git repository, a path that does not
exist, no git on PATH: those all mean nobody looked, and a linter reporting
"nobody looked" with a green tick is the failure mode it exists to prevent.

A repository with no ignore files at all is a 0. Ignoring nothing is a fine thing
to do and the tool has no opinion about it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap

from .checks import SHADOWED, TRACKED, UNREACHABLE_NEGATION, UNREAD_FILE, scan
from .gitcmd import Git, GitError, discover
from .rules import collect

EXIT_OK = 0
EXIT_FOUND = 1
EXIT_ERROR = 2

CHECKS = [TRACKED, UNREACHABLE_NEGATION, UNREAD_FILE, SHADOWED]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unignored",
        description=(
            "Find the .gitignore rules that do nothing: files git already "
            "tracks, negations under an excluded directory, and ignore files "
            "that are never read."
        ),
        epilog="Exit code 0 if every rule can still decide something, 1 if any cannot, 2 if nothing was checked.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="the repository to check (default: the current directory)",
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=CHECKS,
        metavar="CHECK",
        help=f"report only this check; repeatable. One of: {', '.join(CHECKS)}",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="machine-readable output. The exit code is the same either way",
    )
    return parser


def render(findings, files, width: int = 80) -> str:
    lines = []
    for finding in findings:
        lines.append(f"{finding.where}: {finding.text}")
        lines.append(f"    {finding.summary}")
        for path in finding.paths:
            lines.append(f"      {path}")
        if finding.extra:
            lines.append(f"      ... and {finding.extra} more")
        if finding.fix:
            lines.extend(
                textwrap.wrap(
                    finding.fix,
                    width=width,
                    initial_indent="    ",
                    subsequent_indent="    ",
                )
            )
        lines.append("")

    read = sum(1 for f in files if f.read)
    rules = sum(len(f.rules) for f in files if f.read)
    if findings:
        # "findings", not "dead rules": one of them is about a whole file.
        lines.append(
            f"{_plural(len(findings), 'finding')} in {_plural(len(files), 'ignore file')}."
        )
    else:
        lines.append(
            f"nothing dead: {_plural(rules, 'rule')} across "
            f"{_plural(read, 'ignore file')}."
        )
    return "\n".join(lines)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not os.path.exists(args.path):
        print(f"unignored: {args.path}: no such file or directory", file=sys.stderr)
        return EXIT_ERROR

    try:
        root = discover(args.path)
        git = Git(root)
        files = collect(root, git)
        findings = scan(git, files)
    except GitError as exc:
        print(f"unignored: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.only:
        findings = [f for f in findings if f.check in args.only]

    if args.json:
        print(
            json.dumps(
                {
                    "root": root,
                    "ignore_files": [
                        {"path": f.path, "read": f.read, "rules": len(f.rules)}
                        for f in files
                    ],
                    "findings": [f.as_dict() for f in findings],
                },
                indent=2,
            )
        )
    else:
        print(render(findings, files))

    return EXIT_FOUND if findings else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
