"""Interactive initializer CLI entry point.

No-argument human invocation drives the full workflow via the default
:class:`InteractivePort` (backed by ``input()`` and ``getpass.getpass()``).
``--non-interactive --answers <json>`` runs deterministically through typed
:class:`~seam_init.answers.Answers`; secrets are referenced by env-var name in
the answers file and resolved at use-site, never inline or in argv. The
``workflow_runner`` parameter lets tests inject a fake without touching the
real workflow.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, final, runtime_checkable

from core.secret_redaction import redact_sensitive_text
from seam_init.answers import Answers, AnswersLoadError, load_answers
from seam_init.workflow_types import NonInteractivePromptReached

__all__ = [
    "InteractivePort", "NonInteractivePort", "NonInteractivePromptReached",
    "PromptPort", "WorkflowRunner", "build_parser", "load_answers", "main",
    "parse_args",
]


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the initializer CLI."""
    parser = argparse.ArgumentParser(
        prog="seam_init.cli",
        description="SEAM interactive initializer (guided setup).",
    )
    _ = parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="run without stdin/getpass prompts; requires --answers.",
    )
    _ = parser.add_argument(
        "--answers",
        type=Path,
        default=None,
        help="JSON answers file for --non-interactive mode.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments; ``argv`` None reads ``sys.argv``."""
    parser = build_parser()
    if argv is None:
        return parser.parse_args()
    return parser.parse_args(argv)


@runtime_checkable
class PromptPort(Protocol):
    """Injectable boundary for every human-facing prompt."""

    def ask(self, prompt: str, *, default: str | None = None) -> str: ...
    def secret(self, prompt: str) -> str: ...
    def confirm(self, prompt: str, *, default: bool = False) -> bool: ...


@final
class InteractivePort:
    """Default prompt port backed by ``input()`` and ``getpass.getpass()``."""

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        suffix = f" [{default}]" if default is not None else ""
        response = input(f"{prompt}{suffix}: ").strip()
        if not response and default is not None:
            return default
        return response

    def secret(self, prompt: str) -> str:
        return getpass.getpass(f"{prompt}: ")

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        marker = "Y/n" if default else "y/N"
        response = input(f"{prompt} [{marker}]: ").strip().lower()
        if not response:
            return default
        return response in {"y", "yes"}


@final
class NonInteractivePort:
    """Guard port: any call proves the answers file was incomplete."""

    def ask(self, prompt: str, *, default: str | None = None) -> str:
        raise NonInteractivePromptReached(
            f"ask() reached unexpectedly (prompt={prompt!r}, default={default!r})",
        )

    def secret(self, prompt: str) -> str:
        raise NonInteractivePromptReached(f"secret() reached unexpectedly: {prompt!r}")

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        raise NonInteractivePromptReached(
            f"confirm() reached unexpectedly (prompt={prompt!r}, default={default!r})",
        )


WorkflowRunner = Callable[[PromptPort, Answers | None], int]


def _coerce_system_exit(exc: SystemExit) -> int:
    code = exc.code
    if isinstance(code, int):
        return code
    if isinstance(code, str) and code.strip().isdigit():
        return int(code)
    return 2


def _resolve_paths() -> tuple[Path, Path]:
    """Return (project_root, seam_source_path) derived from this file's location.

    ``SEAM_INIT_PROJECT_ROOT`` overrides the project root; the SEAM source path
    always follows this file's location. The override is the public injection
    seam the E2E suite uses to keep every workflow side effect inside a
    temporary sandbox instead of the repository that hosts ``src``.
    """
    src_dir = Path(__file__).resolve().parent.parent
    override = os.environ.get("SEAM_INIT_PROJECT_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve(), src_dir
    return src_dir.parent, src_dir


def _default_runner(prompt: PromptPort, answers: Answers | None) -> int:
    """Build production ports, run the real workflow, render, persist, return exit."""
    from seam_init.environment import (  # noqa: PLC0415
        InterpreterSubprocessProbe, SubprocessVenvCreator,
    )

    class _ProductionOpencodeInstaller:
        def install(self):
            from seam_init.opencode_install import InstallRequest, ensure_opencode  # noqa: PLC0415
            return ensure_opencode(InstallRequest())
    from seam_init.omo_adapters import OmoCommand, SubprocessOmoCapabilityPort  # noqa: PLC0415
    from seam_init.omo_install import (  # noqa: PLC0415
        JsoncPluginRegistrar, SubprocessInstaller, default_bun_dir,
    )
    from seam_init.omo_validation_subprocess import SubprocessOmoCommandPort  # noqa: PLC0415
    from seam_init.opencode_adapters import (  # noqa: PLC0415
        OpencodeCommand, OpencodeSchemaValidator, SubprocessRuntimePort,
    )
    from seam_init.opencode_subprocess import (  # noqa: PLC0415
        SubprocessDiagnoseRunner, SubprocessServerLifecycle, SubprocessVersionProbe,
    )
    from seam_init.reporting import persist_report, render_terminal  # noqa: PLC0415
    from seam_init.seam_install import SubprocessPipRunner  # noqa: PLC0415
    from seam_init.subscription_map import FamilySubscriptionSelector  # noqa: PLC0415
    from seam_init.workflow import run_workflow  # noqa: PLC0415
    from seam_init.workflow_types import (  # noqa: PLC0415
        ConfirmOnlyPort, WorkflowFacts, WorkflowPorts, WorkflowRequest,
    )

    project_root, seam_source = _resolve_paths()
    oc_cmd = OpencodeCommand(argv=("opencode",), cwd=project_root)
    omo_cmd = OmoCommand(argv=("bunx", "oh-my-openagent"), cwd=project_root)
    installer = SubprocessInstaller(bun_install_dir=default_bun_dir())

    stage_prompt = prompt if answers is None else ConfirmOnlyPort()
    ports = WorkflowPorts(
        venv_creator=SubprocessVenvCreator(),
        interpreter_probe=InterpreterSubprocessProbe(),
        pip_runner=SubprocessPipRunner(),
        opencode_installer=_ProductionOpencodeInstaller(),
        opencode_runtime=SubprocessRuntimePort(command=oc_cmd),
        schema_validator=OpencodeSchemaValidator(command=oc_cmd),
        subscription_selector=FamilySubscriptionSelector(),
        bun_installer=installer, omo_installer=installer,
        plugin_registrar=JsoncPluginRegistrar(project_root=project_root),
        omo_capability=SubprocessOmoCapabilityPort(command=omo_cmd),
        server_lifecycle=SubprocessServerLifecycle(),
        diagnose_runner=SubprocessDiagnoseRunner(),
        version_probe=SubprocessVersionProbe(),
        omo_command=SubprocessOmoCommandPort(),
    )
    request = WorkflowRequest(
        project_root=project_root, seam_source_path=seam_source,
        prompt=stage_prompt, ports=ports, answers=answers,
        base_env=dict(os.environ),
    )
    facts = WorkflowFacts()
    outcome = run_workflow(request, facts_out=facts)
    print(render_terminal(outcome, facts), flush=True)
    try:
        persist_report(project_root, outcome, facts)
    except OSError as exc:
        safe_msg = redact_sensitive_text(str(exc))
        print(f"Warning: report persistence failed: {safe_msg}", file=sys.stderr, flush=True)
    return outcome.exit_code


def main(
    argv: list[str] | None = None,
    prompt_port: PromptPort | None = None,
    *,
    workflow_runner: WorkflowRunner | None = None,
) -> int:
    """CLI entry point. Returns a process exit code (0, 2, 60, or 61-69)."""
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return _coerce_system_exit(exc)

    runner = workflow_runner or _default_runner

    if args.non_interactive:
        if args.answers is None:
            print("Error: --non-interactive requires --answers <json>.", file=sys.stderr)
            return 2
        try:
            answers = load_answers(args.answers)
        except AnswersLoadError as exc:
            print(f"Error: {exc.reason}", file=sys.stderr)
            return 2
        port: PromptPort = prompt_port if prompt_port is not None else NonInteractivePort()
        try:
            return runner(port, answers)
        except NonInteractivePromptReached as exc:
            print(f"Error: non-interactive answers incomplete: {exc}", file=sys.stderr)
            return 2

    port = prompt_port if prompt_port is not None else InteractivePort()
    return runner(port, None)


if __name__ == "__main__":
    sys.exit(main())
