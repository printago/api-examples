"""A minimal Printago REST client.

Every example ships its own copy of this file so each folder stands alone.
It covers what the examples need and nothing more: API-key auth, readable
errors, paging, and backing off on HTTP 429.

API reference: https://developers.printago.io
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Iterator

import requests

DEFAULT_BASE_URL = "https://api.printago.io"


class PrintagoError(Exception):
    """Raised for any non-2xx response. `body` holds the parsed error payload."""

    def __init__(self, status: int, method: str, path: str, body: Any):
        self.status = status
        self.method = method
        self.path = path
        self.body = body
        super().__init__(f"{method} {path} failed with {status}: {_describe(body)}")


class Printago:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, max_retries: int = 5):
        if not api_key:
            raise ValueError("A Printago API key is required")
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers["authorization"] = f"ApiKey {api_key}"

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: Any = None, **params: Any) -> Any:
        return self.request("POST", path, json=body, params=params)

    def patch(self, path: str, body: Any = None) -> Any:
        return self.request("PATCH", path, json=body)

    def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
        params = {k: _query_value(v) for k, v in (params or {}).items() if v is not None}
        for attempt in range(self.max_retries + 1):
            response = self.session.request(method, self.base_url + path, params=params, json=json, timeout=60)
            # The API key limit is shared by every client on the store, and a 429
            # does not say how long to wait: back off exponentially (2s, 4s, 8s...).
            if response.status_code == 429 and attempt < self.max_retries:
                time.sleep(2 * 2**attempt)
                continue
            body = _parse(response)
            if not response.ok:
                raise PrintagoError(response.status_code, method, path, body)
            return body
        raise AssertionError("unreachable")

    def paginate(self, path: str, page_size: int = 500, **params: Any) -> Iterator[dict]:
        """Yield every row of a list endpoint, fetching `page_size` at a time."""
        offset = 0
        while True:
            page = self.get(path, limit=page_size, offset=offset, meta=True, **params)
            yield from page["data"]
            if not page["meta"]["hasMore"]:
                return
            offset += page_size


def printago_from_env() -> Printago:
    """Build a client from PRINTAGO_API_KEY / PRINTAGO_BASE_URL (and a .env file, if present)."""
    load_dotenv()
    return Printago(require_env("PRINTAGO_API_KEY"), os.environ.get("PRINTAGO_BASE_URL") or DEFAULT_BASE_URL)


def require_env(name: str) -> str:
    load_dotenv()
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing {name}. Copy .env.example to .env and fill it in.")
    return value


_dotenv_loaded = False


def load_dotenv(path: str | None = None) -> None:
    """Read KEY=value lines from .env into the environment. Real environment variables win.

    Looks in the current directory, then next to the script being run.
    """
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    candidates = [path] if path else [".env", os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), ".env")]
    for candidate in dict.fromkeys(os.path.abspath(c) for c in candidates):
        if os.path.exists(candidate):
            _read_env_file(candidate)


def _read_env_file(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _query_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(v) for v in value)
    return value


def _parse(response: requests.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


def _describe(body: Any) -> str:
    if isinstance(body, dict) and "message" in body:
        message = body["message"]
        return "; ".join(map(str, message)) if isinstance(message, list) else str(message)
    return str(body)[:300]
