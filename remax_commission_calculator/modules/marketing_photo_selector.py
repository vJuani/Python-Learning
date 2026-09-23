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
    "vista",
    "balcon",
    "balcón",
    "balcony",
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
    ("fachada", ("fachada", "frente", "exterior", "facade")),
    ("vista", ("vista", "view", "panorama")),
    ("pileta", ("pileta", "piscina", "pool")),
    ("balcon", ("balcon", "balcón", "balcony")),
    ("jardin", ("jardin", "jardín", "garden", "patio", "parque")),
    ("living", ("living", "estar", "salon", "salón", "ambiente")),
    ("cocina", ("cocina", "kitchen")),
    ("dormitorio", ("dormitorio", "habitacion", "habitación", "bedroom")),
    ("bano", ("baño", "bano", "bath", "toilette")),
)
LIFESTYLE_SCENES = frozenset({"fachada", "vista", "balcon", "pileta", "jardin"})
LIFESTYLE_TOKENS = (
    "fachada",
    "frente",
    "exterior",
    "facade",
    "vista",
    "view",
    "balcon",
    "balcón",
    "balcony",
    "pileta",
    "piscina",
    "pool",
    "jardin",
    "jardín",
    "garden",
    "patio",
)
LIVING_TOKENS = ("living", "estar", "salon", "salón")
POWERFUL_MIN_SCORE = 70
SECONDARY_SCENE_BONUS = {
    "living": 80,
    "jardin": 75,
    "vista": 75,
    "pileta": 70,
    "balcon": 65,
    "cocina": 50,
    "dormitorio": 40,
    "fachada": 20,
    "bano": 10,
}
_PROFILE_MISS = object()
_PROFILE_CACHE = {}


def _blob(photo):
    photo = photo or {}
    stem = Path(str(photo.get("storage_key") or photo.get("path") or "")).stem
    parts = [
        photo.get("label"),
        photo.get("caption"),
        photo.get("alt"),
        photo.get("role"),
        photo.get("room"),
        photo.get("scene"),
        stem,
    ]
    return " ".join(str(part or "") for part in parts).casefold()


def photo_key(photo):
    photo = photo or {}
    if photo.get("id") is not None:
        return ("id", photo.get("id"))
    return ("key", photo.get("storage_key") or photo.get("path") or "")


def photo_scene_label(photo):
    blob = _blob(photo)
    for key, needles in SECONDARY_LABELS:
        if any(token in blob for token in needles):
            return key
    return None


def _visual_profile(photo):
    path = resolve_media_filesystem_path(photo)
    if path is None:
        return None
    cache_key = str(path)
    if cache_key in _PROFILE_CACHE:
        cached = _PROFILE_CACHE[cache_key]
        return None if cached is _PROFILE_MISS else cached
    try:
        with Image.open(path) as image:
            image.load()
            rgb = image.convert("RGB")
            width, height = rgb.size
            quality = rgb.resize((64, 64), Image.Resampling.BILINEAR)
            thumb_image = rgb.resize((8, 8), Image.Resampling.BILINEAR)
            stat = ImageStat.Stat(quality)
            mean = tuple(float(value) for value in stat.mean)
            profile = {
                "mean": mean,
                "mean_luma": sum(mean) / 3.0,
                "variance": sum(stat.var) / 3.0,
                "width": width,
                "height": height,
                "ratio": width / float(height or 1),
                "thumb": tuple(thumb_image.convert("RGB").getdata()),
            }
    except Exception:
        _PROFILE_CACHE[cache_key] = _PROFILE_MISS
        return None
    _PROFILE_CACHE[cache_key] = profile
    return profile


def _photo_dimensions(photo):
    profile = _visual_profile(photo)
    if profile:
        return int(profile["width"] or 0), int(profile["height"] or 0)
    try:
        width = int((photo or {}).get("width") or 0)
        height = int((photo or {}).get("height") or 0)
    except (TypeError, ValueError):
        return 0, 0
    return width, height


def _resolution_score(photo, *, hero=False):
    width, height = _photo_dimensions(photo)
    if width <= 0 or height <= 0:
        return 0
    long_edge = max(width, height)
    score = 0
    cover_scale = max(1080.0 / float(width), 1350.0 / float(height))
    if hero:
        if cover_scale <= 1.0:
            score += 40
        elif cover_scale <= 1.15:
            score += 28
        elif cover_scale <= 1.4:
            score += 8
        else:
            score -= 6
        if long_edge >= 2400:
            score += 20
        elif long_edge >= 1600:
            score += 12
        elif long_edge >= 1080:
            score += 4
    else:
        if long_edge >= 1600:
            score += 10
        elif long_edge >= 800:
            score += 4
    return score


