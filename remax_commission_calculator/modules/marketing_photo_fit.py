"""Choose cover vs contain for listing photos without stretching or upscaling."""

from __future__ import annotations

COVER_MAX_CROP = 0.16
MAX_SHARP_SCALE = 1.15
MAX_CONTAIN_SCALE = 1.5
FULL_POSTER_FRAME = (1080, 1350)

# Master canvases keep the official reference aspect ratio at 1080 px width.
# Clean Grid reference 581x683, Lifestyle/Premium references 390x612.
CLEAN_GRID_MASTER = (1080, 1270)
FULL_BLEED_MASTER = (1080, 1695)
_CLEAN_GRID_UNIT = 1080 / 581.0
_FULL_BLEED_UNIT = 1080 / 390.0
# Photo boxes never sit under text: the listing photo is shown untouched.
# Max area each photo may use. The box is then sized to the photo, not the other way round.
FULL_BLEED_PHOTO_MAX = (990, 800)
CLEAN_GRID_HERO_MAX = (int(329 * _CLEAN_GRID_UNIT), 700)
CLEAN_GRID_HERO_WIDE_MAX = (int(548 * _CLEAN_GRID_UNIT), 800)
CLEAN_GRID_THUMB_MAX = (int(213 * _CLEAN_GRID_UNIT), 520)

# Master anchors (px) the flowing layout is built on.
CLEAN_GRID_CARD_TOP = 76 * _CLEAN_GRID_UNIT
CLEAN_GRID_CARD_HEIGHT = 435 * _CLEAN_GRID_UNIT
CLEAN_GRID_PHOTO_AREA = 242 * _CLEAN_GRID_UNIT
CLEAN_GRID_THUMB_LEFT = 348 * _CLEAN_GRID_UNIT
CLEAN_GRID_THUMB_GAP = int(5 * _CLEAN_GRID_UNIT)
PREMIUM_PHOTO_TOP = int(290 * _FULL_BLEED_UNIT)
PREMIUM_BELOW_TOP = 481 * _FULL_BLEED_UNIT
LIFESTYLE_PHOTO_TOP = 200
LIFESTYLE_BELOW_TOP = 99 * _FULL_BLEED_UNIT
PHOTO_GAP = 40


def plan_original_photo(source_size, frame_size):
    """Whole photo, never cropped, never enlarged. Only shrinks when larger than the box."""
    source_w = int(source_size[0] or 0)
    source_h = int(source_size[1] or 0)
    if source_w <= 0 or source_h <= 0:
        return {
            "fit": "original",
            "crop": 0.0,
            "crop_percent": 0.0,
            "scale_factor": 1.0,
            "display_width": int(frame_size[0]),
            "display_height": int(frame_size[1]),
            "visual_transform": "none",
        }
    scale = min(1.0, frame_size[0] / float(source_w), frame_size[1] / float(source_h))
    display_w = source_w if scale >= 1.0 else max(1, min(int(frame_size[0]), round(source_w * scale)))
    display_h = source_h if scale >= 1.0 else max(1, min(int(frame_size[1]), round(source_h * scale)))
    return {
        "fit": "original",
        "crop": 0.0,
        "crop_percent": 0.0,
        "scale_factor": scale,
        "display_width": display_w,
        "display_height": display_h,
        "visual_transform": "none" if scale >= 1.0 else "downscale_to_fit",
    }


def cover_crop_fraction(source_size, frame_size):
    source_w, source_h = (float(source_size[0] or 0), float(source_size[1] or 0))
    frame_w, frame_h = (float(frame_size[0] or 0), float(frame_size[1] or 0))
    if source_w <= 0 or source_h <= 0 or frame_w <= 0 or frame_h <= 0:
        return 1.0
    source_ratio = source_w / source_h
    frame_ratio = frame_w / frame_h
    if source_ratio >= frame_ratio:
        return max(0.0, 1.0 - (frame_ratio / source_ratio))
    return max(0.0, 1.0 - (source_ratio / frame_ratio))


def cover_scale_required(source_size, frame_size):
    source_w, source_h = (float(source_size[0] or 0), float(source_size[1] or 0))
    frame_w, frame_h = (float(frame_size[0] or 0), float(frame_size[1] or 0))
    if source_w <= 0 or source_h <= 0 or frame_w <= 0 or frame_h <= 0:
        return 99.0
    return max(frame_w / source_w, frame_h / source_h)


def sharp_display_size(source_size, frame_size, *, max_scale=MAX_SHARP_SCALE):
    source_w, source_h = (float(source_size[0] or 0), float(source_size[1] or 0))
    frame_w, frame_h = (float(frame_size[0] or 0), float(frame_size[1] or 0))
    if source_w <= 0 or source_h <= 0:
        return 1, 1
    scale = min(max_scale, frame_w / source_w, frame_h / source_h)
    if scale <= 0:
        return 1, 1
    width = max(1, int(round(source_w * scale)))
    height = max(1, int(round(source_h * scale)))
    return width, height


def plan_photo_render(
    source_size,
    frame_size,
    *,
    max_crop=COVER_MAX_CROP,
    max_scale=MAX_SHARP_SCALE,
):
    crop = cover_crop_fraction(source_size, frame_size)
    cover_scale = cover_scale_required(source_size, frame_size)
    if cover_scale <= max_scale and crop <= max_crop:
        return {
            "fit": "cover",
            "crop": crop,
            "crop_percent": crop * 100.0,
            "scale_factor": cover_scale,
            "display_width": int(frame_size[0]),
            "display_height": int(frame_size[1]),
        }
    source_w, source_h = (float(source_size[0] or 0), float(source_size[1] or 0))
    contain_cap = MAX_CONTAIN_SCALE if source_w >= source_h else min(MAX_CONTAIN_SCALE, 1.35)
    display_width, display_height = sharp_display_size(
        source_size,
        frame_size,
        max_scale=contain_cap,
    )
    scale_factor = max(
        display_width / float(source_w or 1),
        display_height / float(source_h or 1),
    )
    return {
        "fit": "contain",
        "crop": crop,
        "crop_percent": 0.0,
        "scale_factor": scale_factor,
        "display_width": display_width,
        "display_height": display_height,
    }


def choose_photo_fit(
    source_size,
    frame_size,
    *,
    max_crop=COVER_MAX_CROP,
    max_scale=MAX_SHARP_SCALE,
):
    plan = plan_photo_render(
        source_size,
        frame_size,
        max_crop=max_crop,
        max_scale=max_scale,
    )
    return plan["fit"], plan["crop"]
