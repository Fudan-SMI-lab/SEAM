from __future__ import annotations

from typing import Final, Protocol, final

MAX_HTTP_BODY_BYTES: Final = 64 * 1024 * 1024


class BinaryBody(Protocol):
    def read(self, n: int = -1) -> bytes: ...


@final
class HTTPBodyTooLarge(ValueError):
    pass


@final
class InvalidHTTPBodyLimit(ValueError):
    pass


def read_bounded_http_body(
    body: BinaryBody,
    max_bytes: int = MAX_HTTP_BODY_BYTES,
) -> bytes:
    if max_bytes < 1:
        raise InvalidHTTPBodyLimit("max_bytes must be positive")
    content = body.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPBodyTooLarge("HTTP response body exceeds byte limit")
    return content
