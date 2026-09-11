"""Pick real listing photos for a creative. Never invent media."""

from __future__ import annotations


def select_photos_for_item(photos, *, fmt, index, limit=None):
    items = list(photos or [])
    if not items:
        return []
    if limit is None:
        limit = 5 if fmt == "flyer" else (3 if fmt == "post" else 3)
    cover = items[0]
    rest = items[1:]
    if not rest:
        return items[:limit]
    rotated = rest[index % len(rest) :] + rest[: index % len(rest)]
    chosen = [cover] + rotated
    seen = set()
    unique = []
    for photo in chosen:
        key = photo.get("id") or photo.get("storage_key")
        if key in seen:
            continue
        seen.add(key)
        unique.append(photo)
        if len(unique) >= limit:
            break
    return unique
