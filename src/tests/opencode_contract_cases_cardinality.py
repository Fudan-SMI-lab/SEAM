from typing import final

import pytest

import harness.session.opencode_contract_messages as contract_messages
from harness.session.opencode_contract import Completeness
from harness.session.opencode_contract import JsonObject, JsonValue


def _response(body: list[JsonValue]) -> JsonObject:
    return {"status": 200, "body": body, "query": {}, "headers": {}}


def test_message_limit_fails_before_message_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contract_messages, "MAX_MESSAGE_COUNT", 1)

    result, invalid = contract_messages.parse_messages(_response([{}, {}]))

    assert invalid is True
    assert result.completeness is Completeness.INCOMPATIBLE


def test_part_limit_fails_before_part_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contract_messages, "MAX_PART_COUNT", 1)
    body: list[JsonValue] = [
        {
            "info": {
                "id": "msg-1",
                "sessionID": "ses-1",
                "role": "user",
                "time": {"created": 1},
            },
            "parts": [{}, {}],
        }
    ]

    result, invalid = contract_messages.parse_messages(_response(body))

    assert invalid is True
    assert result.completeness is Completeness.INCOMPATIBLE


@final
class TestContractCardinality:
    test_message_limit_fails_before_message_parsing = staticmethod(
        test_message_limit_fails_before_message_parsing
    )
    test_part_limit_fails_before_part_parsing = staticmethod(
        test_part_limit_fails_before_part_parsing
    )
