"""Parsing, and the walk that decides which ignore files git ever reads."""

from __future__ import annotations

from unignored.rules import Rule, collect, literal_ancestors, parse, strip_trailing_space


def rule(text: str) -> Rule:
    return Rule(source=".gitignore", line=1, text=text, negated=text.startswith("!"))


class TestParse:
    def test_line_numbers_count_blanks_and_comments(self):
        # Git reports the real line number, so a report that renumbered would
        # point at the wrong line in every file with a comment in it.
        rules = parse("# a comment\n\n*.log\n", ".gitignore")
        assert [(r.line, r.text) for r in rules] == [(3, "*.log")]

    def test_comment_and_blank_produce_no_rules(self):
        assert parse("#x\n\n   \n", ".gitignore") == []

    def test_escaped_hash_is_a_pattern(self):
        assert [r.text for r in parse("\\#notacomment\n", ".gitignore")] == ["\\#notacomment"]

    def test_negation_is_recorded_and_stripped_from_the_body(self):
        (negated,) = parse("!keep.txt\n", ".gitignore")
        assert negated.negated
        assert negated.body == "keep.txt"

    def test_crlf_files_do_not_grow_a_carriage_return(self):
        (only,) = parse("*.log\r\n", ".gitignore")
        assert only.text == "*.log"

    def test_no_trailing_newline(self):
        assert [r.text for r in parse("*.log", ".gitignore")] == ["*.log"]


class TestTrailingSpace:
    # gitignore(5): trailing spaces are ignored unless quoted with a backslash.
    def test_unquoted_trailing_space_is_dropped(self):
        assert strip_trailing_space("*.log   ") == "*.log"

    def test_backslash_quotes_one_space(self):
        assert strip_trailing_space("name\\ ") == "name\\ "

    def test_two_backslashes_do_not_quote_the_space(self):
        assert strip_trailing_space("name\\\\ ") == "name\\\\"

    def test_tabs_go_too(self):
        assert strip_trailing_space("*.log\t") == "*.log"

    def test_leading_space_is_kept(self):
        assert strip_trailing_space("  name") == "  name"


class TestLiteralAncestors:
    def test_one_directory(self):
        assert literal_ancestors(rule("!build/keep.txt")) == ["build"]

    def test_several(self):
        assert literal_ancestors(rule("!a/b/c.txt")) == ["a", "a/b"]

    def test_leading_slash_anchors_but_is_not_a_component(self):
        assert literal_ancestors(rule("!/src/main.c")) == ["src"]

    def test_a_bare_name_is_pinned_under_nothing(self):
        # `keep.txt` matches at any depth, so no one directory can be blamed.
        assert literal_ancestors(rule("!keep.txt")) == []

    def test_glob_stops_the_walk(self):
        assert literal_ancestors(rule("!build/*/keep.txt")) == ["build"]

    def test_leading_globstar_yields_nothing(self):
        assert literal_ancestors(rule("!**/build/keep.txt")) == []

    def test_trailing_slash_is_not_a_component(self):
        assert literal_ancestors(rule("!build/sub/")) == ["build"]

    def test_backslash_stops_the_walk(self):
        # An escape could mean anything; guessing at it would invent findings.
        assert literal_ancestors(rule("!a\\*b/c.txt")) == []


class TestCollect:
    def test_finds_root_and_nested_files(self, repo):
        repo.write(".gitignore", "*.log\n")
        repo.write("src/.gitignore", "*.tmp\n")
        found = {f.path: f for f in collect(repo.path, repo.handle)}
        assert set(found) == {".gitignore", "src/.gitignore"}
        assert all(f.read for f in found.values())

    def test_info_exclude_is_an_ignore_file_too(self, repo):
        repo.write(".git/info/exclude", "scratch/\n")
        found = {f.path for f in collect(repo.path, repo.handle)}
        assert ".git/info/exclude" in found

    def test_a_file_under_an_excluded_directory_is_unread(self, repo):
        repo.write(".gitignore", "vendor/\n")
        repo.write("vendor/.gitignore", "*.o\n")
        found = {f.path: f for f in collect(repo.path, repo.handle)}
        assert found["vendor/.gitignore"].read is False
        assert found["vendor/.gitignore"].unread_because.pattern == "vendor/"

    def test_the_walk_stops_at_an_excluded_directory(self, repo):
        # The deeper file is not reported separately: its parent is already the
        # finding, and descending into an ignored tree is what makes a linter
        # too slow to keep in CI.
        repo.write(".gitignore", "vendor/\n")
        repo.write("vendor/deep/.gitignore", "*.o\n")
        found = {f.path for f in collect(repo.path, repo.handle)}
        assert "vendor/deep/.gitignore" not in found

    def test_a_re_included_directory_is_still_walked(self, repo):
        repo.write(".gitignore", "vendor/\n!vendor\n")
        repo.write("vendor/.gitignore", "*.o\n")
        found = {f.path: f for f in collect(repo.path, repo.handle)}
        assert found["vendor/.gitignore"].read is True

    def test_the_git_directory_is_not_walked(self, repo):
        repo.write(".git/hooks/.gitignore", "*\n")
        found = {f.path for f in collect(repo.path, repo.handle)}
        assert found == set()

    def test_submodules_are_left_alone(self, repo):
        # A submodule has its own index and its own rules; judging it from out
        # here would get both wrong.
        repo.write("sub/.git", "gitdir: ../.git/modules/sub\n")
        repo.write("sub/.gitignore", "*.o\n")
        found = {f.path for f in collect(repo.path, repo.handle)}
        assert found == set()
