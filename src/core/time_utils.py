"""Canonical UTC timestamps for SEAM-owned human and machine logs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone


def utc_now_iso(*, timespec: str = "milliseconds") -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec=timespec)
        .replace("+00:00", "Z")
    )


class UTCFormatter(logging.Formatter):
    """Logging formatter whose ``asctime`` is explicit ISO-8601 UTC."""

    def formatTime(  # noqa: N802 - logging.Formatter API
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        instant = datetime.fromtimestamp(record.created, timezone.utc)
        if datefmt:
            return instant.strftime(datefmt)
        return instant.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def configure_utc_logging(*, verbose: bool) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        UTCFormatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=[handler],
        force=True,
    )
