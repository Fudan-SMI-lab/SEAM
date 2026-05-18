# pyright: reportAny=false, reportPrivateUsage=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnusedParameter=false, reportUnusedCallResult=false

"""Tests for sm_adapt_cli argument parsing."""
from pathlib import Path

import pytest
from scripts.sm_adapt_cli import build_parser, parse_args, main


class TestBuildParser:
    def test_parser_has_project_dir(self):
        parser = build_parser()
        # --project-dir is positional-like but actually required optional
        opts = [a.dest for a in parser._actions]
        assert "project_dir" in opts

    def test_parser_has_workflow(self):
        parser = build_parser()
        opts = [a.dest for a in parser._actions]
        assert "workflow" in opts

    def test_parser_has_opencode_url(self):
        parser = build_parser()
        opts = [a.dest for a in parser._actions]
        assert "opencode_url" in opts

    def test_parser_has_command(self):
        parser = build_parser()
        opts = [a.dest for a in parser._actions]
        assert "command" in opts

    def test_parser_has_resume(self):
        parser = build_parser()
        opts = [a.dest for a in parser._actions]
        assert "resume" in opts

    def test_parser_has_verbose(self):
        parser = build_parser()
        opts = [a.dest for a in parser._actions]
        assert "verbose" in opts

    def test_parser_has_max_iterations(self):
        parser = build_parser()
        opts = [a.dest for a in parser._actions]
        assert "max_iterations" in opts

    def test_parser_has_user_constraints(self):
        parser = build_parser()
        opts = [a.dest for a in parser._actions]
        assert "user_constraints" in opts


class TestParseArgs:
    def test_minimal_valid_args(self):
        args = parse_args(["--project-dir", "/path/to/project"])
        assert args.project_dir == "/path/to/project"
        assert args.opencode_url == "http://127.0.0.1:4096"
        assert args.command is None
        assert args.resume is False
        assert args.verbose is False
        assert args.max_iterations == 5
        assert args.workflow is not None

    def test_all_args(self):
        args = parse_args([
            "--project-dir", "/path/to/project",
            "--workflow", "/path/to/workflow.yaml",
            "--opencode-url", "http://custom:8080",
            "--command", "Migrate all kernels",
            "--resume",
            "--verbose",
            "--max-iterations", "10",
            "--user-constraints", "No CPU fallback",
        ])
        assert args.project_dir == "/path/to/project"
        assert args.workflow == "/path/to/workflow.yaml"
        assert args.opencode_url == "http://custom:8080"
        assert args.command == "Migrate all kernels"
        assert args.resume is True
        assert args.verbose is True
        assert args.max_iterations == 10
        assert args.user_constraints == "No CPU fallback"

    def test_user_constraints_string(self):
        args = parse_args(["--project-dir", "/tmp/test", "--user-constraints", "Zero CPU fallback"])
        assert args.user_constraints == "Zero CPU fallback"

    def test_user_constraints_default_none(self):
        args = parse_args(["--project-dir", "/tmp/test"])
        assert args.user_constraints is None

    def test_missing_project_dir_raises(self):
        with pytest.raises(SystemExit) as exc_info:
            parse_args([])
        assert exc_info.value.code == 2

    def test_help_raises_system_exit(self):
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--help"])
        assert exc_info.value.code == 0


class TestMain:
    def test_help_returns_0(self):
        code = main(["--help"])
        assert code == 0

    def test_missing_project_dir_returns_2(self):
        code = main([])
        assert code == 2

    def test_valid_args_returns_0(self, capsys):
        code = main(["--project-dir", "/tmp/test"])
        assert code == 0

    def test_verbose_prints_info(self, capsys):
        code = main([
            "--project-dir", "/tmp/test",
            "--verbose",
        ])
        assert code == 0
        captured = capsys.readouterr()
        assert "[INFO] Project directory: /tmp/test" in captured.out
        assert "[INFO] OpenCode URL: http://127.0.0.1:4096" in captured.out

    def test_verbose_prints_constraints(self, capsys):
        code = main([
            "--project-dir", "/tmp/test",
            "--user-constraints", "Zero CPU fallback",
            "--verbose",
        ])
        assert code == 0
        captured = capsys.readouterr()
        assert "[INFO] User constraints: Zero CPU fallback" in captured.out


def test_resolve_user_constraints_string():
    from scripts.sm_adapt_cli import _resolve_user_constraints

    assert _resolve_user_constraints("Zero CPU fallback") == "Zero CPU fallback"


def test_resolve_user_constraints_file(tmp_path: Path):
    from scripts.sm_adapt_cli import _resolve_user_constraints

    constraint_file = tmp_path / "constraints.md"
    constraint_file.write_text("No CPU fallback\n")
    assert _resolve_user_constraints(str(constraint_file)) == "No CPU fallback"


def test_resolve_user_constraints_none():
    from scripts.sm_adapt_cli import _resolve_user_constraints

    assert _resolve_user_constraints(None) == ""
