"""
PDF export for operation summaries (ReportLab).
"""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import Image as RLImage

from modules.formatting import format_money, format_number


NAVY = colors.HexColor("#001838")
ACCENT = colors.HexColor("#0860c8")
ACCENT_SOFT = colors.HexColor("#e8f1fc")
MUTED = colors.HexColor("#5b6b7c")
LINE = colors.HexColor("#c9d6e5")
SOFT = colors.HexColor("#eef3f9")
ROW_ALT = colors.HexColor("#f5f8fc")
WHITE = colors.white
MONEY_BG = colors.HexColor("#eaf6ff")
TOTAL_BG = colors.HexColor("#d7ebff")
STATUS_COLORS = {
    "approved": colors.HexColor("#dcf5e5"),
    "pending": colors.HexColor("#fff4d6"),
    "rejected": colors.HexColor("#fde2e1"),
    "draft": colors.HexColor("#e8eef5"),
}


def _text(value, empty_label):
    if value is None or value == "":
        return empty_label

    return str(value)


def _money(amount, currency, language):
    if amount is None:
        return "—"

    return format_money(
        amount,
        currency=currency,
        language=language,
    )


def _number(amount, language, decimals=2):
    if amount is None:
        return "—"

    return format_number(
        amount,
        language=language,
        decimals=decimals,
    )


def _safe_logo(path_str, max_width, max_height):
    if not path_str:
        return None

    path = Path(path_str)

    if not path.is_file():
        return None

    try:
        image = RLImage(str(path))
        image.hAlign = "LEFT"
        width = float(image.imageWidth)
        height = float(image.imageHeight)

        if width <= 0 or height <= 0:
            return None

        scale = min(max_width / width, max_height / height, 1.0)
        image.drawWidth = width * scale
        image.drawHeight = height * scale
        return image
    except Exception:
        return None


def _build_styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="HeroTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=15,
            textColor=WHITE,
            leading=18,
            alignment=TA_LEFT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="HeroSub",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            textColor=colors.HexColor("#d6e4f5"),
            leading=11,
            alignment=TA_LEFT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="HeroMeta",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=WHITE,
            leading=12,
            alignment=TA_RIGHT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PageTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=12,
            textColor=NAVY,
            spaceBefore=2,
            spaceAfter=2,
            leading=15,
        )
    )
    styles.add(
        ParagraphStyle(
            name="StatusPill",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=NAVY,
            alignment=TA_CENTER,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=WHITE,
            leading=13,
        )
    )
    styles.add(
        ParagraphStyle(
            name="FieldLabel",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            textColor=MUTED,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="FieldValue",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            textColor=NAVY,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="MoneyValue",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9.5,
            textColor=ACCENT,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="TotalValue",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            textColor=NAVY,
            leading=13,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Note",
            parent=styles["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=7.5,
            textColor=MUTED,
            spaceBefore=1,
            spaceAfter=4,
            leading=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Footer",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            textColor=MUTED,
            alignment=TA_CENTER,
            leading=10,
        )
    )
    return styles


def _hero_header(brand, operation, labels, styles):
    logo = _safe_logo(
        brand.get("logo_path"),
        max_width=42 * mm,
        max_height=14 * mm,
    )

    left_bits = []

    if logo is not None:
        left_bits.append(logo)
    else:
        left_bits.append(
            Paragraph(brand["app_name"], styles["HeroTitle"])
        )

    org_name = brand.get("organization_name") or brand["app_name"]
    left_bits.append(Spacer(1, 1.5 * mm))
    left_bits.append(
        Paragraph(brand["slogan"], styles["HeroSub"])
    )
    left_bits.append(
        Paragraph(org_name, styles["HeroSub"])
    )

    right_bits = [
        Paragraph(labels["page_title"], styles["HeroMeta"]),
        Paragraph(operation["id"], styles["HeroMeta"]),
        Paragraph(
            _text(operation["date"], "—"),
            styles["HeroMeta"],
        ),
    ]

    header = Table(
        [[left_bits, right_bits]],
        colWidths=[110 * mm, 65 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )

    accent_bar = Table(
        [[""]],
        colWidths=[175 * mm],
        rowHeights=[3.2 * mm],
    )
    accent_bar.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
            ]
        )
    )

    return [header, accent_bar, Spacer(1, 4 * mm)]


