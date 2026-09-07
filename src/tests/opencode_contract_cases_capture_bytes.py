from typing import final

import pytest

import harness.session.opencode_contract_json as contract_json
from harness.session.opencode_contract import Compatibility, parse_trace_contract


def test_invalid_utf8_bytes_are_preserved_exactly() -> None:
    raw = b"\xff\xfe"

    contract = parse_trace_contract(raw)

    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.to_json_value() == raw


def test_oversized_capture_is_rejected_without_retaining_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contract_json, "MAX_CAPTURE_CHARS", 8)

    contract = parse_trace_contract(b"123456789")

    assert contract.compatibility is Compatibility.INCOMPATIBLE
    assert contract.to_json_value() is None


def test_json_depth_limit_discards_parsed_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contract_json, "MAX_JSON_DEPTH", 2)

    capture = contract_json.decode_capture('[[["deep"]]]')

    assert capture is None


def test_json_node_limit_discards_parsed_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contract_json, "MAX_JSON_NODES", 3)

    capture = contract_json.decode_capture("[1, 2, 3]")

    assert capture is None


@final
class TestCaptureBytes:
    test_invalid_utf8_bytes_are_preserved_exactly = staticmethod(
        test_invalid_utf8_bytes_are_preserved_exactly
    )
    test_oversized_capture_is_rejected_without_retaining_payload = staticmethod(
        test_oversized_capture_is_rejected_without_retaining_payload
    )
    test_json_depth_limit_discards_parsed_capture = staticmethod(
        test_json_depth_limit_discards_parsed_capture
    )
    test_json_node_limit_discards_parsed_capture = staticmethod(
        test_json_node_limit_discards_parsed_capture
    )
