from __future__ import annotations

import json
from typing import final

import pytest

from harness.session.opencode_contract import (
    Completeness,
    Compatibility,
    parse_trace_contract,
)
from tests.opencode_contract_test_helpers import fixture, snapshot_trace


def test_malformed_with_parts_marks_contract_incompatible_without_data_loss() -> None:
    # Given
    raw = fixture("trace_malformed_with_parts.json")

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.messages.completeness is Completeness.INCOMPATIBLE
    assert contract.completeness is Completeness.INCOMPATIBLE
    assert contract.messages.messages == ()
    assert contract.to_json_value() == json.loads(raw)


def test_boolean_message_limit_is_incompatible() -> None:
    # Given
    raw = fixture("trace_complete.json").replace(
        '"query": {},', '"query": {"limit": false},', 1
    )

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.messages.completeness is Completeness.INCOMPATIBLE


@pytest.mark.parametrize(
    "raw",
    [
        "[" * 10000 + "]" * 10000,
        '{"health":{"status":200,"body":{"healthy":NaN,"version":"1.18.5"}}}',
        '{"health":{"status":200,"body":{"healthy":true,"version":"1.18.5","future":1e400}}}',
        '{"health":{},"health":{}}',
    ],
)
def test_invalid_json_forms_are_explicitly_incompatible(raw: str) -> None:
    # Given / When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.completeness is Completeness.INCOMPATIBLE
    assert contract.to_json_value() == raw


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(
            snapshot_trace().replace('"query": {}', '"query": []', 1),
            id="query-array",
        ),
        pytest.param(
            snapshot_trace().replace('"query": {}', '"query": {"limit": null}', 1),
            id="query-limit-null",
        ),
        pytest.param(
            snapshot_trace().replace('"headers": {}', '"headers": []', 1),
            id="headers-array",
        ),
        pytest.param(
            snapshot_trace().replace('"status": 200', '"status": 200.0', 1),
            id="float-status",
        ),
        pytest.param(
            snapshot_trace().replace('"cost": 0', '"cost": ' + "9" * 1000, 1),
            id="huge-cost",
        ),
        pytest.param(
            fixture("trace_complete.json").replace(
                '"role": "user",', '"role": "user", "format": [],', 1
            ),
            id="user-format",
        ),
        pytest.param(
            fixture("trace_complete.json").replace(
                '"mode": "build",', '"mode": "build", "variant": 7,', 1
            ),
            id="assistant-variant",
        ),
        pytest.param(
            fixture("trace_complete.json").replace(
                '"synthetic": false,', '"synthetic": {},', 1
            ),
            id="text-synthetic",
        ),
        pytest.param(
            fixture("trace_complete.json").replace(
                '"metadata": {"taskKind": "explore",',
                '"summary": [], "metadata": {"taskKind": "explore",',
                1,
            ),
            id="child-summary",
        ),
    ],
)
def test_malformed_envelopes_numbers_and_optional_fields_are_incompatible(
    raw: str,
) -> None:
    # Given / When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.completeness is Completeness.INCOMPATIBLE


def test_oversized_integer_decode_is_explicitly_incompatible() -> None:
    # Given
    raw = '{"value":' + "9" * 5000 + "}"

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.to_json_value() == raw


def test_bounded_integer_decode_rejects_large_unknown_values() -> None:
    # Given
    raw = snapshot_trace()[:-1] + ',"future":' + "9" * 2000 + "}"

    # When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.to_json_value() == raw


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(
            snapshot_trace().replace(
                '"mode": "build",', '"mode": "build", "error": {},', 1
            ),
            id="assistant-error",
        ),
        pytest.param(
            fixture("trace_complete.json").replace(
                '"agent": "build",\n          "model":',
                '"agent": "build",\n          "summary": {"diffs": [7]},\n'
                + '          "model":',
                1,
            ),
            id="user-summary-diff",
        ),
        pytest.param(
            fixture("trace_complete.json").replace(
                '"version": "1.18.5",\n        "metadata":',
                '"version": "1.18.5",\n'
                + '        "summary": {"additions": 1, "deletions": 1, '
                + '"files": 1, "diffs": [7]},\n        "metadata":',
                1,
            ),
            id="child-summary-diff",
        ),
        pytest.param(
            fixture("trace_complete.json").replace(
                '"version": "1.18.5",\n        "metadata":',
                '"version": "1.18.5",\n        "permission": [7],\n'
                + '        "metadata":',
                1,
            ),
            id="child-permission",
        ),
    ],
)
def test_malformed_nested_schema_entries_are_incompatible(raw: str) -> None:
    # Given / When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.completeness is Completeness.INCOMPATIBLE


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(
            snapshot_trace().replace('"role": "assistant"', '"role": []', 1),
            id="message-role",
        ),
        pytest.param(
            snapshot_trace().replace(
                '"mode": "build",',
                '"mode": "build", "error": {"name": [], "data": {}},',
                1,
            ),
            id="assistant-error-name",
        ),
        pytest.param(
            fixture("trace_complete.json").replace(
                '"agent": "build",\n          "model":',
                '"agent": "build",\n          "summary": {"diffs": '
                + '[{"additions": 0, "deletions": 0, "status": []}]},\n'
                + '          "model":',
                1,
            ),
            id="diff-status",
        ),
        pytest.param(
            fixture("trace_complete.json").replace(
                '"version": "1.18.5",\n        "metadata":',
                '"version": "1.18.5",\n        "permission": '
                + '[{"permission": "read", "pattern": "*", "action": []}],\n'
                + '        "metadata":',
                1,
            ),
            id="permission-action",
        ),
        pytest.param(
            fixture("trace_complete.json").replace(
                '"time": {"created": 1000}',
                '"time": {"created": 1000, "completed": null}',
                1,
            ),
            id="user-completed-null",
        ),
    ],
)
def test_unhashable_discriminators_and_user_completed_null_are_incompatible(
    raw: str,
) -> None:
    # Given / When
    contract = parse_trace_contract(raw)

    # Then
    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.completeness is Completeness.INCOMPATIBLE


@final
class TestMalformedEnvelopes:
    test_malformed_with_parts_marks_contract_incompatible_without_data_loss = (
        staticmethod(
            test_malformed_with_parts_marks_contract_incompatible_without_data_loss
        )
    )
    test_boolean_message_limit_is_incompatible = staticmethod(
        test_boolean_message_limit_is_incompatible
    )
    test_invalid_json_forms_are_explicitly_incompatible = staticmethod(
        test_invalid_json_forms_are_explicitly_incompatible
    )
    test_malformed_envelopes_numbers_and_optional_fields_are_incompatible = (
        staticmethod(
            test_malformed_envelopes_numbers_and_optional_fields_are_incompatible
        )
    )
    test_oversized_integer_decode_is_explicitly_incompatible = staticmethod(
        test_oversized_integer_decode_is_explicitly_incompatible
    )
    test_bounded_integer_decode_rejects_large_unknown_values = staticmethod(
        test_bounded_integer_decode_rejects_large_unknown_values
    )
    test_malformed_nested_schema_entries_are_incompatible = staticmethod(
        test_malformed_nested_schema_entries_are_incompatible
    )
    test_unhashable_discriminators_and_user_completed_null_are_incompatible = (
        staticmethod(
            test_unhashable_discriminators_and_user_completed_null_are_incompatible
        )
    )
