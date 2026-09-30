"""Premium commercial property brochure (ReportLab). Not an ACM report."""

from __future__ import annotations

import io
import re
from pathlib import Path
from xml.etree import ElementTree

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import _listWrapOn

from modules.pdf_images import _open_image, compose_on_color, uncropped_raster
from modules.pdf_svg import draw_svg


NAVY = colors.HexColor("#0A1633")
SOFT = colors.HexColor("#EEF3FF")
MUTED = colors.HexColor("#5B6B7C")
INK = colors.HexColor("#33415C")
WHITE = colors.white
NAVY_RGB = (10, 22, 51)
FOOTER_H = 52 * mm
WHATSAPP_GREEN = colors.HexColor("#25D366")
_CONTACT_ICON = 3.15 * mm
_CONTACT_ICON_GAP = 1.25 * mm
_CONTACT_ROW = 4.05 * mm
_ICON_DIR = Path(__file__).resolve().parent.parent / "static" / "assets" / "icons"
_CONTACT_ICON_FILES = {
    "mail": _ICON_DIR / "email.svg",
    "whatsapp": _ICON_DIR / "whatsapp.svg",
    "instagram": _ICON_DIR / "instagram.svg",
}
_LOGO_MAX_W = 42 * mm
_LOGO_MAX_H = 14 * mm
_LOGO_MIN_DPI = 144


class ChipRow(Flowable):
    def __init__(self, labels, width):
        super().__init__()
        self.labels = [str(label) for label in labels if label]
        self.box_width = width
        self.height = 0
        self._rows = []

    def wrap(self, avail_width, avail_height):
        width = min(self.box_width, avail_width)
        x = 0
        row = []
        rows = []
        for label in self.labels:
            chip_width = min(width, 10 + (len(label) * 5.0))
            if row and x + chip_width > width:
                rows.append(row)
                row = []
                x = 0
            row.append((x, label, chip_width))
            x += chip_width + 5
        if row:
            rows.append(row)
        self._rows = rows
        self.width = width
        self.height = max(18, len(rows) * 20)
        return width, self.height

    def draw(self):
        self.canv.setFont("Helvetica", 8)
        for row_index, row in enumerate(self._rows):
            y = self.height - ((row_index + 1) * 20) + 4
            for x, label, chip_width in row:
                self.canv.setFillColor(SOFT)
                self.canv.roundRect(x, y, chip_width, 15, 7, fill=1, stroke=0)
                self.canv.setFillColor(NAVY)
                self.canv.drawString(x + 5, y + 4, label)


_COLLAGE_GAP = 8
_PAGE_TOP = 12 * mm
_PAGE_BOTTOM = FOOTER_H + 4 * mm
_FRAME_PAD = 6


def brochure_content_height():
    """Inner frame height: page, margins, footer, and ReportLab frame padding."""
    return A4[1] - _PAGE_TOP - _PAGE_BOTTOM - (2 * _FRAME_PAD)


def collage_height_limit(content_height=None):
    """35–40% of the usable page. The collage never grows past this."""
    height = brochure_content_height() if content_height is None else content_height
    return height * 0.38


def _story_height(story, width):
    """Height ReportLab will actually reserve, including paragraph spacing."""
    flat = []
    for item in story:
        content = getattr(item, "_content", None)
        if isinstance(item, KeepTogether) and content is not None:
            flat.extend(content)
        else:
            flat.append(item)
    _width, height = _listWrapOn(flat, width, None)
    return height


def _hero_stack(width, hero_ratio, top_ratio, bottom_ratio, gap):
    """Left photo plus two stacked photos. Every cell matches its image."""
    denom = 1 + hero_ratio * (1 / top_ratio + 1 / bottom_ratio)
    numer = width - gap * (1 + hero_ratio)
    if denom <= 0 or numer <= 0:
        return None
    side_w = numer / denom
    top_h = side_w / top_ratio
    bottom_h = side_w / bottom_ratio
    hero_h = top_h + gap + bottom_h
    hero_w = hero_ratio * hero_h
    if min(hero_w, hero_h, side_w, top_h, bottom_h) < 36:
        return None
    return hero_w, hero_h, side_w, top_h, bottom_h


