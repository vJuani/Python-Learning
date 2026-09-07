"""
Normalize property document images into a compact private PDF.

Original files are never modified.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


MAX_IMAGE_EDGE = 1600
JPEG_QUALITY = 80
PAGE_MARGIN = 10 * mm


class DocumentNormalizeError(Exception):
    def __init__(self, message_key="property_doc_err_normalize"):
        super().__init__(message_key)
        self.message_key = message_key


def _load_rgb_image(path):
    try:
        image = Image.open(path)
        image.load()
    except (OSError, UnidentifiedImageError) as error:
        raise DocumentNormalizeError("property_doc_err_invalid_image") from error

    if image.mode in ("RGBA", "LA", "P"):
        converted = Image.new("RGB", image.size, (255, 255, 255))
        alpha = image.convert("RGBA")
        converted.paste(alpha, mask=alpha.split()[-1])
        image = converted
    elif image.mode != "RGB":
        image = image.convert("RGB")

    width, height = image.size
    longest = max(width, height)
    if longest > MAX_IMAGE_EDGE:
        scale = MAX_IMAGE_EDGE / float(longest)
        image = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )

    return image


def _image_to_jpeg_bytes(image):
    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=JPEG_QUALITY,
        optimize=True,
    )
    buffer.seek(0)
    return buffer


def normalize_document_to_pdf(image_paths, output_path):
    """
    Build a multi-page PDF from images, preserving order and aspect ratio.
    """
    paths = [Path(path) for path in image_paths if path]
    if not paths:
        raise DocumentNormalizeError("property_doc_err_normalize")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    writer = canvas.Canvas(str(output))
    page_count = 0

    try:
        for path in paths:
            image = _load_rgb_image(path)
            width, height = image.size
            page_size = landscape(A4) if width > height else A4
            page_width, page_height = page_size
            usable_width = page_width - (2 * PAGE_MARGIN)
            usable_height = page_height - (2 * PAGE_MARGIN)
            scale = min(usable_width / width, usable_height / height)
            draw_width = width * scale
            draw_height = height * scale
            x = (page_width - draw_width) / 2
            y = (page_height - draw_height) / 2

            writer.setPageSize(page_size)
            jpeg = ImageReader(_image_to_jpeg_bytes(image))
            writer.drawImage(
                jpeg,
                x,
                y,
                width=draw_width,
                height=draw_height,
                preserveAspectRatio=True,
                mask="auto",
            )
            writer.showPage()
            page_count += 1
            image.close()

        writer.save()
    except Exception:
        if output.exists():
            output.unlink()
        raise

    if page_count <= 0 or not output.is_file() or output.stat().st_size <= 0:
        if output.exists():
            output.unlink()
        raise DocumentNormalizeError("property_doc_err_normalize")

    return {
        "path": output,
        "page_count": page_count,
        "size_bytes": output.stat().st_size,
    }
