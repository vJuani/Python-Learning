"""HTTP client for RedREMAX listings. Transport is injectable. Tokens never logged."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from modules.property_sync.redremax.auth import default_auth_provider
from modules.property_sync.redremax.errors import RedRemaxAuthError, RedRemaxError
from modules.property_sync.redremax.filters import LISTINGS_PATH, RedRemaxSyncFilterConfig
from modules.property_sync.redremax.mapping import DEFAULT_API_BASE_URL


ENV_BASE_URL = "REDREMAX_API_BASE_URL"
ENV_TIMEOUT = "REDREMAX_HTTP_TIMEOUT_SECONDS"
DEFAULT_TIMEOUT = 20
MAX_RETRIES = 3
MAX_RETRY_AFTER = 30


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    body: bytes
    headers: dict


def default_http_transport(url, headers, timeout):
    safe_headers = dict(headers or {})
    request = Request(url, headers=safe_headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return TransportResponse(
                int(response.status),
                response.read(),
                {key.lower(): value for key, value in response.headers.items()},
            )
    except HTTPError as error:
        body = error.read() if hasattr(error, "read") else b""
        return TransportResponse(
            int(error.code),
            body,
            {key.lower(): value for key, value in (error.headers or {}).items()},
        )
    except URLError as error:
        raise RedRemaxError("redremax_err_http", 502) from error


def _timeout_seconds():
    raw = os.environ.get(ENV_TIMEOUT, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        return max(1, min(int(raw), 60))
    except ValueError:
        return DEFAULT_TIMEOUT


def _base_url():
    return (os.environ.get(ENV_BASE_URL) or DEFAULT_API_BASE_URL).rstrip("/")


class RedRemaxClient:
    def __init__(self, *, auth_provider=None, transport=None, base_url=None, timeout=None, sleeper=None):
        self.auth_provider = auth_provider or default_auth_provider()
        self.transport = transport or default_http_transport
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout if timeout is not None else _timeout_seconds()
        self._sleep = sleeper or time.sleep

    def _headers(self):
        token = self.auth_provider.get_access_token() if self.auth_provider else None
        if not token:
            raise RedRemaxAuthError()
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }

    def get_listings(
        self,
        *,
        office_id,
        page=1,
        page_size=None,
        filters=None,
        agent=None,
    ):
        filters = filters or RedRemaxSyncFilterConfig()
        pairs = filters.query_pairs(
            office_id=office_id,
            page=page,
            page_size=page_size,
            agent=agent,
        )
        url = f"{self.base_url}{LISTINGS_PATH}?{urlencode(pairs, doseq=True)}"
        response = self._get_with_retries(url)
        if response.status_code in (401, 403):
            raise RedRemaxAuthError()
        if response.status_code >= 400:
            raise RedRemaxError("redremax_err_http", response.status_code)
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RedRemaxError("redremax_err_invalid_json", 502) from error
        return parse_listings_payload(payload)

    def _get_with_retries(self, url):
        headers = self._headers()
        last = None
        for attempt in range(MAX_RETRIES):
            last = self.transport(url, headers, self.timeout)
            if last.status_code != 429:
                return last
            if attempt + 1 >= MAX_RETRIES:
                raise RedRemaxError("redremax_err_rate_limited", 429)
            retry_after = _retry_after_seconds(last.headers)
            self._sleep(retry_after)
        return last


def _retry_after_seconds(headers):
    raw = (headers or {}).get("retry-after") or "1"
    try:
        return max(1, min(int(float(raw)), MAX_RETRY_AFTER))
    except (TypeError, ValueError):
        return 1


def parse_listings_payload(payload):
    root = payload or {}
    data = root.get("data") if isinstance(root.get("data"), dict) else root
    results = data.get("results")
    if results is None:
        results = root.get("results")
    if not isinstance(results, list):
        results = []
    return {
        "results": results,
        "page": _as_int(data.get("page") or root.get("page"), 1),
        "page_size": _as_int(data.get("pageSize") or data.get("page_size"), 0),
        "total_items": _as_int(data.get("totalItems") or data.get("total_items"), 0),
        "total_pages": _as_int(data.get("totalPages") or data.get("total_pages"), 1),
    }


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
