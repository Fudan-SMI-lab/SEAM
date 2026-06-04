"""Generic serving runtime validator with zero platform hardcoding.

This module validates vLLM/SGLang serving final-gate reports using fully
platform-neutral checks — no hardcoded platform names (ascend/npu/ppu/musa/etc.)
appear anywhere in this file.

Design principles
------------------
* Zero platform strings in code — all platform knowledge lives in prompts.
* Standardized evidence field names — ``accelerator_execution_evidence``,
  ``accelerator_execution_observed``, ``serving_runtime_evidence`` for every
  platform.
* Generic required-check and validation-obligation defaults — per-backend
  overrides are not needed; platform-specific validation is handled through
  LLM prompts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from core.validator_engine import ValidationDict

_SERVING_ROUTES = {"vllm_serving", "sglang_serving"}
_ROUTE_TO_FRAMEWORK = {"vllm_serving": "vllm", "sglang_serving": "sglang"}

# ---------------------------------------------------------------------------
# Evidence field names — same for every platform
# ---------------------------------------------------------------------------

EXECUTION_EVIDENCE_FIELD = "accelerator_execution_evidence"
EXECUTION_OBSERVED_FIELD = "accelerator_execution_observed"
RUNTIME_EVIDENCE_FIELD = "serving_runtime_evidence"

# ---------------------------------------------------------------------------
# Required checks — generic defaults (no per-backend overrides)
# ---------------------------------------------------------------------------

GENERIC_SERVING_REQUIRED_CHECKS: tuple[str, ...] = (
    "project_demo_or_test_execution",
    "serving_api_request_validation",
    "readiness_probe_passed",
    "accelerator_execution_evidence",
    "no_forbidden_runtime_fallback",
    "no_cpu_fallback",
    "fresh_serving_report",
    "route_framework_match",
)

# ---------------------------------------------------------------------------
# Validation obligations — generic defaults (no per-backend overrides)
# ---------------------------------------------------------------------------

GENERIC_SERVING_VALIDATION_OBLIGATIONS: tuple[str, ...] = (
    "actual_project_demo_test_or_api_validation",
    "accelerator_execution_evidence",
    "reject_import_only_or_smoke_only",
    "reject_forbidden_runtime_or_cpu_fallback",
    "fresh_report_paths",
    "route_framework_match",
)

# ---------------------------------------------------------------------------
# Gateway validation — fully generic
# ---------------------------------------------------------------------------

PASS_STATES = {"PASS", "FULL_PASS", "DONE", "CLOSED_PASS"}
BLOCKING_STATUSES = frozenset({
    "MVP_ONLY",
    "SMOKE_ONLY",
    "PARTIAL",
    "DIRECT_ONLY",
    "ARTIFACT_ONLY",
    "INCOMPLETE",
    "FAILED",
    "BLOCKED",
    "HARDWARE_LIMITATION_ACCEPTED",
    "TODO",
    "FOLLOW_UP",
    "FUTURE_WORK",
})


def validate_serving_final_gate(
    data: dict[str, object],
    expected_route: str | None = None,
) -> ValidationDict:
    """Validate a vLLM/SGLang serving final-gate report (zero platform hardcoding).

    Every check is expressed in platform-neutral terms.  Platform-specific
    evidence field names, required checks, and validation obligations are
    replaced by the standardized generic versions defined in this module.
    """
    errors: list[str] = []

    # --- route / framework identity ---
    route = data.get("migration_route")
    if expected_route is not None and route != expected_route:
        errors.append(
            f"migration_route must match expected serving route {expected_route}"
        )
    if not isinstance(route, str) or route not in _SERVING_ROUTES:
        errors.append("migration_route must be vllm_serving or sglang_serving")

    expected_framework = _ROUTE_TO_FRAMEWORK.get(str(route))
    if data.get("serving_framework") != expected_framework:
        errors.append(
            f"serving_framework must be '{expected_framework}' for migration_route={route}"
        )

    # --- status ---
    full_status = data.get("full_migration_status")
    _reject_blocking_status(full_status, "full_migration_status", errors)
    if full_status != "FULL_PASS":
        errors.append("full_migration_status must be 'FULL_PASS'")

    # --- required evidence lists ---
    for field in ("project_test_files", "expected_outputs", "required_checks"):
        if not _non_empty_string_list(data.get(field)):
            errors.append(
                f"{field} must be a non-empty list of project validation evidence"
            )

    # --- required checks coverage (generic names only) ---
    required_checks = set(_string_values(data.get("required_checks")))
    for check in GENERIC_SERVING_REQUIRED_CHECKS:
        if check not in required_checks:
            errors.append(f"required_checks must include {check}")

    # --- probe / validation evidence ---
    if not _truthy_evidence(data.get("readiness_probe")):
        errors.append("readiness_probe must prove the serving endpoint became ready")
    if not _truthy_evidence(data.get("request_validation")):
        errors.append("request_validation must prove actual project API/demo requests succeeded")
    if not _truthy_evidence(data.get(EXECUTION_EVIDENCE_FIELD)):
        errors.append(f"{EXECUTION_EVIDENCE_FIELD} must prove real accelerator execution")

    # --- boolean gate flags ---
    if data.get("project_demo_or_test_executed") is not True:
        errors.append("project_demo_or_test_executed must be true")
    if data.get("serving_api_validated") is not True:
        errors.append("serving_api_validated must be true")
    if data.get(EXECUTION_OBSERVED_FIELD) is not True:
        errors.append(f"{EXECUTION_OBSERVED_FIELD} must be true")

    # --- runtime evidence ---
    _validate_generic_serving_runtime_evidence(data, errors)

    # --- fallback / synthetic flags ---
    for field in ("cuda_fallback_detected", "cpu_fallback_detected", "import_only", "smoke_only"):
        if data.get(field) is not False:
            errors.append(f"{field} must be false")

    return {"passed": not errors, "errors": errors, "warnings": []}


def _validate_generic_serving_runtime_evidence(
    data: dict[str, object],
    errors: list[str],
) -> None:
    """Validate the standardized ``serving_runtime_evidence`` block."""
    evidence_value = data.get(RUNTIME_EVIDENCE_FIELD)
    if not isinstance(evidence_value, Mapping):
        errors.append(
            f"{RUNTIME_EVIDENCE_FIELD} must be present for serving FULL_PASS validation"
        )
        return
    evidence = cast(Mapping[object, object], evidence_value)
    if evidence.get("forbidden_runtime_markers_absent") is not True:
        errors.append(
            f"{RUNTIME_EVIDENCE_FIELD}.forbidden_runtime_markers_absent must be true"
        )
    framework = data.get("serving_framework")
    if isinstance(framework, str) and evidence.get(f"{framework}_imported") is not True:
        errors.append(
            f"{RUNTIME_EVIDENCE_FIELD}.{framework}_imported must be true"
        )


# ---------------------------------------------------------------------------
# Helpers — shared across validators
# ---------------------------------------------------------------------------


def _reject_blocking_status(value: object, label: str, errors: list[str]) -> None:
    status = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if status in BLOCKING_STATUSES:
        errors.append(f"{label} must not be {status}")


def _non_empty_string_list(value: object) -> bool:
    return bool(_string_values(value))


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item.strip()
        for item in cast(list[object], value)
        if isinstance(item, str) and item.strip()
    ]


def _truthy_evidence(value: object) -> bool:
    if isinstance(value, Mapping):
        evidence = cast(Mapping[object, object], value)
        if evidence.get("passed") is False or evidence.get("success") is False:
            return False
        return any(
            item not in (None, False, "", [], {}) for item in evidence.values()
        )
    if isinstance(value, list):
        evidence_items = cast(list[object], value)
        return bool(evidence_items) and all(
            _truthy_evidence(item) for item in evidence_items
        )
    if isinstance(value, str):
        return bool(value.strip())
    return value is True