def _status_chip(operation, styles):
    fill = STATUS_COLORS.get(
        operation.get("status"),
        ACCENT_SOFT,
    )
    chip = Table(
        [[
            Paragraph(
                operation["status_label"],
                styles["StatusPill"],
            )
        ]],
        colWidths=[42 * mm],
        rowHeights=[8 * mm],
    )
    chip.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), fill),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    title_row = Table(
        [[
            Paragraph(
                f"{operation['id']}",
                styles["PageTitle"],
            ),
            chip,
        ]],
        colWidths=[128 * mm, 47 * mm],
    )
    title_row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [title_row, Spacer(1, 3.5 * mm)]


def _section_table(
    title,
    rows,
    styles,
    *,
    money_labels=None,
    total_labels=None,
):
    money_labels = money_labels or set()
    total_labels = total_labels or set()

    title_table = Table(
        [[Paragraph(title, styles["SectionTitle"])]],
        colWidths=[175 * mm],
    )
    title_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    data = []
    row_styles = []

    for index, (label, value) in enumerate(rows):
        is_money = label in money_labels
        is_total = label in total_labels
        value_style = styles["FieldValue"]

        if is_total:
            value_style = styles["TotalValue"]
        elif is_money:
            value_style = styles["MoneyValue"]

        data.append(
            [
                Paragraph(str(label), styles["FieldLabel"]),
                Paragraph(str(value), value_style),
            ]
        )

        if is_total:
            bg = TOTAL_BG
        elif is_money:
            bg = MONEY_BG
        elif index % 2:
            bg = ROW_ALT
        else:
            bg = WHITE

        row_styles.append(
            ("BACKGROUND", (0, index), (-1, index), bg)
        )
        row_styles.append(
            ("BACKGROUND", (0, index), (0, index), SOFT)
        )

        if is_total:
            row_styles.append(
                ("BACKGROUND", (0, index), (-1, index), TOTAL_BG)
            )

    table = Table(data, colWidths=[58 * mm, 117 * mm])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("BOX", (0, 0), (-1, -1), 0.7, LINE),
                ("LINEBELOW", (0, 0), (-1, -2), 0.35, LINE),
                *row_styles,
            ]
        )
    )

    return KeepTogether(
        [
            title_table,
            table,
            Spacer(1, 4.5 * mm),
        ]
    )


