"""
Commercial property brochure layout (ReportLab).
"""

from __future__ import annotations

import io
import unicodedata
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import Image as RLImage
from PIL import Image as PILImage


NAVY = colors.HexColor("#001838")
ACCENT = colors.HexColor("#0860c8")
SOFT = colors.HexColor("#eef3f9")
MUTED = colors.HexColor("#5b6b7c")
LINE = colors.HexColor("#c9d6e5")
WHITE = colors.white


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
        row_height = 16
        for label in self.labels:
            chip_width = min(width, 8 + (len(label) * 5.1))
            if row and x + chip_width > width:
                rows.append(row)
                row = []
                x = 0
            row.append((x, label, chip_width))
            x += chip_width + 6
        if row:
            rows.append(row)
        self._rows = rows
        self.width = width
        self.height = max(18, len(rows) * (row_height + 6))
        return width, self.height

    def draw(self):
        self.canv.setFont("Helvetica", 8)
        for row_index, row in enumerate(self._rows):
            y = self.height - ((row_index + 1) * 20) + 4
            for x, label, chip_width in row:
                self.canv.setFillColor(SOFT)
                self.canv.roundRect(x, y, chip_width, 16, 8, fill=1, stroke=0)
                self.canv.setFillColor(NAVY)
                self.canv.drawString(x + 6, y + 5, label)


def _styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="BrochureKicker",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=MUTED,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BrochureAddress",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=18,
            textColor=NAVY,
            leading=22,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BrochureZone",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=10,
            textColor=MUTED,
            leading=13,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BrochurePrice",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=20,
            textColor=ACCENT,
            leading=24,
            spaceBefore=4,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BrochureBody",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            textColor=NAVY,
            leading=13,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BrochureSection",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=NAVY,
            spaceBefore=10,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BrochureAgentName",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=12,
            textColor=NAVY,
            leading=15,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BrochureFooter",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=MUTED,
            alignment=TA_CENTER,
        )
    )
    return styles


def _escape(value):
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.encode("latin-1", "replace").decode("latin-1")
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _compress_image_bytes(path_or_bytes, max_edge=720):
    if isinstance(path_or_bytes, (bytes, bytearray)):
        source = io.BytesIO(path_or_bytes)
    else:
        path = Path(path_or_bytes)
        if not path.is_file():
            return None
        source = path

    image = PILImage.open(source)
    image.load()
    if image.mode in ("P", "LA"):
        image = image.convert("RGBA")
    if image.mode == "RGBA":
        background = PILImage.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")

    width, height = image.size
    longest = max(width, height)
    if longest > max_edge:
        scale = max_edge / float(longest)
        image = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            PILImage.Resampling.LANCZOS,
        )

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=80, optimize=True)
    buffer.seek(0)
    return buffer, image.size


def _image_flowable(path_or_bytes, max_width, max_height):
    if path_or_bytes is None:
        return None
    try:
        compressed = _compress_image_bytes(path_or_bytes)
        if compressed is None:
            return None
        buffer, (width, height) = compressed
        if width <= 0 or height <= 0:
            return None
        image = RLImage(buffer)
        image.hAlign = "CENTER"
        scale = min(max_width / width, max_height / height, 1.0)
        image.drawWidth = width * scale
        image.drawHeight = height * scale
        return image
    except Exception:
        return None


def build_property_brochure_pdf(payload):
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=payload.get("title") or "Property",
    )
    styles = _styles()
    story = []
    usable_width = A4[0] - (32 * mm)

    logo = _image_flowable(payload.get("logo_path"), 42 * mm, 14 * mm)
    brand_name = payload.get("brand_name")
    header_cells = []
    if logo is not None:
        header_cells.append(logo)
    if brand_name:
        header_cells.append(Paragraph(_escape(brand_name), styles["BrochureKicker"]))
    if header_cells:
        table = Table(
            [header_cells],
            colWidths=[usable_width / len(header_cells)] * len(header_cells),
        )
        table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ])
        )
        story.append(table)

    hero = _image_flowable(payload.get("hero_image"), usable_width, 88 * mm)
    if hero is not None:
        story.append(hero)
        story.append(Spacer(1, 6 * mm))

    if payload.get("code"):
        story.append(
            Paragraph(_escape(payload["code"]), styles["BrochureKicker"])
        )
    if payload.get("address"):
        story.append(
            Paragraph(_escape(payload["address"]), styles["BrochureAddress"])
        )
    if payload.get("zone"):
        story.append(Paragraph(_escape(payload["zone"]), styles["BrochureZone"]))
    if payload.get("price"):
        story.append(Paragraph(_escape(payload["price"]), styles["BrochurePrice"]))

    chips = payload.get("chips") or []
    if chips:
        story.append(ChipRow(chips, usable_width))
        story.append(Spacer(1, 3 * mm))

    facts = payload.get("facts") or []
    if facts:
        story.append(
            Paragraph(
                _escape(payload.get("facts_title") or ""),
                styles["BrochureSection"],
            )
        )
        story.append(Paragraph(_escape(" · ".join(facts)), styles["BrochureBody"]))

    if payload.get("description"):
        story.append(
            Paragraph(
                _escape(payload.get("description_title") or ""),
                styles["BrochureSection"],
            )
        )
        story.append(
            Paragraph(_escape(payload["description"]), styles["BrochureBody"])
        )

    gallery = [
        _image_flowable(item, (usable_width - 6 * mm) / 2, 42 * mm)
        for item in (payload.get("gallery") or [])
    ]
    gallery = [item for item in gallery if item is not None]
    if gallery:
        story.append(
            Paragraph(
                _escape(payload.get("gallery_title") or ""),
                styles["BrochureSection"],
            )
        )
        rows = []
        for index in range(0, len(gallery), 2):
            pair = gallery[index:index + 2]
            if len(pair) == 1:
                pair.append("")
            rows.append(pair)
        table = Table(rows, colWidths=[usable_width / 2, usable_width / 2])
        table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ])
        )
        story.append(table)

    agent = payload.get("agent")
    if agent:
        lines = [
            Paragraph(
                _escape(payload.get("agent_title") or ""),
                styles["BrochureSection"],
            ),
        ]
        if agent.get("name"):
            lines.append(
                Paragraph(_escape(agent["name"]), styles["BrochureAgentName"])
            )
        if agent.get("role"):
            lines.append(Paragraph(_escape(agent["role"]), styles["BrochureZone"]))
        if agent.get("phone"):
            lines.append(
                Paragraph(_escape(agent["phone"]), styles["BrochureBody"])
            )
        if agent.get("email"):
            lines.append(
                Paragraph(_escape(agent["email"]), styles["BrochureBody"])
            )
        story.append(KeepTogether(lines))

    if payload.get("footer"):
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph(_escape(payload["footer"]), styles["BrochureFooter"]))

    document.build(story)
    return buffer.getvalue()
