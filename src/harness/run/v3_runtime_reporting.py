from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Callable, Protocol, Union, final

from core.compat import TypeAlias

from core.phase5_attempt_receipt import AttemptReceiptError
from core.resource_manifest import ResourceManifestError
from core.run_outcome import TerminalOutcome
from core.v3_runtime_report import (
    RuntimeFact,
    RuntimeReportRequest,
    V3RuntimeReport,
    build_runtime_report,
)

from .models import RunArtifactUpdate

JsonScalar: TypeAlias = Union[str, int, float, bool, None]
JsonValue: TypeAlias = "JsonScalar | list[JsonValue] | dict[str, JsonValue]"
PostCleanupHook: TypeAlias = Callable[[TerminalOutcome], RunArtifactUpdate]


class RuntimeTelemetrySink(Protocol):
    def set_metadata(self, key: str, value: Mapping[str, JsonValue]) -> None: ...

    def record_event(self, event_type: str, **details: JsonValue) -> None: ...


@final
class V3RuntimeReportRecorder:
    """Capture one projection so telemetry and summary share identical data."""

    __slots__ = ("_existing", "_report", "_request", "_telemetry")

    def __init__(
        self,
        request: RuntimeReportRequest,
        telemetry: RuntimeTelemetrySink,
        existing: PostCleanupHook,
    ) -> None:
        self._request = request
        self._telemetry = telemetry
        self._existing = existing
        self._report: V3RuntimeReport | None = None

    def __call__(self, outcome: TerminalOutcome) -> RunArtifactUpdate:
        report: V3RuntimeReport | None = None
        try:
            try:
                report = build_runtime_report(self._request)
            except (AttemptReceiptError, ResourceManifestError, OSError) as exc:
                unavailable = build_runtime_report(
                    replace(
                        self._request,
                        manifest_store=None,
                        accepted_receipt=None,
                    )
                )
                report = unavailable.model_copy(
                    update={"diagnostics": (*unavailable.diagnostics, str(exc))}
                )
            self._report = report
            payload = report.model_dump(mode="json")
            try:
                self._telemetry.set_metadata("runtime", payload)
                self._telemetry.record_event("runtime_reported", runtime=payload)
            except Exception:  # noqa  # noqa: BROAD_EXCEPT_OK - protocol boundary
                self._report = report
        except (TypeError, ValueError):
            self._report = report
        try:
            return self._existing(outcome)
        except Exception:  # noqa  # noqa: BROAD_EXCEPT_OK - telemetry persistence
            return RunArtifactUpdate()

    def read(self) -> V3RuntimeReport | None:
        return self._report


def _fact(facts: tuple[RuntimeFact, ...], name: str) -> RuntimeFact | None:
    return next((fact for fact in facts if fact.name == name), None)


def _display(fact: RuntimeFact | None) -> str:
    if fact is None:
        return "unavailable"
    value = fact.value or fact.status.value
    return f"{value} [{fact.provenance.value}; {fact.namespace}; {fact.status.value}]"


def _active_environment(report: V3RuntimeReport):
    return next(
        (
            environment
            for environment in report.environments
            if environment.environment_id == report.active_environment_id
        ),
        None,
    )


def render_runtime_report_lines(report: V3RuntimeReport) -> tuple[str, ...]:
    effective_backend = _fact(report.execution, "backend.effective")
    lines = [
        f"- Resource manifest: {report.manifest_path or 'unavailable'}",
        f"- Execution mode: {_display(effective_backend)}",
        f"- Requested mode: {_display(_fact(report.execution, 'backend.requested'))}",
        f"- Launcher Python: {_display(_fact(report.launcher, 'launcher.python_realpath'))}",
    ]
    environment = _active_environment(report)
    if environment is None:
        lines.append("- Environment: unavailable")
    else:
        lines.extend(
            (
                f"- Environment: {_display(_fact(environment.facts, 'environment.type'))}",
                f"- Environment Python: {_display(_fact(environment.facts, 'interpreter.sys_executable'))}",
                f"- Python version: {_display(_fact(environment.facts, 'python.version'))}",
            )
        )
    if effective_backend is not None and effective_backend.value == "container":
        lines.extend(
            (
                "- Container: "
                + f"runtime={_display(_fact(report.container, 'container.runtime'))} "
                + f"name={_display(_fact(report.container, 'container.name'))} "
                + f"id={_display(_fact(report.container, 'container.id'))} "
                + f"image={_display(_fact(report.container, 'container.image'))}",
                f"- Container workdir: {_display(_fact(report.container, 'container.workdir'))}",
                "- Container mount: "
                + f"{_display(_fact(report.container, 'container.mount_source'))} -> "
                + f"{_display(_fact(report.container, 'container.mount_destination'))}",
            )
        )
    lines.extend(
        (
            "- Ownership: "
            + f"{_display(_fact(report.container, 'ownership.resource_owner_kind'))}",
            "- Retention: "
            + f"requested={_display(_fact(report.retention, 'retention.requested'))} "
            + f"effective={_display(_fact(report.retention, 'retention.effective'))} "
            + f"cleanup={_display(_fact(report.retention, 'retention.cleanup_result'))}",
            "- OpenCode: "
            + f"endpoint={_display(_fact(report.opencode, 'opencode.endpoint'))} "
            + f"version={_display(_fact(report.opencode, 'opencode.version'))} "
            + f"owner={_display(_fact(report.opencode, 'opencode.owner_kind'))}",
        )
    )
    if report.replay.accepted_attempt_id is not None:
        lines.append(f"- Accepted attempt: {report.replay.accepted_attempt_id}")
    if report.replay.validation_command is not None:
        lines.append(f"- Validation command: {report.replay.validation_command}")
    lines.append(
        f"- Access: {report.access.detail}"
        + (
            f" [{report.access.provenance.value}]"
            if report.access.provenance is not None
            else ""
        )
    )
    if report.access.entry_command is not None:
        lines.append(f"- Container entry: {report.access.entry_command}")
    if report.access.activation_command is not None:
        lines.append(f"- Environment activation: {report.access.activation_command}")
    reason = (
        f" ({report.replay.reason.value})" if report.replay.reason is not None else ""
    )
    lines.append(
        f"- Replay available: {'yes' if report.replay.available else 'no'}{reason}"
    )
    if report.replay.command is not None:
        lines.append(f"- Replay command: {report.replay.command}")
    lines.append(f"- Replay notice: {report.replay.nondeterminism_notice}")
    return tuple(lines)


def print_runtime_report(report: V3RuntimeReport) -> None:
    for line in render_runtime_report_lines(report):
        print(line)
