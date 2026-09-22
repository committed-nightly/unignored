# unignored

Finds the lines in your `.gitignore` files that do nothing at all — the rule that
was never going to work, not the rule you might not need. If you have ever added
a file to `.gitignore` and watched it keep turning up in `git status`, this is
the tool that tells you why.

It is for anyone who maintains a repository old enough that nobody remembers why
half the ignore file is there.

## Install

```
pip install git+https://github.com/committed-nightly/unignored
```

Python 3.10 or newer, and a `git` binary. No other dependencies.

## Use

```
unignored [PATH]
```

`PATH` is any directory inside a git repository; the whole repository is checked.
Defaults to the current directory.

```
$ unignored
.gitignore:18: .eggs/
    never decides anything: line 22 repeats it, and the last match wins
    delete this line; `.eggs/` on line 22 is the one in effect

1 finding in 2 ignore files.
```

That one is real — it is `psf/requests`, which has listed `.eggs/` twice for
years.

Exit codes, because the reason to run this in CI is to fail a build:

| code | meaning |
| ---- | ------- |
| 0 | every rule can still decide something |
| 1 | at least one cannot |
| 2 | nothing was checked — not a repository, no such path, no git |

`--json` gives the same report in a machine-readable form; the exit code does not
change. `--only CHECK` narrows it to one of the four checks, which is what you
want when adopting this on a repository with a lot of history.

## What it looks for

**`tracked` — a rule that matches a file git already tracks.** Once a path is in
the index, ignore rules do not apply to it. It keeps showing up in `git status`,
its changes keep getting committed, and the rule that was meant to stop that does
nothing for it. This is the finding behind every "but I added it to .gitignore"
question ever asked.

**`unreachable-negation` — a `!` rule that never puts its file back.** From
gitignore(5): *"It is not possible to re-include a file if a parent directory of
that file is excluded."* So this, which looks obviously correct, is not:

```
build/
!build/keep.txt
```

Git never descends into `build/`, so it never reads the second line. The working
form excludes the directory's contents instead of the directory:

```
build/*
!build/keep.txt
```

**`unread-file` — a `.gitignore` git never reads.** An ignore file inside an
excluded directory cannot change anything: everything below that directory is
already ignored by the rule that excluded it, and nothing inside can be
re-included. The file is inert, whatever is in it.

**`shadowed` — a rule a later line in the same file always beats.** The last
pattern to match a path decides it, so an identical pattern further down the file
makes the earlier one unreachable — whether the later line repeats it or negates
it.

## How it decides

It does not match ignore patterns. Every question goes to
`git check-ignore --no-index -v`, which answers with the exact file, line and
pattern that decided a path.

That is the point rather than a shortcut. Gitignore matching has enough corners
in it — `**`, the anchoring rule for a mid-pattern slash, trailing-space escapes,
the difference between `foo` and `foo/` — that a second implementation would
disagree with git somewhere, and a linter that disagrees with the thing it is
linting is worse than no linter. Being unable to disagree with git is the only
interesting property this tool has.

Checking a repository costs one `git check-ignore` per directory level, plus one
for all tracked files at once. The walk stops at an excluded directory rather
than descending into it, so `node_modules/` in your ignore file does not cost a
walk of node_modules.

## What it will not tell you

**Rules that match nothing right now.** A rule for a `dist/` you have not built
yet is a working rule, and reporting it would make the other four findings worth
less. Everything reported here is dead no matter what you check out next.

**Whether one glob covers another.** `shadowed` compares pattern text, not
meaning. `app.log` followed by `*.log` is dead in practice and is not reported —
deciding that in general means implementing glob subsumption, which is a
different program.

**Negations that name a set rather than a path.** `!build/*.keep` and a bare
`!keep.txt` are skipped. The first stands for a set of paths; the second matches
at any depth, so it is dead inside an excluded directory and alive everywhere
else, which is not one answer about the rule.

**Self-ignoring markers.** A `.gitignore` whose whole content is `*` — what
pytest writes into `.pytest_cache/` and cargo into `target/` — is not reported as
unread. It exists so the directory ignores itself with or without help from
outside, and deleting it would only take away a fallback.

**Your global excludes.** `core.excludesFile` is read by git and will be named as
the blocker if it is the reason something is dead, but its own lines are not
audited. It is not this repository's file.

**Submodules.** They have their own index and their own ignore rules; run
`unignored` inside one.

## Licence

MIT.
