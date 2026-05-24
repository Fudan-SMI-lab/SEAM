"""OpenCode-assisted verification for custom-op migration phases."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Protocol, cast

from harness.session.manager import extract_json_response
from core.custom_op_variants import source_template_expanded_variants


TRACK_ORDINARY_CUDA = "ordinary_cuda"
TRACK_CUSTOM_OP = "custom_op"
TRACK_CUSTOM_OP_VARIANT = "custom_op_variant"

PHASE1_CHECK_ID = "phase_1_custom_op_completeness_check"
PHASE3_CHECK_ID = "phase_3_custom_op_contract_coverage_check"


class SessionLike(Protocol):
    def get_or_create(self, role: str, lifecycle: str, agent: str = "") -> str:
        ...

    def send_command(self, session_id: str, command: str, timeout: int | None = None) -> str:
        ...


class ArtifactStoreLike(Protocol):
    def save_phase_output(self, phase_id: str, data: dict[str, object], attempt: int = 0) -> str:
        ...

    def mark_validated(self, phase_id: str, data: dict[str, object]) -> str:
        ...

    def write_journal(self, entry: dict[str, object]) -> str:
        ...


@dataclass(frozen=True)
class AssistedVerificationConfig:
    enabled: bool = False
    phase1_custom_op: bool = True
    phase3_contract_coverage: bool = True
    phase5_diagnostic: bool = True
    require_for_custom_op_signals: bool = True
    skip_when_no_custom_op_signals: bool = True
    max_attempts: int = 2
    timeout_seconds: int = 30000
    verifier_role: str = "custom_op_verifier"
    verifier_lifecycle: str = "persistent"
    verifier_agent: str = "Sisyphus-Junior"

    @classmethod
    def from_framework_config(cls, framework_config: Mapping[str, object] | None) -> "AssistedVerificationConfig":
        if not isinstance(framework_config, Mapping):
            return cls()
        raw = framework_config.get("assisted_verification")
        framework_section = cast(object, framework_config.get("framework"))
        if not isinstance(raw, Mapping) and isinstance(framework_section, Mapping):
            raw = cast(Mapping[str, object], framework_section).get("assisted_verification")
        if not isinstance(raw, Mapping):
            return cls()
        cfg = cast(Mapping[object, object], raw)
        return cls(
            enabled=_bool_cfg(cfg, "enabled", cls.enabled),
            phase1_custom_op=_bool_cfg(cfg, "phase1_custom_op", cls.phase1_custom_op),
            phase3_contract_coverage=_bool_cfg(cfg, "phase3_contract_coverage", cls.phase3_contract_coverage),
            phase5_diagnostic=_bool_cfg(cfg, "phase5_diagnostic", cls.phase5_diagnostic),
            require_for_custom_op_signals=_bool_cfg(cfg, "require_for_custom_op_signals", cls.require_for_custom_op_signals),
            skip_when_no_custom_op_signals=_bool_cfg(cfg, "skip_when_no_custom_op_signals", cls.skip_when_no_custom_op_signals),
            max_attempts=_int_cfg(cfg, "max_attempts", cls.max_attempts, minimum=1, maximum=5),
            timeout_seconds=_int_cfg(cfg, "timeout_seconds", cls.timeout_seconds, minimum=1, maximum=86400),
            verifier_role=_str_cfg(cfg, "verifier_role", cls.verifier_role),
            verifier_lifecycle=_str_cfg(cfg, "verifier_lifecycle", cls.verifier_lifecycle),
            verifier_agent=_str_cfg_any(cfg, ("verifier_agent", "backend_agent", "agent"), cls.verifier_agent),
        )


@dataclass(frozen=True)
class PhaseInventory:
    track: str
    fine_grained_operator_units: tuple[str, ...] = ()
    expanded_unit_identities: tuple[str, ...] = ()
    expanded_operator_instances_count: int = 0
    variant_axes_detected: bool = False

    @property
    def has_custom_ops(self) -> bool:
        return self.track in {TRACK_CUSTOM_OP, TRACK_CUSTOM_OP_VARIANT}

    @property
    def requires_variant_coverage(self) -> bool:
        return self.track == TRACK_CUSTOM_OP_VARIANT


@dataclass
class AssistedVerificationResult:
    phase_id: str
    skipped: bool
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    report: dict[str, object] = field(default_factory=dict)
    raw_path: str = ""
    canonical_path: str = ""
    correction_prompt: str = ""

    @property
    def summary(self) -> dict[str, object]:
        if self.skipped:
            return {"status": "skipped", "phase_id": self.phase_id}
        track = self.report.get("track")
        return {
            "status": "complete" if self.passed else "failed",
            "phase_id": self.phase_id,
            "track": str(track or "unknown"),
            "raw_artifact": self.raw_path,
            "canonical_artifact": self.canonical_path,
            "errors": self.errors,
        }


class AssistedVerificationRunner:
    """Run server-backed source/script verification and compare results."""

    def __init__(
        self,
        *,
        session_mgr: SessionLike,
        artifact_store: ArtifactStoreLike,
        framework_config: Mapping[str, object] | None = None,
    ) -> None:
        self.session_mgr: SessionLike = session_mgr
        self.artifact_store: ArtifactStoreLike = artifact_store
        self.config: AssistedVerificationConfig = AssistedVerificationConfig.from_framework_config(framework_config)

    def verify_phase1(
        self,
        *,
        phase_output: Mapping[str, object],
        project_dir: str,
        attempt: int,
    ) -> AssistedVerificationResult:
        inventory = phase1_inventory(phase_output)
        if self._skip_phase1(inventory):
            return AssistedVerificationResult(PHASE1_CHECK_ID, skipped=True, passed=True)

        prompt = build_phase1_verification_prompt(project_dir=project_dir, phase_output=phase_output, inventory=inventory)
        report_result = self._request_report(check_id=PHASE1_CHECK_ID, prompt=prompt, attempt=attempt)
        if report_result.errors:
            return report_result

        errors = validate_phase1_assisted_report(report_result.report, phase_output)
        if errors and _should_repair_phase1_verifier_report(report_result.report, phase_output):
            repaired_report = self._repair_report(
                check_id=PHASE1_CHECK_ID,
                report=report_result.report,
                errors=errors,
                phase_output=phase_output,
                phase1_output=None,
                attempt=attempt,
            )
            if repaired_report is not None:
                report_result = repaired_report
                errors = validate_phase1_assisted_report(report_result.report, phase_output)
        if errors:
            report_result.passed = False
            report_result.errors = errors
            report_result.correction_prompt = build_phase1_assisted_correction_prompt(
                errors=errors,
                report=report_result.report,
                phase_output=phase_output,
            )
            self._write_journal(PHASE1_CHECK_ID, attempt, report_result, "validation_failed")
            return report_result

        report_result.passed = True
        report_result.canonical_path = self.artifact_store.mark_validated(PHASE1_CHECK_ID, report_result.report)
        self._write_journal(PHASE1_CHECK_ID, attempt, report_result, "succeeded")
        return report_result

    def verify_phase3(
        self,
        *,
        phase_output: Mapping[str, object],
        phase1_output: Mapping[str, object] | None,
        project_dir: str,
        attempt: int,
    ) -> AssistedVerificationResult:
        if phase1_output is None:
            return AssistedVerificationResult(PHASE3_CHECK_ID, skipped=True, passed=True)
        inventory = phase1_inventory(phase1_output)
        if self._skip_phase3(inventory):
            return AssistedVerificationResult(PHASE3_CHECK_ID, skipped=True, passed=True)

        prompt = build_phase3_verification_prompt(
            project_dir=project_dir,
            phase_output=phase_output,
            phase1_output=phase1_output,
            inventory=inventory,
        )
        report_result = self._request_report(check_id=PHASE3_CHECK_ID, prompt=prompt, attempt=attempt)
        if report_result.errors:
            return report_result

        errors = validate_phase3_assisted_report(report_result.report, phase_output, phase1_output)
        if errors:
            report_result.passed = False
            report_result.errors = errors
            report_result.correction_prompt = build_phase3_assisted_correction_prompt(
                errors=errors,
                report=report_result.report,
                phase_output=phase_output,
                inventory=inventory,
            )
            self._write_journal(PHASE3_CHECK_ID, attempt, report_result, "validation_failed")
            return report_result

        report_result.passed = True
        report_result.canonical_path = self.artifact_store.mark_validated(PHASE3_CHECK_ID, report_result.report)
        self._write_journal(PHASE3_CHECK_ID, attempt, report_result, "succeeded")
        return report_result

    def _request_report(self, *, check_id: str, prompt: str, attempt: int) -> AssistedVerificationResult:
        result = AssistedVerificationResult(check_id, skipped=False, passed=False)
        active_prompt = prompt
        for verifier_attempt in range(1, self.config.max_attempts + 1):
            try:
                session_id = self._get_verifier_session()
                raw_response = self._send_verifier_command(session_id, active_prompt)
            except Exception as exc:
                result.errors = [f"assisted verifier session failed: {exc}"]
                result.report = {"phase_id": check_id, "verdict": "unknown", "error": str(exc)}
                result.raw_path = self.artifact_store.save_phase_output(check_id, result.report, attempt=attempt)
                self._write_journal(check_id, attempt, result, "session_failed")
                return result

            parsed = cast(object, extract_json_response(raw_response))
            if isinstance(parsed, Mapping):
                parsed_report = cast(Mapping[str, object], parsed)
                report: dict[str, object] = dict(parsed_report)
            else:
                report = {}
            session_error = _session_error_text(report)
            if session_error:
                result.errors = [f"assisted verifier session failed: {session_error}"]
                result.report = {"phase_id": check_id, "verdict": "unknown", "session_error": session_error}
                result.raw_path = self.artifact_store.save_phase_output(check_id, result.report, attempt=attempt)
                self._write_journal(check_id, attempt, result, "session_failed")
                return result
            if report:
                result.report = report
                result.raw_path = self.artifact_store.save_phase_output(check_id, report, attempt=attempt)
                return result
            if verifier_attempt < self.config.max_attempts:
                active_prompt = (
                    "Your previous assisted-verification response was not a JSON object. "
                    "Return only one complete JSON report matching the requested schema."
                )

        result.errors = ["assisted verifier did not return a parseable JSON object"]
        result.report = {"phase_id": check_id, "verdict": "unknown", "raw_response_unparseable": True}
        result.raw_path = self.artifact_store.save_phase_output(check_id, result.report, attempt=attempt)
        self._write_journal(check_id, attempt, result, "invalid_json")
        return result

    def _repair_report(
        self,
        *,
        check_id: str,
        report: Mapping[str, object],
        errors: Sequence[str],
        phase_output: Mapping[str, object],
        phase1_output: Mapping[str, object] | None,
        attempt: int,
    ) -> AssistedVerificationResult | None:
        prompt = build_assisted_report_repair_prompt(
            check_id=check_id,
            errors=errors,
            report=report,
            phase_output=phase_output,
            phase1_output=phase1_output,
        )
        repaired = self._request_report(check_id=check_id, prompt=prompt, attempt=attempt)
        if repaired.errors:
            return None
        return repaired

    def _get_verifier_session(self) -> str:
        if self.config.verifier_agent:
            try:
                return str(self.session_mgr.get_or_create(
                    role=self.config.verifier_role,
                    lifecycle=self.config.verifier_lifecycle,
                    agent=self.config.verifier_agent,
                ))
            except TypeError:
                pass
        return str(self.session_mgr.get_or_create(
            role=self.config.verifier_role,
            lifecycle=self.config.verifier_lifecycle,
        ))

    def _send_verifier_command(self, session_id: str, prompt: str) -> str:
        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                return self.session_mgr.send_command(
                    session_id,
                    prompt,
                    timeout=None,
                )
            except (TimeoutError, RuntimeError, ConnectionError) as exc:
                last_error = exc
        raise RuntimeError(str(last_error or "unknown assisted verifier session error"))

    def _skip_phase1(self, inventory: PhaseInventory) -> bool:
        if not self.config.enabled or not self.config.phase1_custom_op:
            return True
        return self.config.skip_when_no_custom_op_signals and not inventory.has_custom_ops

    def _skip_phase3(self, inventory: PhaseInventory) -> bool:
        if not self.config.enabled or not self.config.phase3_contract_coverage:
            return True
        return self.config.skip_when_no_custom_op_signals and not inventory.has_custom_ops

    def _write_journal(self, check_id: str, attempt: int, result: AssistedVerificationResult, status: str) -> None:
        try:
            _ = self.artifact_store.write_journal({
                "phase_id": check_id,
                "attempt": attempt,
                "status": status,
                "raw_path": result.raw_path,
                "canonical_path": result.canonical_path,
                "errors": result.errors,
                "warnings": result.warnings,
            })
        except Exception:
            return


def phase1_inventory(phase_output: Mapping[str, object] | None) -> PhaseInventory:
    if not isinstance(phase_output, Mapping):
        return PhaseInventory(track=TRACK_ORDINARY_CUDA)
    surface_obj = phase_output.get("custom_op_surface")
    if not isinstance(surface_obj, Mapping):
        return PhaseInventory(track=TRACK_ORDINARY_CUDA)
    surface = cast(Mapping[str, object], surface_obj)
    if surface.get("custom_op_detected") is not True:
        return PhaseInventory(track=TRACK_ORDINARY_CUDA)

    units = tuple(_string_list(surface.get("fine_grained_operator_units")))
    variants = _variant_unit_ids(surface.get("expanded_operator_variants"))
    generated_variants = _source_template_variant_ids(phase_output, surface)
    if generated_variants and set(generated_variants) != set(variants):
        variants = generated_variants
    declared_count = surface.get("expanded_operator_instances_count")
    count = len(variants)
    if isinstance(declared_count, int) and not isinstance(declared_count, bool) and declared_count == len(variants):
        count = declared_count
    variant_axes_detected = surface.get("variant_axes_detected") is True and bool(variants)
    track = TRACK_CUSTOM_OP_VARIANT if variant_axes_detected else TRACK_CUSTOM_OP
    return PhaseInventory(
        track=track,
        fine_grained_operator_units=tuple(_ordered_unique(units)),
        expanded_unit_identities=tuple(_ordered_unique(variants)),
        expanded_operator_instances_count=count,
        variant_axes_detected=variant_axes_detected,
    )


def validate_phase1_assisted_report(report: Mapping[str, object], phase_output: Mapping[str, object]) -> list[str]:
    errors = _validate_common_report(report, PHASE1_CHECK_ID)
    expected = phase1_inventory(phase_output)
    if expected.track == TRACK_ORDINARY_CUDA:
        return errors

    if str(report.get("track", "")) != expected.track:
        errors.append(f"assisted Phase 1 report track must be {expected.track}")

    phase1_inventory_report = _mapping(report.get("phase1_inventory"))
    source_inventory_report = _mapping(report.get("source_evidence_inventory"))
    reported_units = set(_string_list(phase1_inventory_report.get("fine_grained_operator_units")))
    if not reported_units:
        errors.append("assisted Phase 1 report must list phase1_inventory fine_grained_operator_units")
    if reported_units and reported_units != set(expected.fine_grained_operator_units):
        errors.append("assisted Phase 1 report phase1_inventory fine_grained_operator_units does not match normalized Phase 1 output")

    source_units = set(_string_list(source_inventory_report.get("fine_grained_operator_units")))
    if not source_units:
        errors.append("assisted Phase 1 report must list source_evidence_inventory fine_grained_operator_units")
    if source_units and source_units != set(expected.fine_grained_operator_units):
        missing = sorted(source_units - set(expected.fine_grained_operator_units))
        extra = sorted(set(expected.fine_grained_operator_units) - source_units)
        if missing:
            errors.append("assisted Phase 1 source inventory reports missing normalized units: " + ", ".join(missing))
        if extra:
            errors.append("assisted Phase 1 source inventory omits normalized units: " + ", ".join(extra))

    if expected.requires_variant_coverage:
        surface_obj = phase_output.get("custom_op_surface")
        surface = cast(Mapping[str, object], surface_obj) if isinstance(surface_obj, Mapping) else {}
        concrete_variants = set(_variant_unit_ids(surface.get("expanded_operator_variants")))
        if concrete_variants and set(expected.expanded_unit_identities) != concrete_variants:
            missing = sorted(set(expected.expanded_unit_identities) - concrete_variants)
            extra = sorted(concrete_variants - set(expected.expanded_unit_identities))
            if missing:
                errors.append(
                    "normalized Phase 1 output is incomplete relative to source-template expansion; missing variants: "
                    + ", ".join(missing[:20])
                )
            if extra:
                errors.append(
                    "normalized Phase 1 output has variants outside source-template expansion: "
                    + ", ".join(extra[:20])
                )
        if expected.expanded_operator_instances_count != len(expected.expanded_unit_identities):
            errors.append(
                "normalized Phase 1 expanded_operator_instances_count must match deterministic source-template expanded variant count"
            )
        phase1_variants = set(_string_list(phase1_inventory_report.get("expanded_unit_identities")))
        source_variants = set(_string_list(source_inventory_report.get("expanded_unit_identities")))
        source_axes_prove_variant_scope = _source_axes_prove_variant_scope(source_inventory_report, expected)
        phase1_count_proves_variant_scope = _inventory_count_matches_expected(phase1_inventory_report, expected)
        source_count_proves_variant_scope = _inventory_count_matches_expected(source_inventory_report, expected)
        if _has_variant_placeholder_alias(phase1_variants):
            errors.append("assisted Phase 1 report phase1_inventory expanded_unit_identities contains placeholder aliases")
        if _has_variant_placeholder_alias(source_variants):
            errors.append("assisted Phase 1 source inventory expanded_unit_identities contains placeholder aliases")
        if not source_variants and not source_axes_prove_variant_scope and not source_count_proves_variant_scope:
            errors.append("assisted Phase 1 report must list source_evidence_inventory expanded_unit_identities or source variant axes for custom_op_variant track")
        if not phase1_variants and not phase1_count_proves_variant_scope:
            errors.append("assisted Phase 1 report must list phase1_inventory expanded_unit_identities for custom_op_variant track or prove the exact expanded count")
        if phase1_variants and not _reported_variants_cover_expected(phase1_variants, expected):
            errors.append("assisted Phase 1 report phase1_inventory expanded_unit_identities does not cover normalized Phase 1 output")
        if source_variants and not _reported_variants_cover_expected(source_variants, expected):
            errors.append("assisted Phase 1 source inventory expanded_unit_identities does not cover normalized Phase 1 output")
        count = phase1_inventory_report.get("expanded_operator_instances_count")
        if isinstance(count, int) and not isinstance(count, bool) and count != expected.expanded_operator_instances_count:
            errors.append("assisted Phase 1 report expanded_operator_instances_count does not match normalized Phase 1 output")
    return errors


def validate_phase3_assisted_report(
    report: Mapping[str, object],
    phase_output: Mapping[str, object],
    phase1_output: Mapping[str, object],
) -> list[str]:
    expected = phase1_inventory(phase1_output)
    script_errors = _validate_phase3_entry_script_locally(phase_output, phase1_output, expected)
    future_report_contract = not script_errors and _phase3_script_has_strict_future_report_contract(phase_output, phase1_output, expected)
    future_report_only_blockers = future_report_contract and _phase3_report_blockers_are_future_reports(report)
    errors = [
        error
        for error in _validate_common_report(report, PHASE3_CHECK_ID)
        if not (future_report_only_blockers and error == f"{PHASE3_CHECK_ID} verdict must be complete")
    ]
    if expected.track == TRACK_ORDINARY_CUDA:
        return errors
    errors.extend(script_errors)

    if str(report.get("track", "")) != expected.track:
        errors.append(f"assisted Phase 3 report track must be {expected.track}")
    contract_inventory = _mapping(report.get("phase3_contract_inventory"))
    covered_units = set(_string_list(contract_inventory.get("covered_unit_identities")))
    expected_units = set(expected.fine_grained_operator_units)
    if not covered_units:
        errors.append("assisted Phase 3 report must list covered_unit_identities")
    elif covered_units != expected_units:
        missing_units = sorted(expected_units - covered_units)
        extra_units = sorted(covered_units - expected_units)
        if missing_units:
            errors.append("assisted Phase 3 verifier found missing unit coverage: " + ", ".join(missing_units[:20]))
        if extra_units:
            errors.append("assisted Phase 3 verifier found extra unit coverage: " + ", ".join(extra_units[:20]))

    if expected.requires_variant_coverage:
        covered = set(_string_list(contract_inventory.get("covered_variant_identities")))
        expected_variants = set(expected.expanded_unit_identities)
        if not covered:
            errors.append("assisted Phase 3 report must list covered_variant_identities for custom_op_variant track")
        elif not _reported_variants_cover_expected(covered, expected):
            missing = sorted(expected_variants - covered)
            extra = sorted(covered - expected_variants)
            if missing:
                errors.append("assisted Phase 3 verifier found missing variant coverage: " + ", ".join(missing[:20]))
            if extra:
                errors.append("assisted Phase 3 verifier found extra variant coverage: " + ", ".join(extra[:20]))
    else:
        pass

    representative_only = _as_list(report.get("representative_only_coverage"))
    non_executable = _as_list(report.get("non_executable_or_missing_checks"))
    if representative_only and not (future_report_contract and _messages_are_future_report_obligations(representative_only)):
        errors.append("assisted Phase 3 verifier reported representative-only coverage")
    if non_executable and not (future_report_contract and _messages_are_future_report_obligations(non_executable)):
        errors.append("assisted Phase 3 verifier reported non-executable or missing checks")
    if not _as_list(report.get("validation_script_evidence")):
        errors.append("assisted Phase 3 report must include validation_script_evidence")
    return errors


def build_phase1_verification_prompt(
    *,
    project_dir: str,
    phase_output: Mapping[str, object],
    inventory: PhaseInventory,
) -> str:
    return (
        "# Phase 1 Custom-Op Completeness Verification\n\n"
        "You are the assisted verifier for SEAM. Use OpenCode tools to inspect the project source, build files, wrappers, loaders, tests, and launch paths.\n"
        "This is a read-only verification step: do not modify files, do not create todos, do not launch background/sub-agent tasks, and do not continue after the JSON report.\n"
        "Classify the project as ordinary_cuda, custom_op, or custom_op_variant. For custom-op projects, independently derive every in-scope fine-grained operator unit. For custom-op+variant projects, derive every concrete source-required expanded variant.\n"
        "Compare your independent source evidence against the normalized Phase 1 JSON below. Do not assume representative rows are complete. Do not include CPU/reference/baseline/ctypes/symbol-loader tokens as target variants. Mark unknown/incomplete if any source group is unresolved.\n\n"
        f"Project dir: {project_dir}\n"
        f"Expected track from normalized Phase 1: {inventory.track}\n"
        f"Expected unit count: {len(inventory.fine_grained_operator_units)}\n"
        f"Expected expanded variant count: {len(inventory.expanded_unit_identities)}\n\n"
        "Return only JSON with this schema:\n"
        "{\"phase_id\":\"phase_1_project_analysis\",\"track\":\"custom_op_variant\",\"verdict\":\"complete|incomplete|unknown\",\"checked_source_categories\":[],\"source_paths_checked\":[],\"phase1_inventory\":{\"fine_grained_operator_units\":[],\"variant_axes_detected\":true,\"expanded_operator_instances_count\":0,\"expanded_unit_identities\":[]},\"source_evidence_inventory\":{\"fine_grained_operator_units\":[],\"variant_axes\":{},\"expanded_unit_identities\":[]},\"missing_units\":[],\"extra_units\":[],\"missing_variants\":[],\"extra_variants\":[],\"collapsed_or_representative_rows\":[],\"unresolved_source_groups\":[],\"evidence\":[],\"correction_hints\":[]}\n\n"
        "Normalized Phase 1 JSON:\n"
        f"```json\n{_json_for_prompt(phase_output)}\n```"
    )


def build_phase3_verification_prompt(
    *,
    project_dir: str,
    phase_output: Mapping[str, object],
    phase1_output: Mapping[str, object],
    inventory: PhaseInventory,
) -> str:
    script_excerpt = _entry_script_excerpt(project_dir, phase_output)
    return (
        "# Phase 3 Custom-Op Validation-Coverage Verification\n\n"
        "You are the assisted verifier for SEAM. Use OpenCode tools to inspect the Phase 3 validation script and contract. Verify that the script/contract covers every verified Phase 1 custom-op unit, and for custom_op_variant projects every expanded variant identity.\n"
        "Phase 3 has three routes: ordinary CUDA keeps the existing documented/project entry behavior unchanged; custom-op without variants must create/select a fail-closed validation script that checks per-fine-grained-unit Ascend OPP build/install provenance after Phase 5; custom-op with variants must additionally require one build.json row per expanded target variant, exact unit_identity set equality, and CANN/OPP build/install provenance for every expanded variant.\n"
        "If coverage is representative-only, sampled, family-only, non-executable, or missing per-variant obligations, report incomplete. Do not require Phase 5 migration_reports files to exist during Phase 3; accept a strict Phase 3 script contract when it declares the required reports and fails closed on missing/incomplete manifest, runtime, performance, build, and final-gate rows for every Phase 1 identity. Actual report existence/content is validated in Phase 5. This is a read-only verification step: do not modify files, do not create todos, do not launch background/sub-agent tasks, and do not continue after the JSON report.\n\n"
        f"Project dir: {project_dir}\n"
        f"Expected track: {inventory.track}\n"
        f"Expected units: {len(inventory.fine_grained_operator_units)}\n"
        f"Expected variants: {len(inventory.expanded_unit_identities)}\n\n"
        "Return only JSON with this schema:\n"
        "{\"phase_id\":\"phase_3_entry_script\",\"track\":\"custom_op_variant\",\"verdict\":\"complete|incomplete|unknown\",\"phase1_verified_inventory\":{\"fine_grained_operator_units\":[],\"expanded_unit_identities\":[]},\"phase3_contract_inventory\":{\"covered_unit_identities\":[],\"covered_variant_identities\":[],\"entry_script_path\":\"\"},\"validation_script_evidence\":[],\"missing_units\":[],\"missing_variants\":[],\"representative_only_coverage\":[],\"non_executable_or_missing_checks\":[],\"correction_hints\":[]}\n\n"
        "Verified Phase 1 JSON:\n"
        f"```json\n{_json_for_prompt(phase1_output)}\n```\n\n"
        "Phase 3 JSON:\n"
        f"```json\n{_json_for_prompt(phase_output)}\n```\n\n"
        "Validation script excerpt if readable:\n"
        f"```text\n{script_excerpt}\n```"
    )


def build_phase1_assisted_correction_prompt(
    *,
    errors: Sequence[str],
    report: Mapping[str, object],
    phase_output: Mapping[str, object],
) -> str:
    return (
        "Your Phase 1 project_analysis output failed the assisted custom-op completeness verifier.\n"
        "Use the verifier mismatches below to supplement the missing custom-op units/variants while preserving valid existing findings. Return one complete replacement Phase 1 JSON object only.\n"
        "Do not return a patch, acknowledgement, partial update, or prose. Do not hardcode project-specific counts; enumerate/source-evidence all concrete units and variants required by the project.\n\n"
        f"Verifier errors:\n{_bullet_list(errors)}\n\n"
        f"Verifier report:\n```json\n{_json_for_prompt(report)}\n```\n\n"
        f"Previous Phase 1 JSON:\n```json\n{_json_for_prompt(phase_output)}\n```"
    )


def build_phase3_assisted_correction_prompt(
    *,
    errors: Sequence[str],
    report: Mapping[str, object],
    phase_output: Mapping[str, object],
    inventory: PhaseInventory,
) -> str:
    return (
        "Your Phase 3 entry-script output failed the assisted custom-op validation-coverage verifier.\n"
        "Use the verifier mismatches below to create or update the validation script and return one complete replacement Phase 3 JSON object only.\n"
        "The corrected script/contract must be readable, syntactically valid Python when entry_script_path names a .py file, and executable by the declared run command.\n"
        "It must cover every verified Phase 1 unit and, for custom_op_variant projects, every expanded variant identity. Representative/family-only coverage is invalid.\n\n"
        f"Track: {inventory.track}\n"
        f"Expected units: {len(inventory.fine_grained_operator_units)}\n"
        f"Expected variants: {len(inventory.expanded_unit_identities)}\n\n"
        f"Verifier errors:\n{_bullet_list(errors)}\n\n"
        f"Verifier report:\n```json\n{_json_for_prompt(report)}\n```\n\n"
        f"Previous Phase 3 JSON:\n```json\n{_json_for_prompt(phase_output)}\n```"
    )


def build_assisted_report_repair_prompt(
    *,
    check_id: str,
    errors: Sequence[str],
    report: Mapping[str, object],
    phase_output: Mapping[str, object],
    phase1_output: Mapping[str, object] | None,
) -> str:
    phase1_section = ""
    if phase1_output is not None:
        phase1_section = f"\nVerified Phase 1 JSON:\n```json\n{_json_for_prompt(phase1_output)}\n```\n"
    return (
        "Your previous assisted-verification JSON report failed semantic validation against the normalized SEAM output.\n"
        "Re-check the normalized JSON before changing your verdict. Return only one corrected verifier JSON report with the same schema requested earlier.\n"
        "Do not ask the main phase agent to rewrite its output from this prompt. If your prior report used base units where expanded variants were required, replace them with explicit expanded IDs, grouped base:*N tokens, or exact count-plus-source evidence that proves coverage.\n"
        "Do not use same_as_* placeholder aliases; source_evidence_inventory.expanded_unit_identities must contain concrete source-derived identities, grouped per-unit coverage tokens, or be empty when nested per-unit source variant_axes proves coverage.\n"
        "If the normalized output already enumerates the complete inventory, mark verdict complete and clear missing_variants/missing_units. If source evidence genuinely contradicts it, keep verdict incomplete and list concrete missing units/variants.\n\n"
        f"Check id: {check_id}\n"
        f"Semantic validation errors:\n{_bullet_list(errors)}\n\n"
        f"Previous verifier report:\n```json\n{_json_for_prompt(report)}\n```\n\n"
        f"Normalized phase JSON:\n```json\n{_json_for_prompt(phase_output)}\n```"
        f"{phase1_section}"
    )


def attach_assisted_summary(output: Mapping[str, object], result: AssistedVerificationResult) -> dict[str, object]:
    updated = dict(output)
    existing = updated.get("assisted_verification")
    summary = dict(cast(Mapping[str, object], existing)) if isinstance(existing, Mapping) else {}
    summary[result.phase_id] = result.summary
    updated["assisted_verification"] = summary
    return updated


def _validate_common_report(report: Mapping[str, object], check_id: str) -> list[str]:
    errors: list[str] = []
    verdict = str(report.get("verdict", "")).strip().lower()
    if verdict != "complete":
        errors.append(f"{check_id} verdict must be complete")
    if str(report.get("phase_id", "")).strip() not in {check_id, "phase_1_project_analysis", "phase_3_entry_script"}:
        errors.append(f"{check_id} report phase_id is missing or invalid")
    if not _list_empty(report.get("missing_units")):
        errors.append(f"{check_id} report contains missing_units")
    if not _list_empty(report.get("missing_variants")):
        errors.append(f"{check_id} report contains missing_variants")
    if not _list_empty(report.get("collapsed_or_representative_rows")):
        errors.append(f"{check_id} report contains collapsed_or_representative_rows")
    if not _list_empty(report.get("unresolved_source_groups")):
        errors.append(f"{check_id} report contains unresolved_source_groups")
    if not _as_list(report.get("evidence")) and check_id == PHASE1_CHECK_ID:
        errors.append(f"{check_id} report must include evidence")
    return errors


def _entry_script_excerpt(project_dir: str, phase_output: Mapping[str, object]) -> str:
    resolved = _resolve_phase3_entry_script_path(phase_output, project_dir)
    if resolved is None:
        return "(entry_script_path missing)"
    try:
        project_root = Path(project_dir).resolve()
        if project_root not in resolved.parents and resolved != project_root:
            return "(entry script outside project root)"
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(could not read entry script: {exc})"
    return text[:20000]


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _variant_unit_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, Mapping):
            continue
        unit_id = cast(Mapping[object, object], item).get("unit_identity")
        if isinstance(unit_id, str) and unit_id.strip():
            result.append(unit_id.strip())
    return tuple(_ordered_unique(result))


def _source_template_variant_ids(phase_output: Mapping[str, object], surface: Mapping[str, object]) -> tuple[str, ...]:
    project_dir = phase_output.get("project_dir")
    generated = source_template_expanded_variants(
        surface,
        project_dir=project_dir if isinstance(project_dir, str) else None,
    )
    return _variant_unit_ids(generated)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in cast(list[object], value):
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            text = str(item).strip()
            if text:
                result.append(text)
            continue
        if isinstance(item, Mapping):
            unit_identity = cast(Mapping[object, object], item).get("unit_identity")
            if isinstance(unit_identity, (str, int, float)) and not isinstance(unit_identity, bool):
                text = str(unit_identity).strip()
                if text:
                    result.append(text)
    return result


def _as_list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _list_empty(value: object) -> bool:
    return not _as_list(value)


def _ordered_unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered


def _reported_variants_cover_expected(reported_variants: set[str], expected: PhaseInventory) -> bool:
    expected_variants = set(expected.expanded_unit_identities)
    if reported_variants == expected_variants:
        return True
    summary = "\n".join(sorted(reported_variants))
    if _verbatim_count_covers_expected(summary, expected):
        return True
    if _summary_counts_cover_expected(summary, expected):
        return True
    expected_counts = _expected_variant_counts_by_base(expected.expanded_unit_identities)
    grouped_counts = _grouped_variant_counts(reported_variants)
    if grouped_counts and all(_is_grouped_variant_token(token) for token in reported_variants):
        return grouped_counts == expected_counts and sum(grouped_counts.values()) == expected.expanded_operator_instances_count
    pattern_counts = _pattern_variant_counts(reported_variants)
    return bool(pattern_counts) and pattern_counts == expected_counts and sum(pattern_counts.values()) == expected.expanded_operator_instances_count


def _source_axes_prove_variant_scope(source_inventory_report: Mapping[str, object], expected: PhaseInventory) -> bool:
    if expected.expanded_operator_instances_count <= 0:
        return False
    source_units = set(_string_list(source_inventory_report.get("fine_grained_operator_units")))
    if source_units != set(expected.fine_grained_operator_units):
        return False
    variant_axes = source_inventory_report.get("variant_axes")
    if not isinstance(variant_axes, Mapping):
        return False
    variant_axes_map = cast(Mapping[object, object], variant_axes)
    axis_text = _flatten_report_text(variant_axes_map).lower()
    required_axis_terms = ("ndim", "dtype")
    if not all(term in axis_text for term in required_axis_terms):
        return False
    expanded_summary = _flatten_report_text(source_inventory_report.get("expanded_unit_identities"))
    return (
        str(expected.expanded_operator_instances_count) in axis_text
        or _verbatim_count_covers_expected(expanded_summary, expected)
        or _source_axes_product_covers_expected(variant_axes_map, expected)
        or _summary_counts_cover_expected(expanded_summary, expected)
    )


def _inventory_count_matches_expected(inventory_report: Mapping[str, object], expected: PhaseInventory) -> bool:
    count = inventory_report.get("expanded_operator_instances_count")
    if not isinstance(count, int) or isinstance(count, bool):
        return False
    return count == expected.expanded_operator_instances_count and count == len(expected.expanded_unit_identities)


def _should_repair_phase1_verifier_report(report: Mapping[str, object], phase_output: Mapping[str, object]) -> bool:
    expected = phase1_inventory(phase_output)
    if not expected.requires_variant_coverage:
        return False
    if expected.expanded_operator_instances_count != len(expected.expanded_unit_identities):
        return False
    missing_variants = _string_list(report.get("missing_variants"))
    if any(_looks_like_concrete_variant_identity(variant) for variant in missing_variants):
        return False
    phase1_inventory_report = _mapping(report.get("phase1_inventory"))
    source_inventory_report = _mapping(report.get("source_evidence_inventory"))
    reported_count = phase1_inventory_report.get("expanded_operator_instances_count")
    count_mismatch = isinstance(reported_count, int) and not isinstance(reported_count, bool) and reported_count != expected.expanded_operator_instances_count
    phase1_variants = set(_string_list(phase1_inventory_report.get("expanded_unit_identities")))
    source_variants = set(_string_list(source_inventory_report.get("expanded_unit_identities")))
    if _has_variant_placeholder_alias(phase1_variants | source_variants):
        return True
    base_only_report = bool(phase1_variants or source_variants) and not any(
        _looks_like_concrete_variant_identity(value)
        for value in phase1_variants | source_variants
        if not _is_grouped_variant_token(value)
    )
    return count_mismatch or base_only_report


def _has_variant_placeholder_alias(values: Iterable[str]) -> bool:
    return any(_is_variant_placeholder_alias(value) for value in values)


def _is_variant_placeholder_alias(value: str) -> bool:
    normalized = re.sub(r"[\s-]+", "_", value.strip().lower())
    return normalized.startswith("same_as_")


def _looks_like_concrete_variant_identity(value: str) -> bool:
    return any(token in value for token in (":ndim=", ":accuracy=", ":dtype=", ":device="))


def _source_axes_product_covers_expected(variant_axes: object, expected: PhaseInventory) -> bool:
    if not isinstance(variant_axes, Mapping):
        return False
    total = 0
    for value in cast(Mapping[object, object], variant_axes).values():
        if isinstance(value, Mapping):
            axis_lengths = [
                len(cast(list[object], axis_values))
                for axis_values in cast(Mapping[object, object], value).values()
                if isinstance(axis_values, list) and axis_values
            ]
            if axis_lengths:
                product = 1
                for length in axis_lengths:
                    product *= length
                total += product
    return total == expected.expanded_operator_instances_count


def _summary_counts_cover_expected(summary: str, expected: PhaseInventory) -> bool:
    if not summary.strip():
        return False
    if _arithmetic_summary_counts_cover_expected(summary, expected):
        return True
    total_counts = [
        int(match.group(1))
        for match in re.finditer(r"\b(?:instances?|identit(?:y|ies)|variants?)\s*=\s*(\d+)\b", summary, flags=re.IGNORECASE)
    ]
    if any(count == expected.expanded_operator_instances_count for count in total_counts):
        return True
    leading_counts = [
        int(match.group(1))
        for match in re.finditer(
            r"\b(\d+)\s+(?:total\s+|unique\s+|concrete\s+|source-required\s+|native\s+|operator\s+|expanded\s+|verified\s+|phase\s+\d+\s+)*(?:instances?|identit(?:y|ies)|variants?)\b",
            summary,
            flags=re.IGNORECASE,
        )
    ]
    if any(count == expected.expanded_operator_instances_count for count in leading_counts):
        return True
    if leading_counts and sum(leading_counts) == expected.expanded_operator_instances_count:
        return True
    qualified_total_counts = [
        int(match.group(1))
        for match in re.finditer(
            r"\b(?:all\s+)?(\d+)\s+(?=[^\n.;,]{0,100}\b(?:instances?|identit(?:y|ies)|variants?)\b)(?=[^\n.;,]{0,100}\b(?:canonical|phase\s*1|expanded|verified|source-required)\b)",
            summary,
            flags=re.IGNORECASE,
        )
    ]
    if any(count == expected.expanded_operator_instances_count for count in qualified_total_counts):
        return True
    counts = [
        int(match.group(1))
        for match in re.finditer(r"=\s*(\d+)\s+(?:concrete\s+|source-required\s+|native\s+|operator\s+|expanded\s+)*(?:instances?|identit(?:y|ies)|variants?)\b", summary, flags=re.IGNORECASE)
    ]
    if bool(counts) and sum(counts) == expected.expanded_operator_instances_count:
        return True
    standalone_totals = [
        int(match.group(1))
        for match in re.finditer(r"=\s*(\d+)(?=$|\s|[;,])", summary)
    ]
    return bool(standalone_totals) and sum(standalone_totals) == expected.expanded_operator_instances_count


def _phase3_report_blockers_are_future_reports(report: Mapping[str, object]) -> bool:
    if not _list_empty(report.get("missing_units")) or not _list_empty(report.get("missing_variants")):
        return False
    if not _list_empty(report.get("extra_units")) or not _list_empty(report.get("extra_variants")):
        return False
    representative_only = _as_list(report.get("representative_only_coverage"))
    non_executable = _as_list(report.get("non_executable_or_missing_checks"))
    if not representative_only and not non_executable:
        return False
    return _messages_are_future_report_obligations([*representative_only, *non_executable])


def _messages_are_future_report_obligations(values: Sequence[object]) -> bool:
    if not values:
        return False
    return all(_message_is_future_report_obligation(str(value)) for value in values)


def _message_is_future_report_obligation(value: str) -> bool:
    text = value.strip().lower()
    if not text:
        return False
    report_terms = (
        "migration_reports",
        "migration_manifest",
        "runtime_coverage",
        "performance",
        "build.json",
        "implementation_resolution",
        "custom_op_final_gate",
        "evidence_validation",
        "summary.json",
        "manifest",
        "report",
        "reports",
        "same-run",
        "per-row",
    )
    future_terms = ("missing", "currently", "required", "phase 5", "future", "manifest", "report", "same-run", "per-row")
    return any(term in text for term in report_terms) and any(term in text for term in future_terms)


def _validate_phase3_entry_script_locally(
    phase_output: Mapping[str, object],
    phase1_output: Mapping[str, object],
    expected: PhaseInventory,
) -> list[str]:
    if not expected.has_custom_ops:
        return []
    project_dir = _phase3_project_dir(phase_output, phase1_output)
    resolved = _resolve_phase3_entry_script_path(phase_output, project_dir)
    if resolved is None:
        return ["assisted Phase 3 validation script is missing entry_script_path"]
    try:
        script_text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"assisted Phase 3 validation script is unreadable: {resolved}: {exc}"]
    except UnicodeError as exc:
        return [f"assisted Phase 3 validation script is unreadable: {resolved}: {exc}"]
    if resolved.suffix == ".py":
        try:
            _ = compile(script_text, str(resolved), "exec")
        except SyntaxError as exc:
            location = f" line {exc.lineno}" if exc.lineno is not None else ""
            detail = exc.msg or str(exc)
            return [f"assisted Phase 3 validation script is not valid Python:{location}: {detail}"]
    return []


def _phase3_project_dir(phase_output: Mapping[str, object], phase1_output: Mapping[str, object]) -> str | None:
    for value in (phase_output.get("project_dir"), phase1_output.get("project_dir")):
        if isinstance(value, str) and value.strip():
            return value
    return None


def _resolve_phase3_entry_script_path(phase_output: Mapping[str, object], project_dir: str | None) -> Path | None:
    entry_script = phase_output.get("entry_script_path")
    if not isinstance(entry_script, str) or not entry_script.strip():
        return None
    path = Path(entry_script)
    if not path.is_absolute() and project_dir:
        path = Path(project_dir) / path
    return path.resolve(strict=False)


def _phase3_script_has_strict_future_report_contract(
    phase_output: Mapping[str, object],
    phase1_output: Mapping[str, object],
    expected: PhaseInventory,
) -> bool:
    if not expected.has_custom_ops:
        return False
    script_text = _phase3_script_text(phase_output, phase1_output).lower()
    if not script_text:
        return False
    required_terms = [
        "migration_reports",
        "migration_manifest.json",
        "runtime_coverage.json",
        "performance.json",
        "build.json",
        "custom_op_final_gate.json",
        "unit_identity",
        "provenance",
        "opp",
        "cann",
        "install",
    ]
    fail_closed_terms = ("required report missing", "fail(", "raise systemexit", "assert")
    build_closure_terms = (
        "build row",
        "build rows",
        "build_by_id",
        "build_row_by_id",
        "build report",
        "build.json row",
    )
    unit_closure_terms = ("set(row_by_id)", "set(expected)", "row_by_id", "unit_identity equality")
    if expected.requires_variant_coverage:
        required_terms.extend(("expanded_variant_inventory", "variant_axis_coverage", "per_variant"))
        variant_terms = (
            "per-expanded-variant",
            "expanded variant",
            "set(build_by_id) == set(expected)",
            "set(build_row_by_id) == set(expected)",
            "build rows do not close",
        )
        has_variant_closure = any(term in script_text for term in variant_terms)
    else:
        has_variant_closure = True
    return (
        all(term in script_text for term in required_terms)
        and any(term in script_text for term in fail_closed_terms)
        and any(term in script_text for term in build_closure_terms)
        and any(term in script_text for term in unit_closure_terms)
        and has_variant_closure
    )

def _phase3_script_text(phase_output: Mapping[str, object], phase1_output: Mapping[str, object]) -> str:
    project_dir = _phase3_project_dir(phase_output, phase1_output)
    resolved = _resolve_phase3_entry_script_path(phase_output, project_dir)
    if resolved is not None:
        try:
            if resolved.is_file():
                return resolved.read_text(encoding="utf-8", errors="replace")[:120000]
        except OSError:
            return ""
    for key in ("entry_script_path", "entry_script"):
        value = phase_output.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        path = Path(value)
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")[:120000]
        except OSError:
            return ""
    return _flatten_report_text(phase_output)


def _arithmetic_summary_counts_cover_expected(summary: str, expected: PhaseInventory) -> bool:
    for match in re.finditer(r"=\s*((?:\d+\s*\+\s*)+\d+)\s*(?:=\s*(\d+))?", summary):
        expression = str(match.group(1))
        addend_matches = cast(list[str], re.findall(r"\d+", expression))
        addends = [int(value) for value in addend_matches]
        total = sum(addends)
        declared_total = match.group(2)
        if declared_total is not None and int(declared_total) != total:
            continue
        if total == expected.expanded_operator_instances_count:
            return True
    return False


def _flatten_report_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        parts: list[str] = []
        for key, item in cast(Mapping[object, object], value).items():
            if isinstance(key, str):
                parts.append(key)
            parts.append(_flatten_report_text(item))
        return "\n".join(parts)
    if isinstance(value, list):
        return "\n".join(_flatten_report_text(item) for item in cast(list[object], value))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _grouped_variant_counts(values: set[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        grouped = _grouped_variant_token_parts(value)
        if grouped is None:
            continue
        base, count = grouped
        counts[base] = count
    return counts


def _pattern_variant_counts(values: set[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        value_counts = _pattern_variant_counts_for_value(value)
        if value_counts is None:
            return {}
        for base, count in value_counts.items():
            counts[base] = counts.get(base, 0) + count
    return counts


def _pattern_variant_counts_for_value(value: str) -> dict[str, int] | None:
    base_pattern = _variant_base_identity(value)
    bases = _expand_brace_alternatives(base_pattern)
    if not bases:
        return None
    axis_product = _axis_assignment_product_count(value)
    if axis_product <= 0:
        return None
    return {base: axis_product for base in bases}


def _expand_brace_alternatives(value: str) -> list[str]:
    match = re.search(r"\{([^{}]+)\}", value)
    if match is None:
        return [value] if value.strip() else []
    prefix = value[: match.start()]
    suffix = value[match.end():]
    expanded: list[str] = []
    for option in match.group(1).split(","):
        option = option.strip()
        if not option:
            continue
        expanded.extend(_expand_brace_alternatives(prefix + option + suffix))
    return expanded


def _axis_assignment_product_count(value: str) -> int:
    product = 1
    saw_axis = False
    for match in re.finditer(r":([A-Za-z_][A-Za-z0-9_-]*)=([^:]+)", value):
        saw_axis = True
        product *= _axis_value_count(match.group(2))
    return product if saw_axis else 0


def _axis_value_count(value: str) -> int:
    stripped = value.strip()
    if not stripped:
        return 1
    if "/" in stripped and not stripped.startswith("{"):
        items = [item.strip() for item in stripped.split("/") if item.strip()]
        if len(items) > 1:
            return len(items)
    range_match = re.fullmatch(r"(\d+)([A-Za-z]*)\.\.(\d+)([A-Za-z]*)", stripped)
    if range_match is not None and range_match.group(2) == range_match.group(4):
        start = int(range_match.group(1))
        end = int(range_match.group(3))
        if end >= start:
            return end - start + 1
    if "," in stripped and not stripped.startswith("{"):
        items = [item.strip() for item in stripped.split(",") if item.strip()]
        if len(items) > 1:
            return len(items)
    if not stripped.startswith("{"):
        return 1
    end = stripped.find("}")
    if end <= 0:
        return 1
    items = [item.strip() for item in stripped[1:end].split(",") if item.strip()]
    return max(1, len(items))


def _expected_variant_counts_by_base(values: Sequence[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        base = _variant_base_identity(value)
        counts[base] = counts.get(base, 0) + 1
    return counts


def _variant_base_identity(value: str) -> str:
    assignment = re.search(r":[A-Za-z_][A-Za-z0-9_-]*=", value)
    if assignment is not None:
        return value[: assignment.start()]
    if ":{" in value:
        return value.split(":{", 1)[0]
    return value


def _is_grouped_variant_token(value: str) -> bool:
    return _grouped_variant_token_parts(value) is not None


def _grouped_variant_token_parts(value: str) -> tuple[str, int] | None:
    match = re.search(r":(?:\*|all)(\d+)(?=[:\s(\[\{]|$)", value, flags=re.IGNORECASE)
    if match is None:
        return None
    base = value[: match.start()]
    count = int(match.group(1))
    if count <= 0:
        return None
    return _variant_base_identity(base), count


def _verbatim_count_covers_expected(summary: str, expected: PhaseInventory) -> bool:
    normalized = summary.strip().lower()
    if "verbatim" not in normalized:
        return False
    count_match = re.search(r"count\s*=\s*(\d+)", normalized)
    return count_match is not None and int(count_match.group(1)) == expected.expanded_operator_instances_count


def _json_for_prompt(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _bullet_list(values: Sequence[str]) -> str:
    if not values:
        return "- (none)"
    return "\n".join(f"- {value}" for value in values)


def _bool_cfg(config: Mapping[object, object], key: str, default: bool) -> bool:
    value = config.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def _int_cfg(config: Mapping[object, object], key: str, default: int, *, minimum: int, maximum: int) -> int:
    value = config.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, str)):
        try:
            parsed = int(value)
        except ValueError:
            return default
        return max(minimum, min(maximum, parsed))
    return default


def _str_cfg(config: Mapping[object, object], key: str, default: str) -> str:
    value = config.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _str_cfg_any(config: Mapping[object, object], keys: Sequence[str], default: str) -> str:
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _session_error_text(report: Mapping[str, object]) -> str:
    if report.get("ok") is not False:
        return ""
    error = report.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    return "OpenCode session returned ok=false"