def _row_cells(indices, ratio, y, width, row_h, gap):
    """One full row. Photos keep their ratio and share the row height."""
    if row_h <= 0 or len(indices) < 2:
        return None
    ratio_sum = sum(ratio[index] for index in indices)
    full_h = (width - gap) / ratio_sum
    row_h = min(row_h, full_h)
    widths = [row_h * ratio[index] for index in indices]
    used = sum(widths) + gap
    if used > width + 0.5 or min(min(widths), row_h) < 36:
        return None
    x = max(0.0, (width - used) / 2.0)
    placed = {}
    for index, cell_w in zip(indices, widths):
        placed[index] = (x, y, cell_w, row_h)
        x += cell_w + gap
    return placed, row_h


def _four_grid(count, width, ratio, gap, max_height):
    """Two full-width rows, gallery order. Top about 60%, bottom about 40%."""
    ids = [index for index in range(count) if index in ratio]
    if len(ids) != 4:
        return None
    top_ids = ids[:2]
    bottom_ids = ids[2:]

    def natural(row):
        return (width - gap) / sum(ratio[index] for index in row)

    top_h = natural(top_ids)
    bottom_h = natural(bottom_ids)
    photo_h = top_h + bottom_h
    if photo_h > 0 and top_h / photo_h > 0.62:
        top_h = bottom_h * 0.60 / 0.40
    top = _row_cells(top_ids, ratio, 0.0, width, top_h, gap)
    if top is None:
        return None
    top_placed, top_h = top
    bottom = _row_cells(bottom_ids, ratio, top_h + gap, width, bottom_h, gap)
    if bottom is None:
        return None
    bottom_placed, bottom_h = bottom
    placed = {**top_placed, **bottom_placed}
    height = top_h + gap + bottom_h
    if height > max_height > 0:
        scale = max_height / height
        placed = {
            index: (x * scale, y * scale, w * scale, h * scale)
            for index, (x, y, w, h) in placed.items()
        }
        used = max(x + w for x, _y, w, _h in placed.values())
        pad = max(0.0, (width - used) / 2.0)
        if pad:
            placed = {
                index: (x + pad, y, w, h)
                for index, (x, y, w, h) in placed.items()
            }
        height = max_height
    frames = [placed.get(index) for index in range(count)]
    return frames, height


