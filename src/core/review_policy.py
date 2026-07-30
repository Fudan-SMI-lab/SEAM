from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import ClassVar, Final, NewType, Optional, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Annotated, override

from core.run_outcome import ReviewOutcome
from core.types import TransitionDefinition, WorkflowDefinition


ReviewIterationLimit = NewType("ReviewIterationLimit", int)
ConfigEntry = TypeVar("ConfigEntry")
DEFAULT_MAX_REVIEW_ITERATIONS: Final = ReviewIterationLimit(3)
DEFAULT_REVIEW_FAIL_CLOSED: Final = True
_MODEL_CONFIG: Final = ConfigDict(
    frozen=True,
    extra="forbid",
    hide_input_in_errors=True,
)
_PositiveReviewLimit = Annotated[int, Field(strict=True, gt=0)]


class _ReviewConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    max_review_iterations: Optional[_PositiveReviewLimit] = None
    review_fail_closed: Optional[bool] = None
    fail_closed: Optional[bool] = None


def _parse_review_config(values: Mapping[str, object]) -> _ReviewConfig:
    keys = ("max_review_iterations", "review_fail_closed", "fail_closed")
    return _ReviewConfig.model_validate(
        {key: values[key] for key in keys if key in values}
    )


class _PhaseParamsConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    sub_workflow: Optional[str] = None


@dataclass(frozen=True)
class ReviewCliOverrides:
    max_iterations: ReviewIterationLimit | None
    fail_closed: bool | None


@dataclass(frozen=True)
class ReviewDefaults:
    max_iterations: ReviewIterationLimit | None
    fail_closed: bool | None


@dataclass(frozen=True)
class ReviewPolicyInputs:
    cli: ReviewCliOverrides
    workflow: ReviewDefaults
    framework: ReviewDefaults


@dataclass(frozen=True)
class ReviewPolicy:
    max_iterations: ReviewIterationLimit
    fail_closed: bool


class ReviewPolicyNamespace(Protocol):
    max_review_iter: list[int] | None
    review_fail_closed: bool | None


@dataclass(frozen=True)
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
    framework_value = config.get("framework")
    framework = framework_value if isinstance(framework_value, Mapping) else {}
    review_value = framework.get("review", config.get("review"))
    if not isinstance(review_value, Mapping):
        return ReviewDefaults(max_iterations=None, fail_closed=None)
    review = _parse_review_config(
        {key: value for key, value in review_value.items() if isinstance(key, str)}
    )
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
        projected_params: dict[str, object] = {}
        if "sub_workflow" in phase.params:
            projected_params["sub_workflow"] = phase.params["sub_workflow"]
        params = _PhaseParamsConfig.model_validate(projected_params)
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
    global_review = _parse_review_config(workflow.globals or {})
    max_iterations: ReviewIterationLimit | None = None
    names = _selected_subworkflow_names(workflow)
    if names:
        sub_workflow = workflow.sub_workflows.get(names[0])
        if sub_workflow is not None and sub_workflow.max_review_iterations is not None:
            selected_review = _parse_review_config(
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


def review_outcome_routes(
    transition: TransitionDefinition,
) -> Mapping[ReviewOutcome, str]:
    target = transition.on_reject_exhausted
    if target is None:
        return {}
    return {ReviewOutcome.REJECT_EXHAUSTED: target}
