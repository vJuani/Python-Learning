"""Pick real listing photos for a creative. Never invent media."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageStat

from modules.listing_photo_origin import eligible_listing_photos, source_priority
from modules.property_sync.media import resolve_media_filesystem_path

HERO_HINTS = (
    "fachada",
    "frente",
    "exterior",
    "facade",
    "living",
    "salon",
    "salón",
    "ambiente",
    "estar",
    "cocina",
    "kitchen",
    "jardin",
    "jardín",
    "garden",
    "patio",
    "pileta",
    "piscina",
)
AVOID_HINTS = (
    "plano",
    "mapa",
    "croquis",
    "documento",
    "tecnico",
    "técnico",
    "blueprint",
    "floorplan",
    "floor_plan",
)
SECONDARY_LABELS = (
    ("living", ("living", "estar", "salon", "salón", "ambiente")),
    ("cocina", ("cocina", "kitchen")),
    ("jardin", ("jardin", "jardín", "garden", "patio", "parque")),
    ("fachada", ("fachada", "frente", "exterior", "facade")),
    ("dormitorio", ("dormitorio", "habitacion", "habitación", "bedroom")),
    ("bano", ("baño", "bano", "bath", "toilette")),
    ("balcon", ("balcon", "balcón", "balcony")),
)


def _blob(photo):
    parts = [
        photo.get("label"),
        photo.get("caption"),
        photo.get("alt"),
        photo.get("role"),
        photo.get("room"),
    ]
    return " ".join(str(part or "") for part in parts).casefold()


def photo_scene_label(photo):
    blob = _blob(photo)
    for key, needles in SECONDARY_LABELS:
        if any(token in blob for token in needles):
            return key
    return None


def _quality_score(photo):
    path = resolve_media_filesystem_path(photo)
    if path is None or not Path(path).is_file():
        return 12
    try:
        with Image.open(path) as image:
            image.load()
            sample = image.convert("RGB").resize((64, 64))
            stat = ImageStat.Stat(sample)
            mean = sum(stat.mean) / 3.0
            variance = sum(stat.var) / 3.0
            width, height = image.size
    except Exception:
        return 8
    score = 10
    if 70 <= mean <= 210:
        score += 18
    elif mean < 42 or mean > 235:
        score -= 16
    else:
        score += 4
    if variance > 380:
        score += 12
    elif variance < 80:
        score -= 10
    ratio = width / float(height or 1)
    if 1.15 <= ratio <= 1.9:
        score += 8
    elif ratio < 0.7:
        score -= 4
    if min(width, height) < 600:
        score -= 8
    return score


def score_listing_photo(photo, *, hero=False):
    blob = _blob(photo)
    score = _quality_score(photo)
    if photo.get("is_cover"):
        score += 28
    if any(token in blob for token in AVOID_HINTS):
        score -= 40
    if hero:
        if any(token in blob for token in ("fachada", "frente", "exterior", "facade")):
            score += 24
        elif any(token in blob for token in ("living", "estar", "salon", "salón")):
            score += 16
        elif any(token in blob for token in ("cocina", "jardin", "jardín", "garden", "patio")):
            score += 12
    else:
        if any(token in blob for token in HERO_HINTS):
            score += 6
    return score


def select_photos_for_item(photos, *, fmt, index, limit=None):
    items = eligible_listing_photos(photos)
    if not items:
        return []
    if limit is None:
        limit = 5 if fmt == "flyer" else (4 if fmt == "post" else 3)
    ranked = sorted(
        items,
        key=lambda item: (
            source_priority(item),
            score_listing_photo(item, hero=True),
            item.get("id") or 0,
        ),
        reverse=True,
    )
    hero = ranked[0]
    rest = [item for item in ranked if item is not hero]
    rest.sort(
        key=lambda item: (score_listing_photo(item, hero=False), item.get("id") or 0),
        reverse=True,
    )
    if rest:
        rotated = rest[index % len(rest) :] + rest[: index % len(rest)]
    else:
        rotated = []
    chosen = [hero]
    seen = {hero.get("id") or hero.get("storage_key")}
    used_labels = {photo_scene_label(hero)}
    for photo in rotated:
        key = photo.get("id") or photo.get("storage_key")
        if key in seen:
            continue
        label = photo_scene_label(photo)
        if label and label in used_labels and len(rotated) > 3:
            continue
        seen.add(key)
        if label:
            used_labels.add(label)
        chosen.append(photo)
        if len(chosen) >= limit:
            break
    if len(chosen) < min(limit, len(items)):
        for photo in items:
            key = photo.get("id") or photo.get("storage_key")
            if key in seen:
                continue
            chosen.append(photo)
            seen.add(key)
            if len(chosen) >= limit:
                break
    return chosen[:limit]
