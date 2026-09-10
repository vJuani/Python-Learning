"""HTTP client for RedREMAX listings. Transport is injectable. Tokens never logged."""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from modules.property_sync.redremax.auth import (
    default_auth_provider,
    normalize_access_token,
)
from modules.property_sync.redremax.errors import RedRemaxAuthError, RedRemaxError
from modules.property_sync.redremax.filters import LISTINGS_PATH, RedRemaxSyncFilterConfig
from modules.property_sync.redremax.mapping import DEFAULT_API_BASE_URL
from modules.property_sync.redremax.safe_log import (
    format_safe_http_message,
    safe_content_type,
    safe_query_pairs,
    safe_response_snippet,
)


ENV_BASE_URL = "REDREMAX_API_BASE_URL"
ENV_TIMEOUT = "REDREMAX_HTTP_TIMEOUT_SECONDS"
DEFAULT_TIMEOUT = 20
MAX_RETRIES = 3
MAX_RETRY_AFTER = 30

logger = logging.getLogger(__name__)


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


def _is_timeout(error):
    if isinstance(error, (TimeoutError, socket.timeout)):
        return True
    reason = getattr(error, "reason", None)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    text = str(reason or error).lower()
    return "timed out" in text or "timeout" in type(reason or error).__name__.lower()


def _requests_exception_class(name):
    try:
        import requests
    except ImportError:
        return None
    return getattr(requests, name, None)


