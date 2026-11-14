"""A pricing error never prints a credential.

GCP sends its API key as ``?key=...``, and a failed request quoted the whole
URL in the error the CLI prints to stderr. A 403 (Cloud Billing API not
enabled) is the usual first-run error, so the key leaked exactly when a new
user was most likely to paste the message somewhere. No network here: the
HTTP layer is replaced, the key is fake and the cache is a temp file.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from sim_core.price_source.base import PriceLookupError, redact_url
from sim_core.price_source.gcp import GcpCatalog

FAKE_KEY = "FAKE-SECRET-KEY-123"


def _catalog(tmp_path: Path) -> GcpCatalog:
    return GcpCatalog(cache_path=tmp_path / "prices.json", api_key=FAKE_KEY)


def test_http_error_does_not_print_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(request: urllib.request.Request, timeout: float | None = None) -> Any:
        assert FAKE_KEY in request.full_url  # the request itself still carries it
        raise urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", {},  # type: ignore[arg-type]
            io.BytesIO(b'{"error": "billing api disabled"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    with pytest.raises(PriceLookupError) as raised:
        _catalog(tmp_path)._get("/v1/services", pageSize="1")
    message = str(raised.value)
    assert FAKE_KEY not in message
    assert "HTTP 403" in message and "key=***" in message
    assert "pageSize=1" in message  # the rest of the URL stays readable


def test_bad_json_does_not_print_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Html:
        def __enter__(self) -> _Html:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b"<html>proxy login</html>"

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: _Html())
    with pytest.raises(PriceLookupError) as raised:
        _catalog(tmp_path)._get("/v1/services")
    assert FAKE_KEY not in str(raised.value)
    assert "key=***" in str(raised.value)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x.test/a?key=abc&pageSize=1", "https://x.test/a?key=***&pageSize=1"),
        ("https://x.test/a?pageSize=1&KEY=abc", "https://x.test/a?pageSize=1&KEY=***"),
        ("https://x.test/a?access_token=abc#frag", "https://x.test/a?access_token=***#frag"),
        ("https://x.test/a?monkey=abc", "https://x.test/a?monkey=abc"),
        ("https://x.test/a", "https://x.test/a"),
    ],
)
def test_redact_url(url: str, expected: str) -> None:
    assert redact_url(url) == expected
