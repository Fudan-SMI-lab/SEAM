from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.runtime_observability_models import (
    CommandCorrelation,
    ObservabilityContractError,
)
from core.run_outcome import OutcomeContractError
from core.v3_runtime_report_models import RuntimeReplayReport


def test_replay_report_rejects_arbitrary_reason() -> None:
    with pytest.raises(ValidationError):
        _ = RuntimeReplayReport.model_validate(
            {
                "available": False,
                "reason": "attacker_status",
                "accepted_attempt_id": None,
                "validation_command": None,
                "command": None,
                "cwd": None,
                "nondeterminism_notice": "display only",
            }
        )


def test_replay_report_rejects_overlong_attempt_id() -> None:
    with pytest.raises(OutcomeContractError):
        _ = RuntimeReplayReport.model_validate(
            {
                "available": False,
                "reason": "receipt_missing",
                "accepted_attempt_id": "x" * 129,
                "validation_command": None,
                "command": None,
                "cwd": None,
                "nondeterminism_notice": "display only",
            }
        )


def test_observability_rejects_overlong_correlation_identifier() -> None:
    with pytest.raises(ObservabilityContractError):
        _ = CommandCorrelation(
            run_id="r" * 129,
            session_id="session",
            command_id="command",
        )