class RedRemaxClient:
    def __init__(self, *, auth_provider=None, transport=None, base_url=None, timeout=None, sleeper=None):
        self.auth_provider = auth_provider or default_auth_provider()
        self.transport = transport or default_http_transport
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout if timeout is not None else _timeout_seconds()
        self._sleep = sleeper or time.sleep

    def resolved_token(self):
        raw = self.auth_provider.get_access_token() if self.auth_provider else None
        return normalize_access_token(raw)

    def _headers(self):
        token = self.resolved_token()
        if not token:
            raise RedRemaxAuthError("redremax_err_token_missing")
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
        token_configured = bool(self.resolved_token())
        self._log_request_meta(pairs, office_id=office_id, token_configured=token_configured)
        try:
            response = self._get_with_retries(url, office_id=office_id)
        except RedRemaxError:
            raise
        except Exception as error:
            self._raise_network_error(error, office_id)
        self._log_response(response, office_id)
        self._raise_for_status(response, office_id)
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            logger.exception(
                "RedREMAX request failed office_id=%s error_type=%s",
                office_id,
                type(error).__name__,
            )
            raise RedRemaxError("redremax_err_invalid_json", 502) from error
        return parse_listings_payload(payload)

    def diagnose_listings(self, *, office_id, filters=None):
        """Probe listings without raising. Returns a safe admin report only."""
        filters = filters or RedRemaxSyncFilterConfig()
        pairs = filters.query_pairs(office_id=office_id, page=1, page_size=1)
        token_configured = bool(self.resolved_token())
        report = {
            "base_url": self.base_url,
            "endpoint": LISTINGS_PATH,
            "office_id": office_id,
            "token_configured": token_configured,
            "http_status": None,
            "safe_response_message": "",
            "content_type": "",
            "query": safe_query_pairs(pairs),
        }
        if not office_id:
            report["safe_response_message"] = "missing office_id"
            return report
        if not token_configured:
            report["safe_response_message"] = "token_configured=false"
            return report
        url = f"{self.base_url}{LISTINGS_PATH}?{urlencode(pairs, doseq=True)}"
        self._log_request_meta(pairs, office_id=office_id, token_configured=True)
        try:
            response = self._get_with_retries(url, office_id=office_id)
        except RedRemaxError as error:
            report["http_status"] = error.status_code
            report["safe_response_message"] = error.safe_message or error.message_key
            return report
        except Exception as error:
            try:
                self._raise_network_error(error, office_id)
            except RedRemaxError as mapped:
                report["http_status"] = mapped.status_code
                report["safe_response_message"] = mapped.message_key
                return report
        self._log_response(response, office_id)
        content_type = safe_content_type(response.headers)
        snippet = safe_response_snippet(response.body, content_type)
        report["http_status"] = response.status_code
        report["content_type"] = content_type
        report["safe_response_message"] = format_safe_http_message(
            response.status_code, snippet
        )
        return report

    def _log_request_meta(self, pairs, *, office_id, token_configured):
        logger.info("RedREMAX base_url=%s", self.base_url)
        logger.info(
            "RedREMAX request method=GET path=%s office_id=%s token_configured=%s",
            LISTINGS_PATH,
            office_id,
            token_configured,
        )
        logger.info(
            "RedREMAX query=%s",
            "&".join(f"{key}={value}" for key, value in safe_query_pairs(pairs)),
        )

    def _log_response(self, response, office_id):
        content_type = safe_content_type(response.headers)
        snippet = safe_response_snippet(response.body, content_type)
        logger.info(
            "RedREMAX response status=%s office_id=%s content_type=%s",
            response.status_code,
            office_id,
            content_type or "-",
        )
        if response.status_code >= 400 and snippet:
            logger.warning(
                "RedREMAX response_body_safe status=%s office_id=%s body=%s",
                response.status_code,
                office_id,
                snippet,
            )

    def _raise_for_status(self, response, office_id):
        content_type = safe_content_type(response.headers)
        snippet = safe_response_snippet(response.body, content_type)
        safe_message = format_safe_http_message(response.status_code, snippet)
        if response.status_code == 401:
            logger.warning(
                "RedREMAX authentication failed status=401 office_id=%s",
                office_id,
            )
            raise RedRemaxAuthError(
                "redremax_err_auth_401", 401, safe_message=safe_message
            )
        if response.status_code == 403:
            logger.warning(
                "RedREMAX authorization failed status=403 office_id=%s",
                office_id,
            )
            raise RedRemaxAuthError(
                "redremax_err_auth_403", 403, safe_message=safe_message
            )
        if response.status_code == 429:
            logger.warning(
                "RedREMAX rate limited status=429 office_id=%s",
                office_id,
            )
            raise RedRemaxError(
                "redremax_err_rate_limited", 429, safe_message=safe_message
            )
        if response.status_code >= 400:
            logger.error(
                "RedREMAX HTTP error status=%s office_id=%s",
                response.status_code,
                office_id,
            )
            raise RedRemaxError(
                "redremax_err_http", response.status_code, safe_message=safe_message
            )

    def _get_with_retries(self, url, *, office_id):
        headers = self._headers()
        last = None
        for attempt in range(MAX_RETRIES):
            last = self.transport(url, headers, self.timeout)
            if last.status_code != 429:
                return last
            logger.warning(
                "RedREMAX rate limited status=429 office_id=%s",
                office_id,
            )
            if attempt + 1 >= MAX_RETRIES:
                raise RedRemaxError("redremax_err_rate_limited", 429)
            retry_after = _retry_after_seconds(last.headers)
            self._sleep(retry_after)
        return last

    def _raise_network_error(self, error, office_id):
        timeout_cls = _requests_exception_class("Timeout")
        connection_cls = _requests_exception_class("ConnectionError")
        request_cls = _requests_exception_class("RequestException")
        logger.exception(
            "RedREMAX request failed office_id=%s error_type=%s",
            office_id,
            type(error).__name__,
        )
        if _is_timeout(error) or (timeout_cls and isinstance(error, timeout_cls)):
            raise RedRemaxError("redremax_err_timeout", 504) from error
        if isinstance(error, URLError) or (
            connection_cls and isinstance(error, connection_cls)
        ):
            raise RedRemaxError("redremax_err_http", 502) from error
        if request_cls and isinstance(error, request_cls):
            raise RedRemaxError("redremax_err_http", 502) from error
        raise RedRemaxError("redremax_err_http", 502) from error


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
