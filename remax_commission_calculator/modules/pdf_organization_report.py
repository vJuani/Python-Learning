"""
Executive PDF export for organization reports (ReportLab).
"""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
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
MUTED = colors.HexColor("#5b6b7c")
LINE = colors.HexColor("#c9d6e5")
SOFT = colors.HexColor("#eef3f9")
ROW_ALT = colors.HexColor("#f5f8fc")
WHITE = colors.white
MONEY_BG = colors.HexColor("#eaf6ff")


def _money(amount, language):
    return format_money(amount, currency="USD", language=language)


def _number(amount, language, decimals=2):
    return format_number(amount, language=language, decimals=decimals)


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


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            textColor=NAVY,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "ReportSubtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            textColor=MUTED,
            spaceAfter=8,
        ),
        "section": ParagraphStyle(
            "ReportSection",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            textColor=NAVY,
            spaceBefore=10,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=NAVY,
        ),
        "right": ParagraphStyle(
            "ReportRight",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=NAVY,
            alignment=TA_RIGHT,
        ),
        "muted": ParagraphStyle(
            "ReportMuted",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=MUTED,
            spaceAfter=6,
        ),
    }


def _kpi_table(report, styles):
    labels = report["labels"]
    metrics = report["metrics"]
    language = report["language"]
    rows = [
        [
            Paragraph(labels["operations_count"], styles["body"]),
            Paragraph(str(metrics["operations_count"]), styles["right"]),
            Paragraph(labels["volume_usd"], styles["body"]),
            Paragraph(_money(metrics["volume_usd"], language), styles["right"]),
        ],
        [
            Paragraph(labels["total_commission"], styles["body"]),
            Paragraph(
                _money(metrics["total_commission"], language),
                styles["right"],
            ),
            Paragraph(labels["agent_payments"], styles["body"]),
            Paragraph(
                _money(metrics["agent_payments"], language),
                styles["right"],
            ),
        ],
        [
            Paragraph(labels["office_net"], styles["body"]),
            Paragraph(_money(metrics["office_net"], language), styles["right"]),
            Paragraph(labels["vat_total"], styles["body"]),
            Paragraph(_money(metrics["vat_total"], language), styles["right"]),
        ],
        [
            Paragraph(labels["invoiced_count"], styles["body"]),
            Paragraph(str(metrics["invoiced_count"]), styles["right"]),
            Paragraph(labels["not_invoiced_count"], styles["body"]),
            Paragraph(str(metrics["not_invoiced_count"]), styles["right"]),
        ],
        [
            Paragraph(labels["properties_count"], styles["body"]),
            Paragraph(str(metrics["properties_count"]), styles["right"]),
            Paragraph(labels["average_commission"], styles["body"]),
            Paragraph(
                _money(metrics["average_commission"], language),
                styles["right"],
            ),
        ],
        [
            Paragraph(labels["approved_count"], styles["body"]),
            Paragraph(str(metrics["approved_count"]), styles["right"]),
            Paragraph(labels["pending_count"], styles["body"]),
            Paragraph(str(metrics["pending_count"]), styles["right"]),
        ],
    ]
    table = Table(rows, colWidths=[45 * mm, 40 * mm, 45 * mm, 40 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("BACKGROUND", (1, 0), (1, -1), MONEY_BG),
                ("BACKGROUND", (3, 0), (3, -1), MONEY_BG),
            ]
        )
    )
    return table


def _ranking_table(report, styles):
    labels = report["labels"]
    language = report["language"]
    header = [
        Paragraph(labels["col_rank"], styles["body"]),
        Paragraph(labels["col_agent"], styles["body"]),
        Paragraph(labels["col_operations"], styles["body"]),
        Paragraph(labels["col_commission"], styles["body"]),
    ]
    rows = [header]

    for item in report["agent_ranking"][:10]:
        rows.append(
            [
                Paragraph(str(item["rank"]), styles["body"]),
                Paragraph(item["agent_name"], styles["body"]),
                Paragraph(str(item["operations_count"]), styles["right"]),
                Paragraph(
                    _money(item["total_commission"], language),
                    styles["right"],
                ),
            ]
        )

    if len(rows) == 1:
        rows.append(
            [
                Paragraph(labels["empty"], styles["muted"]),
                "",
                "",
                "",
            ]
        )

    table = Table(rows, colWidths=[15 * mm, 70 * mm, 35 * mm, 50 * mm])
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]

    for index in range(1, len(rows)):
        if index % 2 == 0:
            style_commands.append(
                ("BACKGROUND", (0, index), (-1, index), ROW_ALT)
            )

    table.setStyle(TableStyle(style_commands))
    return table


def _monthly_table(report, styles):
    labels = report["labels"]
    language = report["language"]
    header = [
        Paragraph(labels["col_month"], styles["body"]),
        Paragraph(labels["col_operations"], styles["body"]),
        Paragraph(labels["col_volume"], styles["body"]),
        Paragraph(labels["col_commission"], styles["body"]),
    ]
    rows = [header]

    for item in report["monthly_series"]:
        rows.append(
            [
                Paragraph(item["month"], styles["body"]),
                Paragraph(str(item["operations_count"]), styles["right"]),
                Paragraph(_money(item["volume_usd"], language), styles["right"]),
                Paragraph(
                    _money(item["total_commission"], language),
                    styles["right"],
                ),
            ]
        )

    if len(rows) == 1:
        rows.append(
            [
                Paragraph(labels["empty"], styles["muted"]),
                "",
                "",
                "",
            ]
        )

    table = Table(rows, colWidths=[30 * mm, 30 * mm, 55 * mm, 55 * mm])
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]

    for index in range(1, len(rows)):
        if index % 2 == 0:
            style_commands.append(
                ("BACKGROUND", (0, index), (-1, index), ROW_ALT)
            )

    table.setStyle(TableStyle(style_commands))
    return table


def build_organization_report_pdf(report):
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=report["labels"]["pdf_title"],
    )
    styles = _styles()
    labels = report["labels"]
    story = []

    logo = _safe_logo(
        report["brand"].get("logo_path"),
        55 * mm,
        16 * mm,
    )

    if logo is not None:
        story.append(logo)
        story.append(Spacer(1, 4 * mm))

    org_name = report["brand"].get("organization_name") or ""
    story.append(Paragraph(labels["pdf_title"], styles["title"]))
    story.append(
        Paragraph(
            f"{report['brand']['app_name']}"
            + (f" — {org_name}" if org_name else ""),
            styles["subtitle"],
        )
    )
    story.append(Paragraph(labels["pdf_subtitle"], styles["muted"]))
    story.append(Paragraph(labels["official_note"], styles["muted"]))

    if report["active_filters"]:
        filters_text = " · ".join(report["active_filters"])
        story.append(
            Paragraph(
                f"{labels['filters_applied']}: {filters_text}",
                styles["body"],
            )
        )

    story.append(Spacer(1, 3 * mm))
    story.append(_kpi_table(report, styles))

    story.append(Paragraph(labels["ranking_title"], styles["section"]))
    story.append(_ranking_table(report, styles))

    if report["monthly_series"]:
        story.append(Paragraph(labels["monthly_title"], styles["section"]))
        story.append(_monthly_table(report, styles))

    document.build(story)
    return buffer.getvalue()
