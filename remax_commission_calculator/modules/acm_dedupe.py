"""Identity-only comparable dedupe. No fuzzy/address heuristics yet."""

from __future__ import annotations


def _identity(item):
    payload = item or {}
    org = payload.get("organization_id")
    source = (payload.get("source_type") or payload.get("external_source") or "").strip()
    external_id = (
        payload.get("external_id")
        or payload.get("external_reference")
        or ""
    )
    external_id = str(external_id).strip()
    property_id = payload.get("comparable_property_id") or payload.get("property_id")
    if org and source and external_id:
        return ("ext", int(org), source, external_id)
    if org and property_id:
        return ("prop", int(org), int(property_id))
    return None


def deduplicate_comparable_candidates(candidates):
    """Keep first row per identity. Leaves unmatched rows untouched."""
    seen = set()
    unique = []
    for item in candidates or []:
        key = _identity(item)
        if key is None:
            unique.append(item)
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
