"""Parse RedREMAX JSON exports. Never stores tokens, cookies, or private CRM fields."""

from __future__ import annotations

import json
import re

from modules.property_sync.redremax.normalizer import RedRemaxPropertyNormalizer
from modules.property_sync.redremax.privacy import is_sensitive_key, strip_sensitive_fields

INGESTION_METHOD_MANUAL_JSON = "manual_json_import"

MAX_JSON_FILE_BYTES = 8 * 1024 * 1024
MAX_JSON_FILES = 15
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_LISTINGS = 2500
SESSION_TTL_SECONDS = 2 * 60 * 60

ENVELOPE_SECRET_KEYS = frozenset(
    {
        "authorization",
        "bearer",
        "cookie",
        "cookies",
        "jsessionid",
        "headers",
        "requestheaders",
        "request_headers",
        "set-cookie",
        "set_cookie",
    }
)

_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\bauthorization\s*[:=]"),
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"(?i)\bjsessionid\s*[:=]"),
    re.compile(r"(?i)\bcookie\s*[:=]"),
)

_STORED_FORBIDDEN_KEYS = frozenset(
    {
        "clients",
        "clientsdata",
        "documents",
        "privatenotes",
        "authorization",
        "bearer",
        "jsessionid",
        "cookie",
        "cookies",
    }
)


class DemoImportError(ValueError):
    def __init__(self, message_key, status_code=400):
        super().__init__(message_key)
        self.message_key = message_key
        self.status_code = status_code


def assert_json_filename(filename):
    name = str(filename or "").strip().lower()
    if not name.endswith(".json"):
        raise DemoImportError("redremax_err_import_not_json")


def raw_text_contains_secrets(text):
    raw = str(text or "")
    return any(pattern.search(raw) for pattern in _SECRET_TEXT_PATTERNS)


def _reject_secret_keys(payload):
    if not isinstance(payload, dict):
        return
    for key in payload:
        lowered = str(key or "").strip().lower().replace("-", "")
        if lowered in ENVELOPE_SECRET_KEYS or is_sensitive_key(key):
            raise DemoImportError("redremax_err_import_secrets")


def extract_listings_from_payload(payload):
    if not isinstance(payload, dict):
        raise DemoImportError("redremax_err_import_invalid_envelope")
    _reject_secret_keys(payload)
    if isinstance(payload.get("data"), dict):
        _reject_secret_keys(payload["data"])
        results = payload["data"].get("results")
    elif "results" in payload:
        results = payload["results"]
    else:
        raise DemoImportError("redremax_err_import_invalid_envelope")
    if not isinstance(results, list):
        raise DemoImportError("redremax_err_import_invalid_envelope")
    return results


def merge_listings_by_external_id(pages):
    merged = []
    seen = set()
    for page in pages or []:
        for raw in page or []:
            if not isinstance(raw, dict):
                continue
            identity = str(raw.get("id") or raw.get("external_id") or "").strip()
            if not identity or identity in seen:
                continue
            seen.add(identity)
            merged.append(raw)
    return merged


def parse_uploaded_json_files(files):
    """Read in-memory uploads. Original bytes are not written to disk."""
    storages = [item for item in (files or []) if item and getattr(item, "filename", None)]
    if not storages:
        raise DemoImportError("redremax_err_import_no_files")
    if len(storages) > MAX_JSON_FILES:
        raise DemoImportError("redremax_err_import_too_many_files")

    total_bytes = 0
    pages = []
    for storage in storages:
        assert_json_filename(storage.filename)
        payload_bytes = storage.read()
        if payload_bytes is None:
            payload_bytes = b""
        if not isinstance(payload_bytes, (bytes, bytearray)):
            payload_bytes = str(payload_bytes).encode("utf-8")
        size = len(payload_bytes)
        if size > MAX_JSON_FILE_BYTES:
            raise DemoImportError("redremax_err_import_file_too_large")
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise DemoImportError("redremax_err_import_file_too_large")
        try:
            text = payload_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DemoImportError("redremax_err_import_invalid_json") from error
        if raw_text_contains_secrets(text):
            raise DemoImportError("redremax_err_import_secrets")
        try:
            payload = json.loads(text)
        except ValueError as error:
            raise DemoImportError("redremax_err_import_invalid_json") from error
        pages.append(extract_listings_from_payload(payload))

    merged = merge_listings_by_external_id(pages)
    if len(merged) > MAX_LISTINGS:
        raise DemoImportError("redremax_err_import_too_many_listings")
    return merged


def prepare_listing_for_import(raw, *, office_id, normalizer=None):
    """Normalize through RedRemaxPropertyNormalizer. Sensitive fields are dropped first."""
    normalizer = normalizer or RedRemaxPropertyNormalizer()
    stripped = strip_sensitive_fields(raw or {})
    hub, warnings = normalizer.normalize(stripped, expected_office_id=office_id)
    if hub is None:
        return {
            "_skipped": True,
            "warnings": list(warnings or []),
            "external_id": str((raw or {}).get("id") or "").strip() or None,
        }
    metadata = dict(hub.get("external_metadata") or {})
    metadata["ingestion_method"] = INGESTION_METHOD_MANUAL_JSON
    hub["external_metadata"] = metadata
    hub["warnings"] = list(hub.get("warnings") or [])
    assert_safe_to_store(hub)
    return hub


def assert_safe_to_store(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key or "").strip().lower() in _STORED_FORBIDDEN_KEYS:
                raise DemoImportError("redremax_err_import_secrets")
            assert_safe_to_store(nested)
        return
    if isinstance(value, list):
        for item in value:
            assert_safe_to_store(item)
