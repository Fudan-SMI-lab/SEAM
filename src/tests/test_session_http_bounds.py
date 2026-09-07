from __future__ import annotations

from email.message import Message
from io import BytesIO
import urllib.error
import urllib.request

import pytest

from harness.session.manager import MigrationSessionManager
from harness.session.http_body import HTTPBodyTooLarge, read_bounded_http_body


class _BoundedResponse(BytesIO):
    status: int = 200

    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.headers = Message()

    def read(self, n: int | None = -1) -> bytes:
        if n is None or n < 0:
            raise AssertionError("HTTP response read must be bounded")
        return super().read(n)


def test_http_success_body_is_bounded_before_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _BoundedResponse(b"secret-response")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: response)
    manager = MigrationSessionManager(auto_detect_agent=False)

    result = manager._http("GET", "/session")

    assert result["ok"] is True
    assert result["data"] == "secret-response"


def test_http_error_body_is_bounded_before_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _BoundedResponse(b"secret-error")

    def fail(*_args: object, **_kwargs: object) -> _BoundedResponse:
        raise urllib.error.HTTPError(
            "http://127.0.0.1:4096/session",
            500,
            "failure",
            Message(),
            body,
        )

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    manager = MigrationSessionManager(auto_detect_agent=False)

    result = manager._http("GET", "/session")

    assert result["ok"] is False
    assert result["details"] == "secret-error"


def test_bounded_http_body_rejects_limit_plus_one() -> None:
    body = _BoundedResponse(b"12345")

    with pytest.raises(HTTPBodyTooLarge):
        _ = read_bounded_http_body(body, max_bytes=4)
