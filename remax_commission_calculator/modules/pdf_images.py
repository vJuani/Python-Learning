"""PDF image fit helpers. Cover/contain/thumb. Preserve alpha unless composed."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps
from reportlab.platypus import Image as RLImage


def _open_image(source):
    if source is None:
        return None
    try:
        if isinstance(source, (bytes, bytearray)):
            image = Image.open(io.BytesIO(source))
        else:
            path = Path(source)
            if not path.is_file():
                return None
            image = Image.open(path)
        image.load()
        return ImageOps.exif_transpose(image)
    except Exception:
        return None


def _has_alpha(image):
    return image.mode in {"RGBA", "LA", "PA"} or (
        image.mode == "P" and "transparency" in image.info
    )


def _as_rgba(image):
    if image.mode == "RGBA":
        return image
    if image.mode == "P":
        return image.convert("RGBA")
    if image.mode in {"LA", "PA"}:
        return image.convert("RGBA")
    return image.convert("RGBA")


def _fit_cover(image, width, height):
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return image
    scale = max(width / src_w, height / src_h)
    resized = image.resize(
        (max(1, int(src_w * scale)), max(1, int(src_h * scale))),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _fit_contain(image, width, height, background=None):
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return image
    scale = min(width / src_w, height / src_h, 1.0)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    if background is None and _has_alpha(image):
        return resized
    canvas = Image.new("RGBA" if background is None else "RGB", (width, height), background or (0, 0, 0, 0))
    paste = _as_rgba(resized) if canvas.mode == "RGBA" else resized.convert("RGB")
    offset = ((width - new_w) // 2, (height - new_h) // 2)
    if canvas.mode == "RGBA":
        canvas.paste(paste, offset, paste)
        return canvas
    if _has_alpha(resized):
        canvas.paste(_as_rgba(resized).convert("RGB"), offset, _as_rgba(resized).split()[-1])
        return canvas
    canvas.paste(paste, offset)
    return canvas


def prepare_image(
    source,
    *,
    max_width_px,
    max_height_px,
    mode="contain",
    background=None,
    preserve_alpha=False,
):
    """Return PNG/JPEG bytes ready for ReportLab, or None."""
    image = _open_image(source)
    if image is None:
        return None
    width = max(1, int(max_width_px))
    height = max(1, int(max_height_px))
    if mode == "cover":
        fitted = _fit_cover(image, width, height)
    elif mode == "thumb":
        fitted = _fit_contain(image, min(width, 480), min(height, 360), background=background)
    else:
        fitted = _fit_contain(image, width, height, background=background)

    buffer = io.BytesIO()
    if preserve_alpha and background is None and _has_alpha(fitted):
        _as_rgba(fitted).save(buffer, format="PNG")
        fmt = "png"
    elif background is not None:
        if fitted.mode == "RGBA":
            base = Image.new("RGB", fitted.size, background)
            base.paste(fitted, mask=fitted.split()[-1])
            fitted = base
        elif fitted.mode != "RGB":
            fitted = fitted.convert("RGB")
        fitted.save(buffer, format="JPEG", quality=90, optimize=True)
        fmt = "jpeg"
    elif _has_alpha(fitted) and preserve_alpha:
        _as_rgba(fitted).save(buffer, format="PNG")
        fmt = "png"
    else:
        if fitted.mode == "RGBA":
            base = Image.new("RGB", fitted.size, (255, 255, 255))
            base.paste(fitted, mask=fitted.split()[-1])
            fitted = base
        elif fitted.mode != "RGB":
            fitted = fitted.convert("RGB")
        fitted.save(buffer, format="JPEG", quality=88, optimize=True)
        fmt = "jpeg"
    buffer.seek(0)
    return {"buffer": buffer, "size": fitted.size, "format": fmt}


def compose_on_color(source, rgb, *, max_width_px, max_height_px):
    """Flatten a (possibly transparent) photo onto an RGB background."""
    return prepare_image(
        source,
        max_width_px=max_width_px,
        max_height_px=max_height_px,
        mode="contain",
        background=rgb,
        preserve_alpha=False,
    )


def flowable_from_prepared(prepared, draw_width, draw_height):
    if not prepared:
        return None
    image = RLImage(prepared["buffer"], mask="auto" if prepared["format"] == "png" else None)
    image.drawWidth = draw_width
    image.drawHeight = draw_height
    image.hAlign = "LEFT"
    return image


def pdf_image_flowable(
    source,
    draw_width,
    draw_height,
    *,
    mode="cover",
    background=None,
    preserve_alpha=False,
    dpi=140,
):
    if source is None:
        return None
    px_w = max(80, int(draw_width * dpi / 72.0))
    px_h = max(80, int(draw_height * dpi / 72.0))
    prepared = prepare_image(
        source,
        max_width_px=px_w,
        max_height_px=px_h,
        mode=mode,
        background=background,
        preserve_alpha=preserve_alpha,
    )
    return flowable_from_prepared(prepared, draw_width, draw_height)
