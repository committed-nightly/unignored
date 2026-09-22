"""The four checks, against real repositories and a real git.

Everything in here builds a repository on disk and asks git. A test that
stubbed check-ignore out would only prove the tool agrees with the author's
idea of gitignore, which is the thing most likely to be wrong.
"""

from __future__ import annotations

from unignored.checks import (
    SHADOWED,
    TRACKED,
    UNREACHABLE_NEGATION,
    UNREAD_FILE,
    scan,
    shadowed,
    tracked,
    unread_files,
    unreachable_negations,
)
from unignored.rules import collect


def findings_for(repo):
    return scan(repo.handle, collect(repo.path, repo.handle))


def lines(findings, check=None):
    return sorted(
        (f.source, f.line) for f in findings if check is None or f.check == check
    )


class TestTracked:
    def test_a_rule_matching_a_tracked_file_is_reported(self, repo):
        repo.write(".gitignore", "*.log\n")
        repo.write("logs/app.log", "")
        repo.track("logs/app.log")
        (found,) = tracked(repo.handle)
        assert found.check == TRACKED
        assert (found.source, found.line) == (".gitignore", 1)
        assert found.paths == ["logs/app.log"]

    def test_an_untracked_match_is_not_a_finding(self, repo):
        # The rule is working exactly as intended. That is the whole point of it.
        repo.write(".gitignore", "*.log\n")
        repo.write("logs/app.log", "")
        repo.track(".gitignore")
        assert tracked(repo.handle) == []

    def test_a_tracked_file_re_included_by_a_negation_is_not_a_finding(self, repo):
        repo.write(".gitignore", "*.log\n!keep.log\n")
        repo.write("keep.log", "")
        repo.track("keep.log")
        assert tracked(repo.handle) == []

    def test_files_are_grouped_under_the_rule_that_matched(self, repo):
        repo.write(".gitignore", "*.log\n")
        for name in ("a.log", "b.log", "c.log"):
            repo.write(name, "")
        repo.track("a.log", "b.log", "c.log")
        (found,) = tracked(repo.handle)
        assert found.paths == ["a.log", "b.log", "c.log"]
        assert "3 files" in found.summary

    def test_long_lists_are_cut_and_the_rest_counted(self, repo):
        repo.write(".gitignore", "*.log\n")
        names = [f"{n}.log" for n in range(10)]
        for name in names:
            repo.write(name, "")
        repo.track(*names)
        (found,) = tracked(repo.handle, max_paths=3)
        assert len(found.paths) == 3
        assert found.extra == 7

    def test_a_nested_ignore_file_gets_the_blame_for_its_own_rule(self, repo):
        repo.write(".gitignore", "*.tmp\n")
        repo.write("src/.gitignore", "*.log\n")
        repo.write("src/app.log", "")
        repo.track("src/app.log")
        (found,) = tracked(repo.handle)
        assert (found.source, found.line) == ("src/.gitignore", 1)

    def test_info_exclude_gets_the_blame_for_its_own_rule(self, repo):
        repo.write(".git/info/exclude", "secret.txt\n")
        repo.write("secret.txt", "")
        repo.track("secret.txt")
        (found,) = tracked(repo.handle)
        assert found.source == ".git/info/exclude"


class TestUnreachableNegation:
    def test_the_classic(self, repo):
        repo.write(".gitignore", "build/\n!build/keep.txt\n")
        found = findings_for(repo)
        assert lines(found, UNREACHABLE_NEGATION) == [(".gitignore", 2)]
        assert found[0].summary == (
            "never re-includes build/keep.txt: .gitignore:1: build/ decides it instead"
        )

    def test_ignore_everything_then_re_include_one_file(self, repo):
        # The other half of the same mistake: `*` excludes `src` as a directory,
        # so nothing under it can come back either.
        repo.write(".gitignore", "*\n!src/main.c\n")
        found = findings_for(repo)
        assert lines(found, UNREACHABLE_NEGATION) == [(".gitignore", 2)]

    def test_order_in_the_file_does_not_matter(self, repo):
        # Last match wins for a path, but a directory that ends up excluded is
        # excluded whichever line did it, so the negation is still unreachable.
        repo.write(".gitignore", "!build/keep.txt\nbuild/\n")
        assert lines(findings_for(repo), UNREACHABLE_NEGATION) == [(".gitignore", 1)]

    def test_no_finding_when_the_directory_is_re_included(self, repo):
        # This is the working form of the idiom, and it must stay quiet.
        repo.write(".gitignore", "build/*\n!build/keep.txt\n")
        assert lines(findings_for(repo), UNREACHABLE_NEGATION) == []

    def test_no_finding_when_the_parent_is_not_excluded_at_all(self, repo):
        repo.write(".gitignore", "*.log\n!build/keep.log\n")
        assert lines(findings_for(repo), UNREACHABLE_NEGATION) == []

    def test_a_bare_negation_is_never_reported(self, repo):
        # `!keep.txt` matches at any depth. It is dead inside build/ and alive
        # everywhere else, so calling the rule dead would be wrong.
        repo.write(".gitignore", "build/\n!keep.txt\n")
        assert lines(findings_for(repo), UNREACHABLE_NEGATION) == []

    def test_ancestors_are_relative_to_the_ignore_file(self, repo):
        repo.write(".gitignore", "sub/build/\n")
        repo.write("sub/.gitignore", "!build/keep.txt\n")
        found = findings_for(repo)
        assert lines(found, UNREACHABLE_NEGATION) == [("sub/.gitignore", 1)]
        assert "never re-includes sub/build/keep.txt" in found[0].summary

    def test_the_outermost_blocker_is_the_one_named(self, repo):
        # Two directories on the way down are excluded. Git stops at the first
        # one, so that is the rule it names -- and it is the one worth fixing,
        # since fixing the inner one changes nothing.
        repo.write(".gitignore", "a/\na/b/\n")
        repo.write(".git/info/exclude", "!a/b/c.txt\n")
        (found,) = [f for f in findings_for(repo) if f.check == UNREACHABLE_NEGATION]
        assert found.source == ".git/info/exclude"
        assert ".gitignore:1: a/ decides it instead" in found.summary

    def test_a_negation_inside_an_unread_file_is_not_reported_twice(self, repo):
        repo.write(".gitignore", "vendor/\n")
        repo.write("vendor/.gitignore", "!pkg/keep.txt\n")
        found = findings_for(repo)
        assert lines(found, UNREACHABLE_NEGATION) == []
        assert lines(found, UNREAD_FILE) == [("vendor/.gitignore", None)]


