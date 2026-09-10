"""Sanitize RedREMAX diagnostics. Never log tokens, cookies, or auth headers."""

from __future__ import annotations

import json
import re

SAFE_BODY_LIMIT = 1000

_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)authorization\s*[:=]\s*\S+"),
    re.compile(r"(?i)jsessionid\s*[:=]\s*\S+"),
    re.compile(r"(?i)cookie\s*[:=]\s*\S+"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
)

_SAFE_CONTENT_TYPES = (
    "application/json",
    "text/plain",
    "text/html",
    "text/json",
    "application/problem+json",
)


def sanitize_text(value):
    text = str(value or "")
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def safe_content_type(headers):
    raw = str((headers or {}).get("content-type") or "").split(";", 1)[0].strip().lower()
    return raw or ""


def is_safe_text_content_type(content_type):
    raw = str(content_type or "").split(";", 1)[0].strip().lower()
    if raw in _SAFE_CONTENT_TYPES:
        return True
    return raw.startswith("text/") or raw.endswith("+json")


def safe_response_snippet(body, content_type):
    if not is_safe_text_content_type(content_type):
        return ""
    try:
        text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body or "")
    except Exception:
        return ""
    text = text.strip()
    if not text:
        return ""
    extracted = _extract_message(text)
    snippet = sanitize_text(extracted or text).replace("\n", " ").strip()
    if len(snippet) > SAFE_BODY_LIMIT:
        snippet = snippet[:SAFE_BODY_LIMIT].rstrip() + "…"
    return snippet


def format_safe_http_message(status_code, snippet):
    status = int(status_code)
    detail = (snippet or "").strip()
    if detail:
        return f"HTTP {status} — {detail}"
    return f"HTTP {status}"


def _extract_message(text):
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text
    if isinstance(payload, dict):
        for key in ("message", "error", "title", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = value.get("message") or value.get("detail")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
        return json.dumps(payload, ensure_ascii=False)
    return text


def safe_query_pairs(pairs):
    return [(str(key), str(value)) for key, value in (pairs or [])]
