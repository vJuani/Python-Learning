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


def _contact_lines(agent):
    lines = []
    phone = agent.get("phone")
    if phone:
        lines.append(str(phone))
    whatsapp = agent.get("whatsapp")
    if whatsapp and str(whatsapp) != str(phone or ""):
        lines.append(str(whatsapp))
    if agent.get("email"):
        lines.append(str(agent["email"]))
    instagram = agent.get("instagram")
    if instagram:
        handle = str(instagram)
        if not handle.startswith("@"):
            handle = f"@{handle}"
        lines.append(handle)
    return lines


def _wrap_footer_text(value, width=42):
    text = _escape(value)
    if not text:
        return []
    chunks = []
    while text:
        chunks.append(text[:width])
        text = text[width:]
        if len(chunks) == 2:
            break
    return chunks


def _draw_legal(canvas, payload, x, top):
    org = payload.get("organization") or {}
    broker = org.get("legal_broker_name")
    license_no = org.get("legal_broker_license")
    footer_line = org.get("legal_footer_line")
    if not any((broker, license_no, footer_line)):
        return
    y = top
    canvas.setFillColor(colors.HexColor("#D5DEE8"))
    if broker:
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(x, y, _escape(payload.get("broker_label") or "Martillero").upper()[:24])
        y -= 4.2 * mm
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(x, y, _escape(broker)[:36])
        y -= 5 * mm
        canvas.setFillColor(colors.HexColor("#D5DEE8"))
    if license_no:
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(x, y, _escape(payload.get("license_label") or "Matricula").upper()[:24])
        y -= 4.2 * mm
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(x, y, _escape(license_no)[:36])
        y -= 5 * mm
    if footer_line:
        canvas.setFillColor(colors.HexColor("#C5D0DC"))
        canvas.setFont("Helvetica", 6.5)
        for chunk in _wrap_footer_text(footer_line, 46):
            canvas.drawString(x, y, chunk)
            y -= 3.4 * mm


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
        for line in _contact_lines(agent)[:4]:
            canvas.drawString(x, y, _escape(line)[:42])
            y -= 3.6 * mm
        _draw_legal(canvas, payload, 118 * mm, 44 * mm)
    else:
        _draw_legal(canvas, payload, x, 40 * mm)
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
