"""RedREMAX photo URL rules. Remote reference only. No signed URL persistence."""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse, urlunparse

from modules.property_sync.redremax.mapping import PHOTO_HOST_ALLOWLIST

BETA_PHOTO_LIMIT = 5

PHOTO_URL_KEYS = ("cdn", "cloudfront", "prefix")

SIGNED_MARKERS = (
    "x-amz-algorithm",
    "x-amz-credential",
    "x-amz-signature",
    "x-amz-expires",
    "x-amz-date",
    "x-amz-security-token",
)


def is_signed_url(url):
    raw = str(url or "").lower()
    return any(marker in raw for marker in SIGNED_MARKERS)


def canonical_photo_url(url):
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def photo_host(url):
    parsed = urlparse(str(url or "").strip())
    return (parsed.hostname or "").strip().lower()


def is_allowed_redremax_photo_url(url):
    """Allowlisted HTTPS host + path. Query signatures are ignored, not stored."""
    canonical = canonical_photo_url(url)
    if not canonical:
        return False
    parsed = urlparse(canonical)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host in PHOTO_HOST_ALLOWLIST


def photo_external_id(url):
    canonical = canonical_photo_url(url)
    if not canonical:
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reject_reason(url):
    raw = str(url or "").strip()
    if not raw or raw.lower() in {"null", "none"}:
        return "empty"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "not_url"
    if parsed.scheme != "https":
        return "http"
    host = (parsed.hostname or "").lower()
    if host not in PHOTO_HOST_ALLOWLIST:
        return "invalid_host"
    return None


def resolve_photo_source(raw):
    """cdn, then cloudfront, then prefix. Store canonical URL only."""
    if not isinstance(raw, dict):
        return None, None
    for key in PHOTO_URL_KEYS:
        value = str(raw.get(key) or "").strip()
        if not value or value.lower() in {"null", "none"}:
            continue
        if is_allowed_redremax_photo_url(value):
            return canonical_photo_url(value), key
    return None, None


def resolve_photo_source_url(raw):
    url, _key = resolve_photo_source(raw)
    return url


def _first_photo_candidate(raw):
    if not isinstance(raw, dict):
        return str(raw or "").strip()
    for key in PHOTO_URL_KEYS:
        value = str(raw.get(key) or "").strip()
        if value and value.lower() not in {"null", "none"}:
            return value
    return ""


def inspect_photos(photos, *, limit=BETA_PHOTO_LIMIT):
    """Validate every photo, then pick up to `limit`, always keeping primary."""
    audit = {
        "payload_count": 0,
        "valid_count": 0,
        "selected_count": 0,
        "rejected_count": 0,
        "signed_query_stripped": 0,
        "reject_reasons": {},
        "unknown_hosts": [],
        "selected": [],
    }
    if not isinstance(photos, list):
        return audit
    audit["payload_count"] = len(photos)
    valid = []
    unknown = []
    reasons = {}
    for index, raw in enumerate(photos):
        source_url, source_key = resolve_photo_source(raw)
        if source_url:
            chosen = str((raw or {}).get(source_key) or "") if isinstance(raw, dict) else ""
            if is_signed_url(chosen):
                audit["signed_query_stripped"] += 1
            valid.append(
                {
                    "external_media_id": photo_external_id(source_url),
                    "original_url": source_url,
                    "url_kind": "stable",
                    "storage_strategy": "remote_reference",
                    "position": index,
                    "is_cover": bool(isinstance(raw, dict) and raw.get("primary")),
                    "content_type": "image/jpeg",
                }
            )
            continue
        candidate = _first_photo_candidate(raw)
        reason = _reject_reason(candidate)
        reasons[reason] = reasons.get(reason, 0) + 1
        host = photo_host(candidate)
        if reason == "invalid_host" and host and host not in unknown:
            unknown.append(host)
    selected = select_beta_photos(valid, limit=limit)
    audit["valid_count"] = len(valid)
    audit["selected"] = selected
    audit["selected_count"] = len(selected)
    audit["rejected_count"] = max(audit["payload_count"] - len(valid), 0)
    audit["reject_reasons"] = reasons
    audit["unknown_hosts"] = unknown
    return audit


def map_photos(photos, *, limit=BETA_PHOTO_LIMIT):
    return inspect_photos(photos, limit=limit)["selected"]


def select_beta_photos(items, *, limit=BETA_PHOTO_LIMIT):
    """Always keep primary, then fill in array order up to the beta cap."""
    if limit is None or int(limit) <= 0:
        return list(items or [])
    cap = int(limit)
    chosen = []
    seen = set()
    primary = next((item for item in (items or []) if item.get("is_cover")), None)
    if primary:
        chosen.append(primary)
        seen.add(primary.get("external_media_id") or primary.get("original_url"))
    for item in items or []:
        if len(chosen) >= cap:
            break
        identity = item.get("external_media_id") or item.get("original_url")
        if identity in seen:
            continue
        seen.add(identity)
        chosen.append(item)
    return chosen


def has_blueprints(payload):
    blueprints = (payload or {}).get("blueprints")
    return isinstance(blueprints, list) and len(blueprints) > 0


def public_photo_audit(audit):
    audit = audit or {}
    return {
        "payload_count": int(audit.get("payload_count") or 0),
        "valid_count": int(audit.get("valid_count") or 0),
        "selected_count": int(audit.get("selected_count") or 0),
        "rejected_count": int(audit.get("rejected_count") or 0),
        "signed_query_stripped": int(audit.get("signed_query_stripped") or 0),
        "reject_reasons": dict(audit.get("reject_reasons") or {}),
        "unknown_hosts": list(audit.get("unknown_hosts") or []),
    }
