"""Byte-accurate JSONL tail recovery for the dashboard event reader.

These tests pin the contract that incomplete trailing records are withheld
until their terminating newline arrives, malformed terminated records are
skipped exactly once without stalling later valid records, and offsets are
byte offsets valid across UTF-8 content. They exercise the same
``_load_events`` seam used by the Rich and Textual polling loops.
"""

from __future__ import annotations

from pathlib import Path

from core.dashboard import _load_events


def _append(path: Path, payload: bytes) -> None:
    with path.open("ab") as handle:
        handle.write(payload)


def test_baseline_complete_lines_preserve_order_and_byte_offset(
    tmp_path: Path,
) -> None:
    """Given three complete JSONL records; When read once; Then all three appear
    in file order, the offset equals the file size, and a follow-up poll with no
    new data is a no-op."""
    path = tmp_path / "events.jsonl"
    _append(path, b'{"id": 1}\n{"id": 2}\n{"id": 3}\n')

    events, offset = _load_events(path, 0)

    assert [e["id"] for e in events] == [1, 2, 3]
    assert offset == path.stat().st_size

    again, offset_again = _load_events(path, offset)
    assert again == []
    assert offset_again == offset


def test_partial_ascii_tail_is_withheld_then_emitted_once(
    tmp_path: Path,
) -> None:
    """Given a complete record plus a partial ASCII tail with no newline; When
    polled; Then only the complete record is returned and the tail is withheld;
    after the remainder plus newline arrives exactly one new event appears and
    no later poll repeats it."""
    path = tmp_path / "events.jsonl"
    _append(path, b'{"id": 1}\n{"id": 2, "msg": "incomp')

    first, offset = _load_events(path, 0)
    assert [e["id"] for e in first] == [1]

    _append(path, b'lete"}\n')
    second, offset = _load_events(path, offset)
    assert [e["id"] for e in second] == [2]
    assert second[0]["msg"] == "incomplete"

    third, _ = _load_events(path, offset)
    assert third == []


def test_partial_cjk_utf8_line_completed_across_two_polls(
    tmp_path: Path,
) -> None:
    """Given a CJK record whose UTF-8 bytes are split mid-character across two
    polls; When the second half plus newline arrives; Then the full record is
    decoded exactly once with no UnicodeDecodeError and no duplicate."""
    path = tmp_path / "events.jsonl"
    full = '{"id": 1, "msg": "迁移阶段开始"}\n'.encode("utf-8")
    # Split one byte into the multibyte sequence of the first CJK char '迁'.
    split_at = full.index(b"\xe8") + 1
    _append(path, full[:split_at])

    first, offset = _load_events(path, 0)
    assert first == []

    _append(path, full[split_at:])
    second, _ = _load_events(path, offset)
    assert len(second) == 1
    assert second[0]["id"] == 1
    assert second[0]["msg"] == "迁移阶段开始"


def test_byte_offset_is_byte_accurate_across_utf8_content(
    tmp_path: Path,
) -> None:
    """Given CJK content; When read; Then the returned offset equals the byte
    length of the consumed prefix, not the character count."""
    path = tmp_path / "events.jsonl"
    payload = '{"msg": "阶段"}\n'.encode("utf-8")
    _append(path, payload)

    _, offset = _load_events(path, 0)

    assert offset == len(payload)


def test_no_newline_tail_is_withheld_indefinitely(tmp_path: Path) -> None:
    """Given a tail with no newline; When polled repeatedly without new data;
    Then the same bytes are withheld on every poll (offset never advances past
    the tail and no event is emitted)."""
    path = tmp_path / "events.jsonl"
    _append(path, b'{"id": 1}\n{"id": 2, "msg": "pending')

    first, offset = _load_events(path, 0)
    assert [e["id"] for e in first] == [1]

    second, offset2 = _load_events(path, offset)
    assert second == []
    assert offset2 == offset