def collage_frames(sizes, width, *, gap=_COLLAGE_GAP, max_height=None):
    """Cells follow each photo. Nothing is cropped or stretched.

    Four photos sit in two full-width rows, in gallery order. y grows downward.
    """
    usable = [
        (index, float(src_w), float(src_h))
        for index, (src_w, src_h) in enumerate(sizes or [])
        if src_w and src_h
    ]
    if max_height is None:
        max_height = collage_height_limit()
    if not usable or width <= gap:
        return [None] * len(sizes or []), 0.0
    ratio = {index: src_w / src_h for index, src_w, src_h in usable}

    def finish(placed, height):
        if height > max_height > 0:
            scale = max_height / height
            placed = {
                index: (x * scale, y * scale, w * scale, h * scale)
                for index, (x, y, w, h) in placed.items()
            }
            used = max(x + w for x, _y, w, _h in placed.values())
            pad = max(0.0, (width - used) / 2.0)
            if pad:
                placed = {
                    index: (x + pad, y, w, h)
                    for index, (x, y, w, h) in placed.items()
                }
            height = max_height
        frames = [placed.get(index) for index in range(len(sizes))]
        return frames, height

    def stacked():
        placed = {}
        y = 0.0
        for index, src_w, src_h in usable:
            row_h = width / (src_w / src_h)
            placed[index] = (0.0, y, width, row_h)
            y += row_h + gap
        return finish(placed, y - gap if placed else 0.0)

    if len(usable) == 1:
        index, src_w, src_h = usable[0]
        row_h = min(width / ratio[index], max_height)
        return finish({index: (0.0, 0.0, row_h * ratio[index], row_h)}, row_h)

    if len(usable) == 2:
        (first, _, _), (second, _, _) = usable
        row_h = (width - gap) / (ratio[first] + ratio[second])
        first_w = row_h * ratio[first]
        second_w = row_h * ratio[second]
        return finish(
            {
                first: (0.0, 0.0, first_w, row_h),
                second: (first_w + gap, 0.0, second_w, row_h),
            },
            row_h,
        )

    if len(usable) == 4:
        grid = _four_grid(len(sizes), width, ratio, gap, max_height)
        if grid is not None:
            return grid

    widest = sorted(usable, key=lambda item: (-ratio[item[0]], item[0]))
    hero = widest[0][0]
    rest = [item[0] for item in usable if item[0] != hero]
    if len(usable) == 4:
        bottom_ids = [max(rest, key=lambda index: (ratio[index], -index))]
        side_ids = [index for index in rest if index not in bottom_ids]
    elif len(usable) >= 5:
        side_ids = sorted(rest, key=lambda index: (ratio[index], index))[:2]
        bottom_ids = [index for index in rest if index not in side_ids]
    else:
        side_ids = list(rest)
        bottom_ids = []
    side_ids = sorted(side_ids)
    bottom_ids = sorted(bottom_ids)
    if len(side_ids) != 2:
        return stacked()
    stack = _hero_stack(width, ratio[hero], ratio[side_ids[0]], ratio[side_ids[1]], gap)
    if stack is None:
        return stacked()
    hero_w, hero_h, side_w, top_h, bottom_h = stack
    placed = {
        hero: (0.0, 0.0, hero_w, hero_h),
        side_ids[0]: (hero_w + gap, 0.0, side_w, top_h),
        side_ids[1]: (hero_w + gap, top_h + gap, side_w, bottom_h),
    }
    y = hero_h + gap
    if len(bottom_ids) == 1:
        row_h = width / ratio[bottom_ids[0]]
        placed[bottom_ids[0]] = (0.0, y, width, row_h)
        y += row_h
    elif len(bottom_ids) >= 2:
        pair = bottom_ids[:2]
        row_h = (width - gap) / (ratio[pair[0]] + ratio[pair[1]])
        left_w = row_h * ratio[pair[0]]
        placed[pair[0]] = (0.0, y, left_w, row_h)
        placed[pair[1]] = (left_w + gap, y, row_h * ratio[pair[1]], row_h)
        y += row_h
    else:
        y -= gap
    return finish(placed, y)


class PhotoCollage(Flowable):
    """Up to five photos. Each cell follows that photo. Nothing is cropped."""

    def __init__(self, images, width, max_height=None):
        super().__init__()
        self.images = [item for item in images if item is not None][:5]
        self.box_width = width
        self.max_height = collage_height_limit() if max_height is None else max_height
        self._sources = []
        self._frames = []

    def wrap(self, avail_width, avail_height):
        self.width = min(self.box_width, avail_width)
        sources = []
        sizes = []
        for source in self.images:
            image = _open_image(source)
            if image is None:
                continue
            src_w, src_h = image.size
            if src_w <= 0 or src_h <= 0:
                continue
            sources.append(source)
            sizes.append((src_w, src_h))
        self._sources = sources
        self._frames, self.height = collage_frames(
            sizes, self.width, max_height=self.max_height,
        )
        return self.width, self.height

    def draw(self):
        if not self._frames:
            self.wrap(self.box_width, 0)
        for source, frame in zip(self._sources, self._frames):
            if not frame:
                continue
            x, y_top, w, h = frame
            prepared = uncropped_raster(source, max(1, int(w * 3)), max(1, int(h * 3)))
            if not prepared:
                continue
            src_w, src_h = prepared["source_size"]
            offset_x, offset_y, draw_w, draw_h = contained_draw_box(src_w, src_h, w, h)
            self.canv.drawImage(
                ImageReader(prepared["buffer"]),
                x + offset_x,
                self.height - y_top - h + offset_y,
                width=draw_w,
                height=draw_h,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )


def contained_draw_box(src_w, src_h, cell_w, cell_h):
    """object-fit: contain. The whole image stays inside the cell."""
    if src_w <= 0 or src_h <= 0 or cell_w <= 0 or cell_h <= 0:
        return 0.0, 0.0, 0.0, 0.0
    scale = min(cell_w / float(src_w), cell_h / float(src_h))
    draw_w = float(src_w) * scale
    draw_h = float(src_h) * scale
    return (cell_w - draw_w) / 2.0, (cell_h - draw_h) / 2.0, draw_w, draw_h