def build_operation_summary_pdf(summary):
    labels = summary["labels"]
    operation = summary["operation"]
    numbers = summary["numbers"]
    people = summary["people"]
    brand = summary["brand"]
    language = summary["language"]
    empty = labels["empty_value"]
    currency = operation["currency"]

    buffer = io.BytesIO()
    pdf_document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"{labels['page_title']} {operation['id']}",
        author=brand["app_name"],
    )

    styles = _build_styles()
    story = []
    story.extend(
        _hero_header(brand, operation, labels, styles)
    )
    story.extend(_status_chip(operation, styles))

    story.append(
        _section_table(
            labels["section_operation"],
            [
                (
                    labels["field_operation_id"],
                    operation["id"],
                ),
                (
                    labels["field_date"],
                    _text(operation["date"], empty),
                ),
                (
                    labels["field_status"],
                    operation["status_label"],
                ),
            ],
            styles,
        )
    )

    story.append(
        _section_table(
            labels["section_property"],
            [
                (
                    labels["field_property_id"],
                    _text(operation["property_id"], empty),
                ),
                (
                    labels["field_property"],
                    _text(
                        operation["property_address"],
                        empty,
                    ),
                ),
                (
                    labels["field_jurisdiction"],
                    _text(operation["jurisdiction"], empty),
                ),
            ],
            styles,
        )
    )

    story.append(
        _section_table(
            labels["section_agent"],
            [
                (
                    labels["field_agent"],
                    _text(operation["agent_name"], empty),
                ),
                (
                    labels["field_agent_type"],
                    _text(operation["agent_type"], empty),
                ),
            ],
            styles,
        )
    )

    def _format_line(line):
        kind = line["kind"]
        value = line["value"]

        if kind == "money":
            return _money(
                value,
                line.get("currency") or "USD",
                language,
            )

        if kind == "rate":
            return _number(value, language, decimals=4)

        if kind == "percent":
            return f"{_number(value, language)}%"

        return _text(value, empty)

    billing_rows = [
        (line["label"], _format_line(line))
        for line in summary.get("billing_lines", [])
    ]
    billing_money = {
        line["label"]
        for line in summary.get("billing_lines", [])
        if line["kind"] == "money"
    }
    billing_totals = {
        line["label"]
        for line in summary.get("billing_lines", [])
        if line.get("emphasize")
    }
    story.append(
        _section_table(
            labels["section_billing"],
            billing_rows,
            styles,
            money_labels=billing_money,
            total_labels=billing_totals,
        )
    )
    story.append(
        Paragraph(labels["billing_note"], styles["Note"])
    )

    commission_rows = [
        (line["label"], _format_line(line))
        for line in summary.get("commission_lines", [])
    ]
    commission_money = {
        line["label"]
        for line in summary.get("commission_lines", [])
        if line["kind"] == "money"
    }
    commission_totals = {
        line["label"]
        for line in summary.get("commission_lines", [])
        if line.get("emphasize")
    }
    story.append(
        _section_table(
            labels["section_commission"],
            commission_rows,
            styles,
            money_labels=commission_money,
            total_labels=commission_totals,
        )
    )

    story.append(
        _section_table(
            labels["section_status"],
            [
                (
                    labels["field_status"],
                    operation["status_label"],
                ),
                (
                    labels["field_created_by"],
                    _text(people["created_by_name"], empty),
                ),
                (
                    labels["field_reviewed_by"],
                    _text(people["reviewed_by_name"], empty),
                ),
                (
                    labels["field_reviewed_at"],
                    _text(operation["reviewed_at"], empty),
                ),
                (
                    labels["field_rejection_reason"],
                    _text(
                        operation["rejection_reason"],
                        empty,
                    ),
                ),
            ],
            styles,
        )
    )

    if summary["can_see_documents"]:
        if not summary["documents"]:
            pair_rows = [
                (
                    labels["section_documents"],
                    labels["no_documents"],
                )
            ]
        else:
            pair_rows = []
            for doc_row in summary["documents"]:
                pair_rows.append(
                    (
                        doc_row["doc_type_label"],
                        f"{doc_row['original_filename']}"
                        f" · {_text(doc_row['uploaded_at'], empty)}",
                    )
                )

        story.append(
            _section_table(
                labels["section_documents"],
                pair_rows,
                styles,
            )
        )
    else:
        story.append(
            _section_table(
                labels["section_documents"],
                [
                    (
                        labels["section_documents"],
                        labels["documents_hidden"],
                    )
                ],
                styles,
            )
        )

    footer_bar = Table(
        [[
            Paragraph(
                f"{brand['app_name']}  ·  {brand['slogan']}  ·  {operation['id']}",
                styles["Footer"],
            )
        ]],
        colWidths=[175 * mm],
    )
    footer_bar.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(Spacer(1, 2 * mm))
    story.append(footer_bar)

    pdf_document.build(story)
    buffer.seek(0)
    return buffer.getvalue()


__all__ = [
    "build_operation_summary_pdf",
]
