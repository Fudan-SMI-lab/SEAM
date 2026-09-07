"""Byte-accurate JSONL tail reader for the live dashboard event stream.

The dashboard polls an append-only ``ui_events.jsonl`` file written by
``core.ui_events.UIEventSink``. A record may be observed mid-write: its bytes
can end at any position, including inside a multibyte UTF-8 sequence. This
reader never advances past an incomplete trailing record, so the next poll
re-reads the tail together with any newly appended bytes and emits the record
exactly once when its terminating newline arrives.

Contract:

- Returns ``(events, next_offset)`` where ``next_offset`` is a **byte** offset
  valid across UTF-8 content.
- Reads bytes from ``offset``; if the file shrank below ``offset`` (truncation
  or recreation), resets to ``0`` and reads the new content from the start.
- Processes only the bytes through the last ``\\n``; any bytes after it form an
  incomplete tail whose **start** becomes ``next_offset`` so the next poll
  re-reads it.
- Decodes and JSON-parses each newline-terminated record once. Records that
  fail UTF-8 decoding or JSON parsing are skipped without raising and never
  stall later valid records.
- Best-effort for a not-yet-existing file: returns ``([], offset)`` unchanged
  so the caller can retry on the next poll.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_ui_events(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Read complete JSONL records from ``path`` starting at byte ``offset``.

    See module docstring for the full tail-recovery contract.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset
    if size < offset:
        offset = 0
    if size == offset:
        return [], offset
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
    except OSError:
        return [], offset

    last_newline = chunk.rfind(b"\n")
    if last_newline == -1:
        # No complete record yet: withhold the partial tail by holding offset.
        return [], offset
    complete = chunk[: last_newline + 1]
    new_offset = offset + last_newline + 1

    events: list[dict[str, Any]] = []
    for raw_line in complete.splitlines():
        if not raw_line.strip():
            continue
        try:
            decoded = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            continue
        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events, new_offset