def logo_draw_size(src_w, src_h, max_w, max_h, *, min_dpi=_LOGO_MIN_DPI):
    """Fit inside the max box. A raster is never drawn larger than its own pixels."""
    if src_w <= 0 or src_h <= 0 or max_w <= 0 or max_h <= 0:
        return 0.0, 0.0
    cap_w = float(src_w) * 72.0 / float(min_dpi)
    cap_h = float(src_h) * 72.0 / float(min_dpi)
    scale = min(max_w / cap_w, max_h / cap_h, 1.0)
    return cap_w * scale, cap_h * scale


class _SvgLogo(Flowable):
    def __init__(self, svg_text, width, height):
        super().__init__()
        self.svg_text = svg_text
        self.drawWidth = width
        self.drawHeight = height

    def wrap(self, avail_width, avail_height):
        return self.drawWidth, self.drawHeight

    def draw(self):
        draw_svg(
            self.canv,
            self.svg_text,
            0,
            0,
            self.drawWidth,
            self.drawHeight,
            current_color=NAVY,
        )


def _logo_flowable(source):
    """Original logo file, contained. Never stretched and never upscaled."""
    if isinstance(source, (bytes, bytearray)):
        path = None
        payload = bytes(source)
    else:
        path = Path(str(source))
        if not path.is_file():
            return None
        payload = None
    if path is not None and path.suffix.lower() == ".svg":
        svg_text = path.read_text(encoding="utf-8")
        root = ElementTree.fromstring(svg_text)
        raw = (root.attrib.get("viewBox") or "").replace(",", " ").split()
        if len(raw) == 4:
            view_w, view_h = float(raw[2]), float(raw[3])
        else:
            view_w = float(root.attrib.get("width") or 0)
            view_h = float(root.attrib.get("height") or 0)
        if view_w <= 0 or view_h <= 0:
            return None
        scale = min(_LOGO_MAX_W / view_w, _LOGO_MAX_H / view_h)
        return _SvgLogo(svg_text, view_w * scale, view_h * scale)

    from PIL import Image
    from modules.pdf_images import _open_image

    image = _open_image(payload if payload is not None else path)
    if image is None:
        return None
    src_w, src_h = image.size
    draw_w, draw_h = logo_draw_size(src_w, src_h, _LOGO_MAX_W, _LOGO_MAX_H)
    if draw_w <= 0 or draw_h <= 0:
        return None
    pixel_scale = min(1.0, (draw_w * _LOGO_MIN_DPI / 72.0) / src_w)
    out_w = max(1, int(round(src_w * pixel_scale)))
    out_h = max(1, int(round(src_h * pixel_scale)))
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA")
    resized = image.resize((out_w, out_h), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    resized.save(buffer, format="PNG")
    buffer.seek(0)
    from reportlab.platypus import Image as RLImage

    flowable = RLImage(buffer, mask="auto")
    flowable.drawWidth = draw_w
    flowable.drawHeight = draw_h
    flowable.hAlign = "LEFT"
    return flowable


def _org_accent(payload):
    raw = str((payload.get("organization") or {}).get("accent_color") or "").strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", raw):
        return NAVY
    red = int(raw[1:3], 16)
    green = int(raw[3:5], 16)
    blue = int(raw[5:7], 16)
    if (0.299 * red + 0.587 * green + 0.114 * blue) / 255 > 0.75:
        return NAVY
    return colors.HexColor(raw)


def _styles(accent):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="BrKicker", parent=styles["Normal"], fontName="Helvetica", fontSize=8, textColor=accent, leading=10, tracking=0.8))
    styles.add(ParagraphStyle(name="BrMeta", parent=styles["Normal"], fontName="Helvetica", fontSize=7.5, textColor=MUTED, leading=9, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="BrBrand", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=11, textColor=NAVY, leading=13, spaceBefore=1))
    styles.add(ParagraphStyle(name="BrTitle", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=22, textColor=NAVY, leading=25, spaceAfter=1))
    styles.add(ParagraphStyle(name="BrPlace", parent=styles["Normal"], fontName="Helvetica", fontSize=10, textColor=MUTED, leading=13))
    styles.add(ParagraphStyle(name="BrPrice", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=22, textColor=NAVY, leading=26, spaceBefore=3, spaceAfter=4))
    styles.add(ParagraphStyle(name="BrSection", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8, textColor=accent, leading=11, spaceBefore=8, spaceAfter=3))
    styles.add(ParagraphStyle(name="BrBody", parent=styles["Normal"], fontName="Helvetica", fontSize=9.4, textColor=INK, leading=13.6, alignment=TA_JUSTIFY, spaceAfter=3))
    styles.add(ParagraphStyle(name="BrFactLabel", parent=styles["Normal"], fontName="Helvetica", fontSize=6.6, textColor=MUTED, leading=8))
    styles.add(ParagraphStyle(name="BrFactValue", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=12, textColor=NAVY, leading=14))
    styles.add(ParagraphStyle(name="BrSheet", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8, textColor=NAVY, leading=10, alignment=TA_RIGHT))
    return styles


