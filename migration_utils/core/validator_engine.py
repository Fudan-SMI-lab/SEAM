"""Validator engine for phase output checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypeAlias, TypedDict, cast


@dataclass
class ValidationResult:
    """Normalized result returned by the validator engine."""

    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ValidationDict(TypedDict):
    """Dictionary-shaped validator result used by phase validators."""

    passed: bool
    errors: list[str]
    warnings: list[str]


ValidatorResultValue: TypeAlias = ValidationResult | ValidationDict | bool | list[str] | tuple[str, ...]


class ValidatorFn(Protocol):
    """Callable signature for registered validators."""

    def __call__(self, data: dict[str, object]) -> ValidatorResultValue:
        ...


class ValidatorEngine:
    """Registry-backed adapter for validator functions."""

    def __init__(self) -> None:
        self._validators: dict[str, ValidatorFn] = {}

    def register_validator(self, name: str, validator_fn: object) -> None:
        """Register a validator under a stable phase name."""
        if not name:
            raise ValueError("Validator name must be a non-empty string")
        if not callable(validator_fn):
            raise TypeError("validator_fn must be callable")
        self._validators[name] = cast(ValidatorFn, validator_fn)

    def validate(self, name: str, data: dict[str, object]) -> ValidationResult:
        """Run a registered validator and normalize its result."""
        validator_fn = self._validators.get(name)
        if validator_fn is None:
            return ValidationResult(
                passed=False,
                errors=[f"Validator '{name}' is not registered."],
                warnings=[],
            )

        try:
            result = validator_fn(data)
        except Exception as exc:  # pragma: no cover - defensive normalization
            return ValidationResult(
                passed=False,
                errors=[f"Validator '{name}' raised {exc.__class__.__name__}: {exc}"],
                warnings=[],
            )

        return self._normalize_result(name, result)

    def _normalize_result(self, name: str, result: object) -> ValidationResult:
        if isinstance(result, ValidationResult):
            errors = _normalize_messages(result.errors)
            warnings = _normalize_messages(result.warnings)
            return ValidationResult(passed=bool(result.passed) and not errors, errors=errors, warnings=warnings)

        if isinstance(result, bool):
            errors = [] if result else [f"Validator '{name}' reported failure."]
            return ValidationResult(passed=result, errors=errors, warnings=[])

        if isinstance(result, dict):
            validation_dict_obj = cast(object, result)
            validation_dict = cast(ValidationDict, validation_dict_obj)
            errors = _normalize_messages(validation_dict.get("errors"))
            warnings = _normalize_messages(validation_dict.get("warnings"))
            passed = bool(validation_dict.get("passed", not errors)) and not errors
            return ValidationResult(passed=passed, errors=errors, warnings=warnings)

        if isinstance(result, (list, tuple)):
            errors = _normalize_messages(cast(object, result))
            return ValidationResult(passed=not errors, errors=errors, warnings=[])

        return ValidationResult(
            passed=False,
            errors=[f"Validator '{name}' returned unsupported type: {type(result).__name__}"],
            warnings=[],
        )


def _normalize_messages(messages: object) -> list[str]:
    if messages is None:
        return []
    if isinstance(messages, str):
        return [messages]
    if isinstance(messages, (list, tuple, set)):
        iterable_messages = cast(list[object] | tuple[object, ...] | set[object], messages)
        return [str(message) for message in iterable_messages]
    return [str(messages)]