class TestUnreadFile:
    def test_an_ignore_file_under_an_excluded_directory(self, repo):
        repo.write(".gitignore", "vendor/\n")
        repo.write("vendor/.gitignore", "*.o\n!important.o\n")
        (found,) = unread_files(collect(repo.path, repo.handle))
        assert found.check == UNREAD_FILE
        assert found.source == "vendor/.gitignore"
        assert found.line is None
        assert found.text == "2 rules"
        assert "vendor/ is excluded by .gitignore:1: vendor/" in found.summary

    def test_a_readable_nested_file_is_not_a_finding(self, repo):
        repo.write(".gitignore", "*.log\n")
        repo.write("src/.gitignore", "*.o\n")
        assert unread_files(collect(repo.path, repo.handle)) == []

    def test_tracked_content_does_not_rescue_the_file(self, repo):
        # Git may well walk into vendor/ for the tracked file. It still does not
        # matter: tracked paths ignore ignore rules, and untracked ones are
        # already excluded by the rule on vendor/ itself.
        repo.write(".gitignore", "vendor/\n")
        repo.write("vendor/.gitignore", "*.o\n")
        repo.write("vendor/thing.c", "")
        repo.track("vendor/thing.c")
        (found,) = unread_files(collect(repo.path, repo.handle))
        assert found.source == "vendor/.gitignore"


class TestShadowed:
    def test_a_repeated_pattern_kills_the_earlier_one(self, repo):
        repo.write(".gitignore", "*.log\n*.tmp\n*.log\n")
        (found,) = shadowed(collect(repo.path, repo.handle))
        assert (found.source, found.line) == (".gitignore", 1)
        assert "line 3 repeats it" in found.summary

    def test_a_later_negation_kills_the_earlier_rule(self, repo):
        repo.write(".gitignore", "build\n!build\n")
        (found,) = shadowed(collect(repo.path, repo.handle))
        assert found.line == 1
        assert "line 2 is reversed by it" in found.summary

    def test_a_later_rule_kills_an_earlier_negation(self, repo):
        repo.write(".gitignore", "!build\nbuild\n")
        (found,) = shadowed(collect(repo.path, repo.handle))
        assert found.line == 1

    def test_three_copies_report_the_first_two(self, repo):
        repo.write(".gitignore", "*.log\n*.log\n*.log\n")
        assert [f.line for f in shadowed(collect(repo.path, repo.handle))] == [1, 2]

    def test_the_same_pattern_in_a_different_file_is_not_shadowing(self, repo):
        # A rule in src/ applies to src/. The root rule still decides everything
        # outside it, so neither is dead.
        repo.write(".gitignore", "*.log\n")
        repo.write("src/.gitignore", "*.log\n")
        assert shadowed(collect(repo.path, repo.handle)) == []

    def test_different_patterns_are_left_alone(self, repo):
        # No attempt is made to work out whether one glob covers another.
        repo.write(".gitignore", "app.log\n*.log\n")
        assert shadowed(collect(repo.path, repo.handle)) == []


class TestScan:
    def test_a_clean_repository_reports_nothing(self, repo):
        repo.write(".gitignore", "build/*\n!build/keep.txt\n*.log\n")
        repo.write("src/main.c", "")
        repo.track("src/main.c")
        assert findings_for(repo) == []

    def test_a_repository_with_no_ignore_files_reports_nothing(self, repo):
        repo.write("src/main.c", "")
        repo.track("src/main.c")
        assert findings_for(repo) == []

    def test_findings_come_back_sorted_by_file_and_line(self, repo):
        repo.write(".gitignore", "vendor/\n!vendor/keep.txt\n*.log\n*.log\n")
        repo.write("vendor/.gitignore", "*.o\n")
        repo.write("app.log", "")
        repo.track("app.log")
        found = findings_for(repo)
        assert [(f.source, f.line, f.check) for f in found] == [
            (".gitignore", 2, UNREACHABLE_NEGATION),
            (".gitignore", 3, SHADOWED),
            (".gitignore", 4, TRACKED),
            ("vendor/.gitignore", None, UNREAD_FILE),
        ]
