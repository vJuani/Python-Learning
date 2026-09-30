"""Premium commercial property brochure (ReportLab). Not an ACM report."""

from __future__ import annotations

import io
import re

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

from modules.pdf_images import compose_on_color, pdf_image_flowable, prepare_image


NAVY = colors.HexColor("#0A1633")
SOFT = colors.HexColor("#EEF3FF")
MUTED = colors.HexColor("#5B6B7C")
INK = colors.HexColor("#33415C")
WHITE = colors.white
NAVY_RGB = (10, 22, 51)
FOOTER_H = 52 * mm
WHATSAPP_GREEN = colors.HexColor("#25D366")
INSTAGRAM_ORANGE = colors.HexColor("#F77737")
INSTAGRAM_PINK = colors.HexColor("#E1306C")
INSTAGRAM_PURPLE = colors.HexColor("#833AB4")
_CONTACT_ICON = 3.15 * mm
_CONTACT_ICON_GAP = 1.25 * mm
_CONTACT_ROW = 4.05 * mm


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


class PhotoCollage(Flowable):
    """Editorial cover collage. Adapts from 1 to 5 photos. Never a thumbnail page."""

    def __init__(self, images, width, height=88 * mm):
        super().__init__()
        self.images = [item for item in images if item is not None][:5]
        self.box_width = width
        self.box_height = height

    def wrap(self, avail_width, avail_height):
        self.width = min(self.box_width, avail_width)
        self.height = self.box_height if self.images else 0
        return self.width, self.height

    def _slots(self):
        w, h, g = self.width, self.height, 2.2 * mm
        if len(self.images) == 1:
            return [(0, 0, w, h)]
        if len(self.images) == 2:
            left = w * 0.62
            return [(0, 0, left, h), (left + g, 0, w - left - g, h)]
        if len(self.images) == 3:
            left = w * 0.62
            half = (h - g) / 2
            return [
                (0, 0, left, h),
                (left + g, half + g, w - left - g, half),
                (left + g, 0, w - left - g, half),
            ]
        if len(self.images) == 4:
            left = w * 0.62
            top = h * 0.62
            half = (top - g) / 2
            return [
                (0, h - top, left, top),
                (left + g, h - half, w - left - g, half),
                (left + g, h - top, w - left - g, half),
                (0, 0, w, h - top - g),
            ]
        left = w * 0.64
        top = h * 0.62
        half = (top - g) / 2
        bottom = h - top - g
        mid = w * 0.42
        return [
            (0, h - top, left, top),
            (left + g, h - half, w - left - g, half),
            (left + g, h - top, w - left - g, half),
            (0, 0, mid, bottom),
            (mid + g, 0, w - mid - g, bottom),
        ]

    def draw(self):
        for source, (x, y, w, h) in zip(self.images, self._slots()):
            prepared = prepare_image(
                source,
                max_width_px=max(160, int(w * 2.2)),
                max_height_px=max(120, int(h * 2.2)),
                mode="cover",
                background=(232, 238, 246),
            )
            if not prepared:
                continue
            self.canv.saveState()
            path = self.canv.beginPath()
            path.roundRect(x, y, w, h, 3.2)
            self.canv.clipPath(path, stroke=0, fill=0)
            self.canv.drawImage(
                ImageReader(prepared["buffer"]),
                x,
                y,
                width=w,
                height=h,
                preserveAspectRatio=False,
                mask="auto",
            )
            self.canv.restoreState()


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


def _paint_mail_icon(canvas):
    canvas.setStrokeColor(WHITE)
    canvas.setLineWidth(1.65)
    canvas.setLineJoin(1)
    canvas.setLineCap(1)
    canvas.roundRect(1.2, 4.0, 21.6, 16.0, 2.2, stroke=1, fill=0)
    flap = canvas.beginPath()
    flap.moveTo(2.4, 18.6)
    flap.lineTo(12, 11.4)
    flap.lineTo(21.6, 18.6)
    canvas.drawPath(flap, stroke=1, fill=0)