def _escape(value):
    text = str(value or "")
    text = text.encode("latin-1", "replace").decode("latin-1")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _split_description(text):
    raw = str(text or "").replace("\r\n", "\n").strip()
    if not raw:
        return []
    blocks = [part.strip() for part in raw.split("\n\n") if part.strip()]
    if len(blocks) == 1:
        blocks = [part.strip() for part in raw.split("\n") if part.strip()]
    return blocks


def _stored_text(value):
    text = " ".join(str(value or "").split())
    return text or None


def _agent_contact_rows(agent):
    """One row per stored channel. A missing value drops the icon and the line."""
    agent = agent or {}
    email = _stored_text(agent.get("email"))
    phone = _stored_text(agent.get("phone"))
    whatsapp = _stored_text(agent.get("whatsapp"))
    number = whatsapp or phone
    instagram = _stored_text(agent.get("instagram"))
    if instagram and not instagram.startswith("@"):
        instagram = f"@{instagram}"
    rows = []
    if email:
        rows.append(("mail", email))
    if number:
        rows.append(("whatsapp", number))
    if instagram:
        rows.append(("instagram", instagram))
    return rows


def _draw_contact_icon(canvas, kind, x, y, size):
    asset = _CONTACT_ICON_FILES.get(kind)
    if asset is None or not asset.is_file():
        return
    draw_svg(
        canvas,
        asset.read_text(encoding="utf-8"),
        x,
        y,
        size,
        size,
        current_color=WHITE,
    )


def _wrap_words(canvas, text, font_name, font_size, max_width):
    """Wrap on spaces. A word that does not fit is scaled, never cut."""
    words = [word for word in _escape(text).split() if word]
    if not words or max_width <= 0:
        return []
    lines = []
    current = []
    for word in words:
        trial = " ".join(current + [word])
        if current and canvas.stringWidth(trial, font_name, font_size) > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    fitted = []
    for line in lines:
        size = float(font_size)
        while size > 5 and canvas.stringWidth(line, font_name, size) > max_width:
            size -= 0.25
        fitted.append((line, size))
    return fitted


def _draw_wrapped(canvas, text, x, y, font_name, font_size, max_width, color, leading):
    for line, size in _wrap_words(canvas, text, font_name, font_size, max_width):
        canvas.setFillColor(color)
        canvas.setFont(font_name, size)
        canvas.drawString(x, y, line)
        y -= leading
    return y


def _draw_legal(canvas, payload, x, top, max_width):
    org = payload.get("organization") or {}
    broker = org.get("legal_broker_name")
    license_no = org.get("legal_broker_license")
    footer_line = org.get("legal_footer_line")
    if not any((broker, license_no, footer_line)):
        return
    y = top
    label_color = colors.HexColor("#D5DEE8")
    if broker:
        y = _draw_wrapped(
            canvas,
            (payload.get("broker_label") or "Martillero").upper(),
            x, y, "Helvetica", 6.5, max_width, label_color, 3.4 * mm,
        )
        y -= 0.8 * mm
        y = _draw_wrapped(
            canvas, broker, x, y, "Helvetica-Bold", 9, max_width, WHITE, 4.2 * mm,
        )
        y -= 0.8 * mm
    if license_no:
        y = _draw_wrapped(
            canvas,
            (payload.get("license_label") or "Matricula").upper(),
            x, y, "Helvetica", 6.5, max_width, label_color, 3.4 * mm,
        )
        y -= 0.8 * mm
        y = _draw_wrapped(
            canvas, license_no, x, y, "Helvetica-Bold", 9, max_width, WHITE, 4.2 * mm,
        )
        y -= 0.8 * mm
    if footer_line:
        _draw_wrapped(
            canvas,
            footer_line,
            x, y, "Helvetica", 6.5, max_width, colors.HexColor("#C5D0DC"), 3.4 * mm,
        )