def _quality_score(photo):
    profile = _visual_profile(photo)
    if profile is None:
        return 12
    mean = profile["mean_luma"]
    variance = profile["variance"]
    width = profile["width"]
    height = profile["height"]
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
    ratio = profile["ratio"]
    if 1.15 <= ratio <= 1.9:
        score += 8
    elif ratio < 0.7:
        score -= 4
    if min(width, height) < 600:
        score -= 8
    return score


def score_listing_photo(photo, *, hero=False):
    blob = _blob(photo)
    score = _quality_score(photo) + _resolution_score(photo, hero=hero)
    if photo.get("is_cover"):
        score += 28
    if any(token in blob for token in AVOID_HINTS):
        score -= 40
    if hero:
        if any(token in blob for token in ("fachada", "frente", "exterior", "facade")):
            score += 24
        elif any(token in blob for token in ("vista", "view", "balcon", "balcón", "pileta", "piscina")):
            score += 22
        elif any(token in blob for token in ("living", "estar", "salon", "salón")):
            score += 16
        elif any(token in blob for token in ("cocina", "jardin", "jardín", "garden", "patio")):
            score += 12
    else:
        if any(token in blob for token in HERO_HINTS):
            score += 6
    return score


def photos_are_near_duplicates(left, right):
    if left is right:
        return True
    scene_left = photo_scene_label(left)
    scene_right = photo_scene_label(right)
    if scene_left and scene_right and scene_left == scene_right:
        return True
    profile_left = _visual_profile(left)
    profile_right = _visual_profile(right)
    if not profile_left or not profile_right:
        return False
    thumb_left = profile_left.get("thumb") or ()
    thumb_right = profile_right.get("thumb") or ()
    if len(thumb_left) != len(thumb_right) or not thumb_left:
        return False
    mse = sum(
        (left_pixel[channel] - right_pixel[channel]) ** 2
        for left_pixel, right_pixel in zip(thumb_left, thumb_right)
        for channel in range(3)
    ) / float(len(thumb_left) * 3)
    return mse < 40


def is_lifestyle_strong(photo):
    if not photo:
        return False
    blob = _blob(photo)
    if any(token in blob for token in AVOID_HINTS):
        return False
    scene = photo_scene_label(photo)
    if scene in LIFESTYLE_SCENES or any(token in blob for token in LIFESTYLE_TOKENS):
        return True
    if scene == "living" or any(token in blob for token in LIVING_TOKENS):
        profile = _visual_profile(photo)
        if profile is None:
            return False
        return profile["variance"] >= 200 and 60 <= profile["mean_luma"] <= 210
    return False


def is_powerful_hero(photo):
    if not photo or not is_lifestyle_strong(photo):
        return False
    profile = _visual_profile(photo)
    if profile is None:
        return False
    if profile["variance"] < 150:
        return False
    if min(profile["width"], profile["height"]) < 240:
        return False
    return score_listing_photo(photo, hero=True) >= POWERFUL_MIN_SCORE


def unique_scene_keys(photos):
    keys = []
    seen = set()
    for photo in photos or []:
        label = photo_scene_label(photo) or f"photo:{photo_key(photo)}"
        if label in seen:
            continue
        seen.add(label)
        keys.append(label)
    return keys


def _append_diverse(chosen, photo):
    key = photo_key(photo)
    if key in {photo_key(item) for item in chosen}:
        return False
    if any(photos_are_near_duplicates(photo, item) for item in chosen):
        return False
    chosen.append(photo)
    return True


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
    rest = [item for item in ranked if photo_key(item) != photo_key(hero)]
    rest.sort(
        key=lambda item: (
            score_listing_photo(item, hero=False)
            + SECONDARY_SCENE_BONUS.get(photo_scene_label(item), 30),
            item.get("id") or 0,
        ),
        reverse=True,
    )
    if rest:
        rotated = rest[index % len(rest) :] + rest[: index % len(rest)]
    else:
        rotated = []
    chosen = [hero]
    hero_scene = photo_scene_label(hero)
    used_labels = {hero_scene} if hero_scene else set()
    for photo in rotated:
        if len(chosen) >= limit:
            break
        label = photo_scene_label(photo)
        if label and label in used_labels:
            continue
        if _append_diverse(chosen, photo) and label:
            used_labels.add(label)
    if len(chosen) < min(limit, len(items)):
        for photo in rotated:
            if len(chosen) >= limit:
                break
            if _append_diverse(chosen, photo):
                label = photo_scene_label(photo)
                if label:
                    used_labels.add(label)
    return chosen[:limit]
