from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Annotated, ClassVar, Final, NewType, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import override

from core.types import WorkflowDefinition


ReviewIterationLimit = NewType("ReviewIterationLimit", int)
ConfigEntry = TypeVar("ConfigEntry")
DEFAULT_MAX_REVIEW_ITERATIONS: Final = ReviewIterationLimit(3)
DEFAULT_REVIEW_FAIL_CLOSED: Final = True
_MODEL_CONFIG: Final = ConfigDict(
    frozen=True,
    extra="ignore",
    hide_input_in_errors=True,
)
_PositiveReviewLimit = Annotated[int, Field(strict=True, gt=0)]


class _ReviewConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    max_review_iterations: _PositiveReviewLimit | None = None
    review_fail_closed: bool | None = None
    fail_closed: bool | None = None


class _FrameworkConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    review: _ReviewConfig | None = None


class _FrameworkEnvelope(BaseModel):
    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    framework: _FrameworkConfig | None = None
    review: _ReviewConfig | None = None


class _PhaseParamsConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    sub_workflow: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewCliOverrides:
    max_iterations: ReviewIterationLimit | None
    fail_closed: bool | None


@dataclass(frozen=True, slots=True)
class ReviewDefaults:
    max_iterations: ReviewIterationLimit | None
    fail_closed: bool | None


@dataclass(frozen=True, slots=True)
class ReviewPolicyInputs:
    cli: ReviewCliOverrides
    workflow: ReviewDefaults
    framework: ReviewDefaults


@dataclass(frozen=True, slots=True)
class ReviewPolicy:
    max_iterations: ReviewIterationLimit
    fail_closed: bool


class ReviewPolicyNamespace(Protocol):
    max_review_iter: list[int] | None
    review_fail_closed: bool | None


@dataclass(frozen=True, slots=True)
class ReviewPolicyConfigurationError(Exception):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


def _positive_review_limit(value: str) -> int:
    if re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise argparse.ArgumentTypeError("--max-review-iter must be a positive integer")
    return int(value)


def add_review_policy_arguments(parser: argparse.ArgumentParser) -> None:
    _ = parser.add_argument(
        "--max-review-iter",
        action="append",
        type=_positive_review_limit,
        default=None,
        help="Maximum logical review rounds; resolved from workflow/config when unset.",
    )
    review_policy_group = parser.add_mutually_exclusive_group()
    _ = review_policy_group.add_argument(
        "--review-fail-closed",
        dest="review_fail_closed",
        action="store_true",
        default=None,
    )
    _ = review_policy_group.add_argument(
        "--no-review-fail-closed",
        dest="review_fail_closed",
        action="store_false",
        default=None,
    )


def review_cli_overrides_from_namespace(
    namespace: ReviewPolicyNamespace,
    parser: argparse.ArgumentParser,
) -> ReviewCliOverrides:
    max_values = namespace.max_review_iter
    if max_values is not None and len(max_values) > 1:
        parser.error("--max-review-iter may be supplied only once")
    return ReviewCliOverrides(
        max_iterations=(
            ReviewIterationLimit(max_values[0]) if max_values is not None else None
        ),
        fail_closed=namespace.review_fail_closed,
    )


def resolve_review_policy(inputs: ReviewPolicyInputs) -> ReviewPolicy:
    max_iterations = DEFAULT_MAX_REVIEW_ITERATIONS
    for max_candidate in (
        inputs.cli.max_iterations,
        inputs.workflow.max_iterations,
        inputs.framework.max_iterations,
    ):
        if max_candidate is not None:
            max_iterations = max_candidate
            break

    fail_closed = DEFAULT_REVIEW_FAIL_CLOSED
    for fail_candidate in (
        inputs.cli.fail_closed,
        inputs.workflow.fail_closed,
        inputs.framework.fail_closed,
    ):
        if fail_candidate is not None:
            fail_closed = fail_candidate
            break

    return ReviewPolicy(
        max_iterations=max_iterations,
        fail_closed=fail_closed,
    )


def framework_review_defaults(
    config: Mapping[str, ConfigEntry],
) -> ReviewDefaults:
    envelope = _FrameworkEnvelope.model_validate(config)
    framework = envelope.framework
    review = (
        framework.review
        if framework is not None and framework.review is not None
        else envelope.review
    )
    if review is None:
        return ReviewDefaults(max_iterations=None, fail_closed=None)
    fail_closed = (
        review.review_fail_closed
        if review.review_fail_closed is not None
        else review.fail_closed
    )
    return ReviewDefaults(
        max_iterations=(
            ReviewIterationLimit(review.max_review_iterations)
            if review.max_review_iterations is not None
            else None
        ),
        fail_closed=fail_closed,
    )


def _selected_subworkflow_names(workflow: WorkflowDefinition) -> tuple[str, ...]:
    selected_names: list[str] = []
    for phase in workflow.phases:
        params = _PhaseParamsConfig.model_validate(phase.params)
        selected = params.sub_workflow or phase.sub_workflow
        if selected is not None:
            selected_names.append(selected)
    names = tuple(dict.fromkeys(selected_names))
    if len(names) > 1:
        raise ReviewPolicyConfigurationError(
            detail=f"multiple loop sub-workflows are ambiguous: {', '.join(names)}"
        )
    return names


def workflow_review_defaults(workflow: WorkflowDefinition) -> ReviewDefaults:
    global_review = _ReviewConfig.model_validate(workflow.globals or {})
    max_iterations: ReviewIterationLimit | None = None
    names = _selected_subworkflow_names(workflow)
    if names:
        sub_workflow = workflow.sub_workflows.get(names[0])
        if sub_workflow is not None and sub_workflow.max_review_iterations is not None:
            selected_review = _ReviewConfig.model_validate(
                {"max_review_iterations": sub_workflow.max_review_iterations}
            )
            if selected_review.max_review_iterations is not None:
                max_iterations = ReviewIterationLimit(
                    selected_review.max_review_iterations
                )
    fail_closed = (
        global_review.review_fail_closed
        if global_review.review_fail_closed is not None
        else global_review.fail_closed
    )
    return ReviewDefaults(
        max_iterations=max_iterations,
        fail_closed=fail_closed,
    )


def apply_review_policy(
    workflow: WorkflowDefinition,
    policy: ReviewPolicy,
) -> None:
    if workflow.globals is None:
        workflow.globals = {}
    workflow.globals["max_review_iterations"] = int(policy.max_iterations)
    workflow.globals["review_fail_closed"] = policy.fail_closed
    for name in _selected_subworkflow_names(workflow):
        sub_workflow = workflow.sub_workflows.get(name)
        if sub_workflow is not None:
            sub_workflow.max_review_iterations = int(policy.max_iterations)
