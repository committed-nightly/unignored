"""Parsing, and the walk that decides which ignore files git ever reads."""

from __future__ import annotations

from unignored.rules import Rule, collect, literal_path, parse, strip_trailing_space


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


class TestLiteralPath:
    def test_a_literal_pattern_names_its_path(self):
        assert literal_path(rule("!build/keep.txt")) == "build/keep.txt"

    def test_deeper(self):
        assert literal_path(rule("!a/b/c.txt")) == "a/b/c.txt"

    def test_leading_slash_anchors_but_is_not_part_of_the_path(self):
        assert literal_path(rule("!/src/main.c")) == "src/main.c"

    def test_a_bare_name_names_no_one_path(self):
        # `keep.txt` matches at any depth: dead inside an excluded directory and
        # alive everywhere else, which is not a single answer.
        assert literal_path(rule("!keep.txt")) is None

    def test_a_glob_names_a_set_not_a_path(self):
        assert literal_path(rule("!build/*.keep")) is None
        assert literal_path(rule("!**/build/keep.txt")) is None
        assert literal_path(rule("!build/keep?.txt")) is None
        assert literal_path(rule("!build/[abc].txt")) is None

    def test_a_directory_pattern_is_declined(self):
        # Asking git about a directory that may not exist brings back the
        # trailing-slash trap the file probe exists to avoid.
        assert literal_path(rule("!build/sub/")) is None

    def test_a_backslash_is_declined(self):
        # An escape could mean several things; guessing would invent findings.
        assert literal_path(rule("!a\\ b/c.txt")) is None


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
