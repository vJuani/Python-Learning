"""Normalized property features. Grows without a schema change."""

from __future__ import annotations

import json
import unicodedata


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

FEATURE_ALIASES = {
    "balcony": "balcony",
    "balcon": "balcony",
    "balcón": "balcony",
    "terrace": "terrace",
    "terraza": "terrace",
    "garden": "garden",
    "jardin": "garden",
    "jardín": "garden",
    "pool": "pool",
    "pileta": "pool",
    "piscina": "pool",
    "grill": "grill",
    "parrilla": "grill",
    "laundry": "laundry",
    "lavadero": "laundry",
    "storage": "storage",
    "baulera": "storage",
    "elevator": "elevator",
    "ascensor": "elevator",
    "security": "security",
    "seguridad": "security",
    "furnished": "furnished",
    "amoblado": "furnished",
    "amueblado": "furnished",
}


def _fold_feature(value):
    normalized = unicodedata.normalize("NFD", str(value or ""))
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).casefold().strip()


def normalize_feature_key(value):
    folded = _fold_feature(value)
    if not folded:
        return None
    if folded in FEATURE_ALIASES:
        return FEATURE_ALIASES[folded]
    if folded in FEATURE_SET:
        return folded
    return None


def normalize_wanted_features(values):
    keys = []
    seen = set()
    for value in values or []:
        key = normalize_feature_key(value)
        if key is None or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


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
