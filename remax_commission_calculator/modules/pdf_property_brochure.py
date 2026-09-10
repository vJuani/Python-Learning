"""Premium commercial property brochure (ReportLab). Not an ACM report."""

from __future__ import annotations

import io
import unicodedata

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

from modules.branding import get_brand_name, resolve_brand_logo_path
from modules.pdf_images import compose_on_color, pdf_image_flowable, prepare_image


NAVY = colors.HexColor("#0A1633")
ELECTRIC = colors.HexColor("#0D47FF")
SOFT = colors.HexColor("#EEF3FF")
MUTED = colors.HexColor("#5B6B7C")
INK = colors.HexColor("#33415C")
WHITE = colors.white
NAVY_RGB = (10, 22, 51)
FOOTER_H = 48 * mm


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


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="BrKicker", parent=styles["Normal"], fontName="Helvetica", fontSize=8, textColor=ELECTRIC, leading=10, tracking=0.8))
    styles.add(ParagraphStyle(name="BrMeta", parent=styles["Normal"], fontName="Helvetica", fontSize=7.5, textColor=MUTED, leading=9, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="BrTitle", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=22, textColor=NAVY, leading=25, spaceAfter=1))
    styles.add(ParagraphStyle(name="BrPlace", parent=styles["Normal"], fontName="Helvetica", fontSize=10, textColor=MUTED, leading=13))
    styles.add(ParagraphStyle(name="BrPrice", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=22, textColor=NAVY, leading=26, spaceBefore=3, spaceAfter=4))
    styles.add(ParagraphStyle(name="BrSection", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8, textColor=ELECTRIC, leading=11, spaceBefore=8, spaceAfter=3))
    styles.add(ParagraphStyle(name="BrBody", parent=styles["Normal"], fontName="Helvetica", fontSize=9.4, textColor=INK, leading=13.6, alignment=TA_JUSTIFY, spaceAfter=3))
    styles.add(ParagraphStyle(name="BrFactLabel", parent=styles["Normal"], fontName="Helvetica", fontSize=6.6, textColor=MUTED, leading=8))
    styles.add(ParagraphStyle(name="BrFactValue", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=12, textColor=NAVY, leading=14))
    styles.add(ParagraphStyle(name="BrSheet", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8, textColor=NAVY, leading=10, alignment=TA_RIGHT))
    return styles


def _escape(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
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


def _draw_footer(canvas, payload):
    width, _height = A4
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, width, FOOTER_H, fill=1, stroke=0)
    canvas.setFillColor(ELECTRIC)
    canvas.rect(0, FOOTER_H - 2.1, width, 2.1, fill=1, stroke=0)
    agent = payload.get("agent")
    x = 14 * mm
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
                    7 * mm,
                    width=30 * mm,
                    height=36 * mm,
                    mask="auto",
                    preserveAspectRatio=True,
                    anchor="sw",
                )
                x += 34 * mm
            except Exception:
                pass
        canvas.setFillColor(ELECTRIC)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(x, 39 * mm, _escape(payload.get("advisor_label") or "").upper()[:42])
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawString(x, 32 * mm, _escape(agent.get("name") or "")[:42])
        canvas.setFont("Helvetica", 8)
        canvas.drawString(x, 26 * mm, _escape(agent.get("title") or agent.get("role") or "")[:46])
        y = 20 * mm
        canvas.setFont("Helvetica", 7.5)
        for key in ("phone", "email", "instagram", "linkedin"):
            value = agent.get(key)
            if not value:
                continue
            canvas.drawString(x, y, _escape(value)[:48])
            y -= 7.4
    else:
        org = payload.get("organization") or {}
        mark = org.get("logo") or payload.get("platform_logo") or resolve_brand_logo_path()
        if mark:
            composed = compose_on_color(mark, NAVY_RGB, max_width_px=360, max_height_px=120)
            if composed:
                try:
                    canvas.drawImage(
                        ImageReader(composed["buffer"]),
                        x,
                        28 * mm,
                        width=34 * mm,
                        height=11 * mm,
                        mask="auto",
                        preserveAspectRatio=True,
                        anchor="sw",
                    )
                except Exception:
                    pass
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawString(x, 20 * mm, _escape(org.get("name") or payload.get("platform_name") or get_brand_name())[:40])
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#C5D0DC"))
        canvas.drawString(x, 13 * mm, _escape(payload.get("platform_name") or get_brand_name())[:40])
    canvas.restoreState()


def build_property_brochure_pdf(payload):
    buffer = io.BytesIO()
    styles = _styles()
    usable_width = A4[0] - (24 * mm)
    gallery = list(payload.get("gallery") or [])
    if payload.get("hero_image") and payload["hero_image"] not in gallery:
        gallery = [payload["hero_image"], *gallery]
    gallery = gallery[:5]

    header_logo = pdf_image_flowable(
        payload.get("platform_logo") or resolve_brand_logo_path(),
        34 * mm,
        10 * mm,
        mode="contain",
        preserve_alpha=True,
    )
    meta_bits = [payload.get("generated_on")]
    if payload.get("mls"):
        meta_bits.append(f"MLS #{payload['mls']}")
    right = [
        Paragraph(_escape(payload.get("sheet_label") or "FICHA DE PROPIEDAD"), styles["BrSheet"]),
        Paragraph(_escape("  ·  ".join(bit for bit in meta_bits if bit)), styles["BrMeta"]),
    ]
    header = Table(
        [[header_logo or "", right]],
        colWidths=[usable_width * 0.55, usable_width * 0.45],
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
