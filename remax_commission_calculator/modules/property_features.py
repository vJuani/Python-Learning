"""Normalized property features. Grows without a schema change."""

from __future__ import annotations

import json


FEATURE_KEYS = (
    "balcony",
    "terrace",
    "garden",
    "pool",
    "grill",
    "laundry",
    "storage",
    "elevator",
    "security",
    "furnished",
)

FEATURE_SET = set(FEATURE_KEYS)


def normalize_property_features(raw):
    if not raw:
        return {}

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}

    if not isinstance(raw, dict):
        return {}

    features = {}
    for key, value in raw.items():
        name = str(key or "").strip()
        if not name:
            continue
        truthy = value is True or str(value).strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if name in FEATURE_SET:
            features[name] = bool(value) if not isinstance(value, str) else truthy
        elif truthy:
            features[name] = True
    return features


def features_to_json(features):
    normalized = normalize_property_features(features)
    if not normalized:
        return None
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def features_from_form(form):
    selected = set(form.getlist("feature"))
    extras = [
        part.strip()
        for part in (form.get("features") or "").replace(",", "\n").splitlines()
        if part.strip()
    ]
    selected.update(extras)
    return {key: True for key in FEATURE_KEYS if key in selected}


def active_feature_keys(features):
    normalized = normalize_property_features(features)
    return [key for key in FEATURE_KEYS if normalized.get(key)]