def _draw_fitted(canvas, text, x, y, font_name, font_size, max_width):
    fitted = _wrap_words(canvas, text, font_name, font_size, max_width)
    if not fitted:
        return y
    line, size = fitted[0]
    canvas.setFont(font_name, size)
    canvas.drawString(x, y, line)
    return y - (3.6 * mm)


def _draw_agent_contacts(canvas, x, y, max_width, rows):
    font_name = "Helvetica"
    font_size = 7.5
    text_x = x + _CONTACT_ICON + _CONTACT_ICON_GAP
    text_width = max(8, max_width - _CONTACT_ICON - _CONTACT_ICON_GAP)
    for kind, label in rows:
        mid = y + (font_size * 0.36)
        _draw_contact_icon(canvas, kind, x, mid - (_CONTACT_ICON / 2), _CONTACT_ICON)
        canvas.setFillColor(WHITE)
        _draw_fitted(canvas, label, text_x, y, font_name, font_size, text_width)
        y -= _CONTACT_ROW
    return y


def _draw_footer(canvas, payload):
    width, _height = A4
    accent = _org_accent(payload)
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, width, FOOTER_H, fill=1, stroke=0)
    canvas.setFillColor(accent)
    canvas.rect(0, FOOTER_H - 1.8, width, 1.8, fill=1, stroke=0)
    agent = payload.get("agent")
    x = 12 * mm
    if agent:
        photo = None
        if agent.get("photo_path"):
            photo = compose_on_color(
                agent["photo_path"],
                NAVY_RGB,
                max_width_px=420,
                max_height_px=520,
            )
        if photo:
            try:
                canvas.drawImage(
                    ImageReader(photo["buffer"]),
                    x,
                    12 * mm,
                    width=26 * mm,
                    height=32 * mm,
                    mask="auto",
                    preserveAspectRatio=True,
                    anchor="sw",
                )
                x += 30 * mm
            except Exception:
                pass
        canvas.setFillColor(colors.HexColor("#D5DEE8"))
        canvas.setFont("Helvetica", 7)
        canvas.drawString(x, 44 * mm, _escape(payload.get("advisor_label") or "").upper()[:42])
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(x, 38 * mm, _escape(agent.get("name") or "")[:36])
        role = agent.get("title") or agent.get("role")
        y = 32.5 * mm
        if role:
            canvas.setFont("Helvetica", 7.5)
            canvas.setFillColor(colors.HexColor("#D5DEE8"))
            canvas.drawString(x, y, _escape(role)[:40])
            y = 27.5 * mm
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica", 7.5)
        contact_width = (118 * mm) - x - (4 * mm)
        y = _draw_agent_contacts(
            canvas, x, y, contact_width, _agent_contact_rows(agent),
        )
        legal_x = 118 * mm
        _draw_legal(canvas, payload, legal_x, 44 * mm, width - legal_x - (12 * mm))
    else:
        _draw_legal(canvas, payload, x, 40 * mm, width - x - (12 * mm))
    powered = payload.get("powered_by")
    if powered:
        canvas.setFillColor(colors.HexColor("#8E9AAB"))
        canvas.setFont("Helvetica", 6.5)
        canvas.drawRightString(width - 12 * mm, 4.2 * mm, _escape(powered)[:48])
    canvas.restoreState()


