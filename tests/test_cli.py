"""The command line: what it prints, and what it exits with.

The exit code is the contract. Everything else is presentation.
"""

from __future__ import annotations

import json

import pytest

from unignored.cli import EXIT_ERROR, EXIT_FOUND, EXIT_OK, main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


class TestExitCodes:
    def test_a_clean_repository_is_zero(self, capsys, repo):
        repo.write(".gitignore", "*.log\n")
        code, out, _ = run(capsys, repo.path)
        assert code == EXIT_OK
        assert "nothing dead" in out

    def test_a_repository_with_no_ignore_files_is_zero(self, capsys, repo):
        # Ignoring nothing is a fine thing to do and the tool has no view on it.
        code, out, _ = run(capsys, repo.path)
        assert code == EXIT_OK
        assert "0 ignore files" in out

    def test_a_finding_is_one(self, capsys, repo):
        repo.write(".gitignore", "build/\n!build/keep.txt\n")
        code, out, _ = run(capsys, repo.path)
        assert code == EXIT_FOUND
        assert "never re-includes build/keep.txt" in out

    def test_not_a_repository_is_two(self, capsys, tmp_path):
        # Two, not one, and emphatically not zero: nobody looked.
        code, _, err = run(capsys, str(tmp_path))
        assert code == EXIT_ERROR
        assert "unignored:" in err

    def test_a_path_that_does_not_exist_is_two(self, capsys, tmp_path):
        code, _, err = run(capsys, str(tmp_path / "nope"))
        assert code == EXIT_ERROR
        assert "no such file or directory" in err

    def test_a_subdirectory_is_checked_as_its_whole_repository(self, capsys, repo):
        # `git rev-parse --show-toplevel`, like every other git command.
        repo.write(".gitignore", "build/\n!build/keep.txt\n")
        repo.mkdir("src")
        code, out, _ = run(capsys, f"{repo.path}/src")
        assert code == EXIT_FOUND
        assert ".gitignore:2" in out


class TestOutput:
    def test_findings_name_a_file_and_a_line(self, capsys, repo):
        repo.write(".gitignore", "*.log\n*.log\n")
        _, out, _ = run(capsys, repo.path)
        assert out.startswith(".gitignore:1: *.log\n")

    def test_a_whole_file_finding_has_no_line_number(self, capsys, repo):
        repo.write(".gitignore", "vendor/\n")
        repo.write("vendor/.gitignore", "*.o\n")
        _, out, _ = run(capsys, repo.path)
        assert "vendor/.gitignore: 1 rule\n" in out

    def test_only_filters_to_one_check(self, capsys, repo):
        repo.write(".gitignore", "vendor/\n*.log\n*.log\n")
        repo.write("vendor/.gitignore", "*.o\n")
        _, everything, _ = run(capsys, repo.path)
        assert "vendor/.gitignore" in everything and ".gitignore:2" in everything

        code, only, _ = run(capsys, repo.path, "--only", "shadowed")
        assert code == EXIT_FOUND
        assert ".gitignore:2" in only
        assert "vendor/.gitignore: 1 rule" not in only

    def test_only_can_filter_everything_away(self, capsys, repo):
        repo.write(".gitignore", "*.log\n*.log\n")
        code, out, _ = run(capsys, repo.path, "--only", "tracked")
        assert code == EXIT_OK
        assert "nothing dead" in out

    def test_an_unknown_check_is_rejected(self, capsys, repo):
        with pytest.raises(SystemExit):
            main([repo.path, "--only", "nonsense"])


class TestJson:
    def test_findings_and_files_are_both_reported(self, capsys, repo):
        repo.write(".gitignore", "build/\n!build/keep.txt\n")
        code, out, _ = run(capsys, repo.path, "--json")
        assert code == EXIT_FOUND
        payload = json.loads(out)
        assert payload["root"] == repo.path
        assert payload["ignore_files"] == [
            {"path": ".gitignore", "read": True, "rules": 2}
        ]
        (found,) = payload["findings"]
        assert found["check"] == "unreachable-negation"
        assert found["line"] == 2
        assert found["text"] == "!build/keep.txt"

    def test_tracked_findings_carry_their_paths(self, capsys, repo):
        repo.write(".gitignore", "*.log\n")
        repo.write("app.log", "")
        repo.track("app.log")
        _, out, _ = run(capsys, repo.path, "--json")
        (found,) = json.loads(out)["findings"]
        assert found["paths"] == ["app.log"]
        assert found["more_paths"] == 0

    def test_a_clean_repository_is_still_valid_json(self, capsys, repo):
        repo.write(".gitignore", "*.log\n")
        code, out, _ = run(capsys, repo.path, "--json")
        assert code == EXIT_OK
        assert json.loads(out)["findings"] == []

    def test_an_unread_file_is_marked_as_such(self, capsys, repo):
        repo.write(".gitignore", "vendor/\n")
        repo.write("vendor/.gitignore", "*.o\n")
        _, out, _ = run(capsys, repo.path, "--json")
        files = {f["path"]: f["read"] for f in json.loads(out)["ignore_files"]}
        assert files == {".gitignore": True, "vendor/.gitignore": False}
