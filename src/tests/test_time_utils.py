from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.time_utils import UTCFormatter, utc_now_iso


def test_utc_now_iso_is_explicit_utc() -> None:
    timestamp = utc_now_iso()

    assert timestamp.endswith("Z")
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    assert parsed.tzinfo == timezone.utc


def test_utc_formatter_ignores_local_timezone() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "message", (), None)
    record.created = 0

    rendered = UTCFormatter("%(asctime)s %(message)s").format(record)

    assert rendered == "1970-01-01T00:00:00.000Z message"