def build_property_brochure_pdf(payload):
    buffer = io.BytesIO()
    accent = _org_accent(payload)
    styles = _styles(accent)
    usable_width = A4[0] - (24 * mm)
    gallery = list(payload.get("gallery") or [])
    if payload.get("hero_image") and payload["hero_image"] not in gallery:
        gallery = [payload["hero_image"], *gallery]
    gallery = gallery[:5]

    org = payload.get("organization") or {}
    left = []
    if org.get("logo"):
        logo = _logo_flowable(org.get("logo"))
        if logo:
            left.append(logo)
    if org.get("name"):
        left.append(Paragraph(_escape(org["name"]), styles["BrBrand"]))
    meta_bits = [payload.get("generated_on")]
    if payload.get("mls"):
        meta_bits.append(f"MLS #{payload['mls']}")
    right = [
        Paragraph(_escape(payload.get("sheet_label") or "FICHA DE PROPIEDAD"), styles["BrSheet"]),
        Paragraph(_escape("  ·  ".join(bit for bit in meta_bits if bit)), styles["BrMeta"]),
    ]
    header = Table(
        [[left or "", right]],
        colWidths=[usable_width * 0.62, usable_width * 0.38],
    )
    header.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])
    )

    frame_height = brochure_content_height()
    pack = [header]
    if payload.get("eyebrow"):
        pack.append(Paragraph(_escape(payload["eyebrow"]).upper(), styles["BrKicker"]))
    if payload.get("title"):
        pack.append(Paragraph(_escape(payload["title"]), styles["BrTitle"]))
    if payload.get("unit_line"):
        pack.append(Paragraph(_escape(payload["unit_line"]), styles["BrPlace"]))
    if payload.get("location_line"):
        pack.append(Paragraph(_escape(payload["location_line"]), styles["BrPlace"]))
    collage = None
    if gallery:
        photo_limit = collage_height_limit(frame_height)
        if len(gallery) == 4:
            photo_limit = frame_height * 0.50
        collage = PhotoCollage(gallery, usable_width, max_height=photo_limit)
        pack.append(Spacer(1, 4 * mm))
        pack.append(collage)
    if payload.get("price"):
        pack.append(Paragraph(_escape(payload["price"]), styles["BrPrice"]))
    chips = payload.get("chips") or []
    if chips:
        pack.append(ChipRow(chips, usable_width))
    highlights = payload.get("highlights") or []
    if highlights:
        cells = []
        for label, value in highlights:
            cells.append([
                Paragraph(_escape(label), styles["BrFactLabel"]),
                Paragraph(_escape(value), styles["BrFactValue"]),
            ])
        col = usable_width / max(len(cells), 1)
        facts = Table([cells], colWidths=[col] * len(cells))
        facts.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ])
        )
        pack.append(Spacer(1, 3 * mm))
        pack.append(facts)
    story = [KeepTogether(pack), Spacer(1, 3 * mm)]

    description = _split_description(payload.get("description"))
    if description:
        body = [Paragraph(_escape(payload.get("about_label") or ""), styles["BrSection"])]
        for block in description:
            body.append(Paragraph(_escape(block), styles["BrBody"]))
        story.append(KeepTogether(body) if len(body) <= 3 else KeepTogether(body[:2]))
        if len(body) > 3:
            story.extend(body[2:])

    extras = payload.get("features") or payload.get("extra_features") or []
    if extras:
        story.append(KeepTogether([
            Paragraph(_escape(payload.get("features_label") or ""), styles["BrSection"]),
            ChipRow(extras, usable_width),
        ]))

    if payload.get("location_line") or payload.get("full_address"):
        loc = [Paragraph(_escape(payload.get("location_label") or ""), styles["BrSection"])]
        if payload.get("title"):
            loc.append(Paragraph(_escape(payload["title"]), styles["BrFactValue"]))
        if payload.get("location_line"):
            loc.append(Paragraph(_escape(payload["location_line"]), styles["BrPlace"]))
        story.append(KeepTogether(loc))

    if collage is not None:
        frame_width = A4[0] - (24 * mm) - (2 * _FRAME_PAD)
        budget = frame_height - 4
        for _ in range(4):
            used = _story_height(story, frame_width)
            overflow = used - budget
            if overflow <= 0:
                break
            collage.max_height = max(48.0, collage.height - overflow)

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=_PAGE_TOP,
        bottomMargin=_PAGE_BOTTOM,
        title=payload.get("title") or "Property",
    )

    def on_page(canvas, _doc):
        _draw_footer(canvas, payload)

    document.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()
