"""The real Board transport bounds success and HTTP-error bodies identically."""

import io
import urllib.error

from solver import board


class ErrorOpener:
    def open(self, request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            413,
            "too large",
            {"Content-Type": "application/json", "Location": ""},
            io.BytesIO(b"abcdef"),
        )


def test_http_error_body_read_is_bounded_to_cap_plus_one(monkeypatch):
    monkeypatch.setattr(board.urllib.request, "build_opener", lambda *_handlers: ErrorOpener())
    fetch = board.network_transport(4)

    status, body, _location, content_type = fetch(board.urllib.request.Request("https://board.example/fail"))

    assert status == 413
    assert body == b"abcde"
    assert content_type == "application/json"