def test_malformed_newline_terminated_json_skipped_once(
    tmp_path: Path,
) -> None:
    """Given a malformed newline-terminated JSON record followed by a valid
    record; When polled; Then the malformed record is skipped exactly once with
    no exception and the later valid record remains visible; no later poll
    replays either record."""
    path = tmp_path / "events.jsonl"
    _append(path, b'{"id": 1}\nnot-json-at-all\n{"id": 2}\n')

    events, offset = _load_events(path, 0)
    assert [e["id"] for e in events] == [1, 2]

    again, _ = _load_events(path, offset)
    assert again == []


def test_invalid_utf8_terminated_line_skipped_once(tmp_path: Path) -> None:
    """Given an invalid-UTF-8 newline-terminated line between two valid records;
    When polled; Then the invalid line is skipped exactly once and both valid
    records remain visible in order."""
    path = tmp_path / "events.jsonl"
    _append(path, b'{"id": 1}\n\xff\xfe\xfd\n{"id": 2}\n')

    events, _ = _load_events(path, 0)
    assert [e["id"] for e in events] == [1, 2]


def test_truncation_below_offset_resets_to_zero(tmp_path: Path) -> None:
    """Given the file is truncated below the current offset between polls; When
    polled again; Then the offset resets to zero and the new shorter content is
    read from the start."""
    path = tmp_path / "events.jsonl"
    _append(path, b'{"id": 1}\n{"id": 2}\n{"id": 3}\n')
    _, offset = _load_events(path, 0)
    assert offset == path.stat().st_size

    path.write_bytes(b'{"id": 99}\n')

    events, new_offset = _load_events(path, offset)
    assert [e["id"] for e in events] == [99]
    assert new_offset == path.stat().st_size


def test_nonexistent_file_returns_empty_and_unchanged_offset(
    tmp_path: Path,
) -> None:
    """Given the events file does not yet exist; When polled; Then no events are
    returned and the offset is returned unchanged so the next poll retries."""
    path = tmp_path / "does_not_exist.jsonl"

    events, offset = _load_events(path, 123)

    assert events == []
    assert offset == 123


def test_blank_and_whitespace_lines_are_skipped(tmp_path: Path) -> None:
    """Given blank and whitespace-only lines between valid records; When polled;
    Then the blank lines are skipped and only valid records appear in order."""
    path = tmp_path / "events.jsonl"
    _append(path, b'{"id": 1}\n\n  \n{"id": 2}\n\n')

    events, _ = _load_events(path, 0)
    assert [e["id"] for e in events] == [1, 2]


def test_exactly_once_delivery_across_three_polls_with_partial_tail(
    tmp_path: Path,
) -> None:
    """Given three records appended across three polls with one partial tail in
    the middle; When each poll reads from the prior offset; Then every record is
    delivered exactly once with no duplicates and no losses."""
    path = tmp_path / "events.jsonl"
    seen: list[int] = []

    _append(path, b'{"id": 1}\n')
    e1, off1 = _load_events(path, 0)
    seen.extend(e["id"] for e in e1)

    _append(path, b'{"id": 2}\n{"id": 3, "msg": "incomp')
    e2, off2 = _load_events(path, off1)
    seen.extend(e["id"] for e in e2)

    _append(path, b'lete"}\n{"id": 4}\n')
    e3, _ = _load_events(path, off2)
    seen.extend(e["id"] for e in e3)

    assert seen == [1, 2, 3, 4]


def test_non_dict_json_records_are_ignored(tmp_path: Path) -> None:
    """Given newline-terminated JSON values that are not objects (list, number,
    string); When polled; Then they are skipped without raising and only dict
    records appear, matching the existing dashboard contract."""
    path = tmp_path / "events.jsonl"
    _append(path, b'{"id": 1}\n[1, 2, 3]\n42\n"text"\n{"id": 2}\n')

    events, _ = _load_events(path, 0)
    assert [e["id"] for e in events] == [1, 2]
