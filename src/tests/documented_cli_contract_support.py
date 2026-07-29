from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from core.run_outcome import (
    AcceptedAttemptId,
    PhaseId,
    ReviewOutcome,
    ReviewRound,
    ReviewVerdict,
    RunOutcome,
    TerminalAnchor,
    TerminalOutcome,
    WorkflowTerminal,
)

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = (
    ROOT / "README.md",
    ROOT / "README.en.md",
    ROOT / "src" / "docs" / "E2E_TESTING.md",
    ROOT / "src" / "docs" / "full_agent_io_logging_design.md",
)
EXAMPLE_TAG = re.compile(
    r"<!-- cli-contract:(?P<name>[a-z0-9-]+) -->\s*```bash\s*(?P<body>.*?)```",
    re.DOTALL,
)
FLAG_ROW = re.compile(r"^\|\s*`(?P<flag>--[a-z0-9-]+)`\s*\|", re.MULTILINE)
FLAG_TABLE = re.compile(
    "".join(
        (
            r"<!-- cli-contract:python-flags:start -->(?P<body>.*?)",
            r"<!-- cli-contract:python-flags:end -->",
        )
    ),
    re.DOTALL,
)


class ParsedCli(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    project_dir: Path | None
    continue_from: Path | None
    agent: str | None
    output_dir: Path | None
    user_constraints: Path | None
    framework_config: str | None
    verbose: bool
    workflow_path: Path | None
    server_url: str | None
    max_phase5_iter: int
    max_review_iter: list[int] | None
    review_fail_closed: bool | None
    container_retention: str
    save_agent_trace: bool | None
    keep_temp_dir: bool
    review_gate: bool
    server_auto_start: bool
    server_no_auto_start: bool
    server_port: int
    opencode_readiness: str
    opencode_message_timeout: int


@dataclass(frozen=True, slots=True)
class DocumentedExample:
    name: str
    argv: tuple[str, ...]


def documented_examples(path: Path) -> tuple[DocumentedExample, ...]:
    text = path.read_text(encoding="utf-8")
    matches = tuple(EXAMPLE_TAG.finditer(text))
    assert matches, f"no parser-backed CLI example in {path}"
    examples: list[DocumentedExample] = []
    for match in matches:
        command = match.group("body").replace("\\\n", " ")
        tokens = shlex.split(command, posix=True)
        module_index = tokens.index("tests.e2e.e2e_test_v3")
        examples.append(
            DocumentedExample(match.group("name"), tuple(tokens[module_index + 1 :]))
        )
    return tuple(examples)


def review_outcome(
    *,
    validation: bool,
    review: ReviewOutcome,
    strict: bool,
) -> TerminalOutcome:
    rounds = ()
    if review is not ReviewOutcome.DISABLED:
        verdict = {
            ReviewOutcome.ACCEPTED: ReviewVerdict.ACCEPT,
            ReviewOutcome.REJECT_EXHAUSTED: ReviewVerdict.REJECT,
            ReviewOutcome.IMPROVEMENT_ERROR: ReviewVerdict.REJECT,
            ReviewOutcome.UNKNOWN: ReviewVerdict.UNKNOWN,
            ReviewOutcome.SESSION_ERROR: ReviewVerdict.UNKNOWN,
        }.get(review)
        if verdict is None:
            raise AssertionError(f"unsupported terminal fixture outcome: {review}")
        rounds = (ReviewRound(1, 1, verdict, review),)
    outcome = RunOutcome(
        validation_succeeded=validation,
        review_outcome=review,
        review_fail_closed=strict,
        workflow_terminal=WorkflowTerminal("complete"),
        terminal_anchor=TerminalAnchor(PhaseId("phase_5_validation")),
        executed_phases=(PhaseId("phase_5_validation"),),
        accepted_attempt_id=(
            AcceptedAttemptId("phase-5-attempt-cli") if validation else None
        ),
        review_rounds=rounds,
    )
    return outcome.terminal_outcome


def run_optional_real_opencode(tmp_path: Path) -> None:
    if os.environ.get("SEAM_RUN_REAL_OPENCODE_PHASE_0_3") != "1":
        pytest.skip("opt in with SEAM_RUN_REAL_OPENCODE_PHASE_0_3=1")
    from tests.integration import server_available

    if not server_available():
        pytest.skip("real OpenCode service or model credentials are unavailable")
    from tests.integration.test_full_phase_0_to_3 import (
        test_run_phase_0_to_3_with_real_session_manager,
    )

    test_run_phase_0_to_3_with_real_session_manager(tmp_path)


def find_executable(name: str) -> str | None:
    return shutil.which(name)


def run_command(
    args: list[str],
    *,
    capture_output: bool,
    text: bool,
    check: bool,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=capture_output,
        text=text,
        check=check,
        timeout=timeout,
    )


def run_optional_generic_cpu_docker(tmp_path: Path) -> None:
    if os.environ.get("SEAM_RUN_GENERIC_CPU_DOCKER") != "1":
        pytest.skip("opt in with SEAM_RUN_GENERIC_CPU_DOCKER=1")
    docker = find_executable("docker")
    image = os.environ.get("SEAM_GENERIC_CPU_DOCKER_IMAGE")
    if docker is None or image is None:
        pytest.skip("Docker or SEAM_GENERIC_CPU_DOCKER_IMAGE is unavailable")
    inspect = run_command(
        [docker, "image", "inspect", image],
        capture_output=True,
        text=True,
        check=False,
    )
    if inspect.returncode != 0:
        pytest.skip(
            "the requested local CPU image is unavailable; no pull is attempted"
        )

    completed = run_command(
        [
            docker,
            "run",
            "--rm",
            "--pull=never",
            "--network",
            "none",
            "--mount",
            f"type=bind,source={tmp_path.resolve()},target=/workspace",
            image,
            "python",
            "-c",
            (
                "from pathlib import Path; "
                "Path('/workspace/seam-cpu-docker.txt').write_text("
                "'SEAM_CPU_DOCKER_OK', encoding='utf-8')"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "seam-cpu-docker.txt").read_text(encoding="utf-8") == (
        "SEAM_CPU_DOCKER_OK"
    )