def _paint_whatsapp_icon(canvas):
    canvas.setFillColor(WHATSAPP_GREEN)
    canvas.circle(12.6, 12.8, 9.5, stroke=0, fill=1)
    tail = canvas.beginPath()
    tail.moveTo(5.2, 7.4)
    tail.lineTo(2.0, 2.4)
    tail.lineTo(8.6, 6.6)
    tail.close()
    canvas.drawPath(tail, stroke=0, fill=1)
    canvas.saveState()
    canvas.translate(12.8, 12.6)
    canvas.rotate(-38)
    canvas.setFillColor(WHITE)
    canvas.roundRect(-7.4, 1.2, 4.6, 6.4, 2.2, stroke=0, fill=1)
    canvas.roundRect(2.6, -7.6, 4.6, 6.4, 2.2, stroke=0, fill=1)
    canvas.setStrokeColor(WHITE)
    canvas.setFillColor(WHITE)
    canvas.setLineWidth(2.2)
    canvas.setLineCap(1)
    canvas.line(-4.8, 4.0, 4.6, -4.2)
    canvas.restoreState()


def _paint_instagram_icon(canvas):
    canvas.saveState()
    clip = canvas.beginPath()
    clip.roundRect(0.8, 0.8, 22.4, 22.4, 6.0)
    canvas.clipPath(clip, stroke=0, fill=0)
    canvas.setFillColor(INSTAGRAM_ORANGE)
    canvas.rect(0, 0, 24, 24, stroke=0, fill=1)
    pink = canvas.beginPath()
    pink.moveTo(0, 24)
    pink.lineTo(24, 24)
    pink.lineTo(24, 0)
    pink.close()
    canvas.setFillColor(INSTAGRAM_PINK)
    canvas.drawPath(pink, stroke=0, fill=1)
    purple = canvas.beginPath()
    purple.moveTo(0, 9)
    purple.lineTo(15, 24)
    purple.lineTo(0, 24)
    purple.close()
    canvas.setFillColor(INSTAGRAM_PURPLE)
    canvas.drawPath(purple, stroke=0, fill=1)
    canvas.restoreState()
    canvas.setStrokeColor(WHITE)
    canvas.setLineWidth(1.7)
    canvas.circle(12, 12, 4.15, stroke=1, fill=0)
    canvas.setFillColor(WHITE)
    canvas.circle(17.15, 17.15, 1.15, stroke=0, fill=1)


_CONTACT_ICON_PAINTERS = {
    "mail": _paint_mail_icon,
    "whatsapp": _paint_whatsapp_icon,
    "instagram": _paint_instagram_icon,
}


def _draw_contact_icon(canvas, kind, x, y, size):
    painter = _CONTACT_ICON_PAINTERS.get(kind)
    if painter is None:
        return
    canvas.saveState()
    canvas.translate(x, y)
    canvas.scale(size / 24.0, size / 24.0)
    painter(canvas)
    canvas.restoreState()


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
        logo = pdf_image_flowable(
            org.get("logo"),
            42 * mm,
            14 * mm,
            mode="contain",
            preserve_alpha=True,
        )
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

    pack = [header]
    if payload.get("eyebrow"):
        pack.append(Paragraph(_escape(payload["eyebrow"]).upper(), styles["BrKicker"]))
    if payload.get("title"):
        pack.append(Paragraph(_escape(payload["title"]), styles["BrTitle"]))
    if payload.get("unit_line"):
        pack.append(Paragraph(_escape(payload["unit_line"]), styles["BrPlace"]))
    if payload.get("location_line"):
        pack.append(Paragraph(_escape(payload["location_line"]), styles["BrPlace"]))
    if gallery:
        pack.append(Spacer(1, 4 * mm))
        pack.append(PhotoCollage(gallery, usable_width))
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

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=FOOTER_H + 4 * mm,
        title=payload.get("title") or "Property",
    )

    def on_page(canvas, _doc):
        _draw_footer(canvas, payload)

    document.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()
