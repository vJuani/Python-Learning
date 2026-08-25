"""
PDF export for team reports (ReportLab).
"""

from __future__ import annotations

import io

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

from modules.formatting import format_money


NAVY = colors.HexColor("#001838")
ACCENT = colors.HexColor("#0860c8")
MUTED = colors.HexColor("#5b6b7c")
LINE = colors.HexColor("#c9d6e5")
SOFT = colors.HexColor("#eef3f9")
ROW_ALT = colors.HexColor("#f5f8fc")
WHITE = colors.white


def _money(amount, language):
    return format_money(amount, currency="USD", language=language)


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "TeamReportTitle",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            textColor=NAVY,
            spaceAfter=4,
        ),
        "sub": ParagraphStyle(
            "TeamReportSub",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            textColor=MUTED,
            spaceAfter=10,
        ),
        "body": ParagraphStyle(
            "TeamReportBody",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=NAVY,
        ),
        "right": ParagraphStyle(
            "TeamReportRight",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=NAVY,
            alignment=TA_RIGHT,
        ),
    }


def build_team_report_pdf(report):
    buffer = io.BytesIO()
    language = report.get("language") or "es"
    labels = report["labels"]
    metrics = report["metrics"]
    leader = report["leader"]
    styles = _styles()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )

    story = [
        Paragraph(labels["title"], styles["title"]),
        Paragraph(
            f"{leader['name']} ({leader['type']})",
            styles["sub"],
        ),
    ]

    period = report.get("period") or {}
    period_bits = []
    if period.get("date_from_display"):
        period_bits.append(period["date_from_display"])
    if period.get("date_to_display"):
        period_bits.append(period["date_to_display"])
    if period_bits:
        story.append(
            Paragraph(" — ".join(period_bits), styles["sub"])
        )

    metric_rows = [
        [labels["team_production"], _money(metrics["team_production"], language)],
        [labels["juniors_production"], _money(metrics["juniors_production"], language)],
        [labels["leader_own"], _money(metrics["leader_own_income"], language)],
        [labels["juniors_income"], _money(metrics["juniors_income_to_leader"], language)],
        [labels["combined"], _money(metrics["combined_income"], language)],
        [labels["operations"], str(metrics["operations_count"])],
    ]

    metrics_table = Table(metric_rows, colWidths=[100 * mm, 60 * mm])
    metrics_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (-1, -1), NAVY),
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.4, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(metrics_table)
    story.append(Spacer(1, 10))

    junior_header = [
        "Junior",
        "Type",
        "Ops",
        "Production",
        "Yield",
        "TL income",
    ]
    junior_data = [junior_header]

    for row in report["junior_rows"]:
        junior_data.append(
            [
                row["agent"]["name"],
                row["agent"]["type"],
                str(row["operations_count"]),
                _money(row["production"], language),
                _money(row["junior_yield"], language),
                _money(row["team_leader_income"], language),
            ]
        )

    if len(junior_data) == 1:
        junior_data.append(["—", "—", "0", "—", "—", "—"])

    juniors_table = Table(
        junior_data,
        colWidths=[45 * mm, 22 * mm, 15 * mm, 30 * mm, 30 * mm, 30 * mm],
    )
    juniors_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TEXTCOLOR", (0, 1), (-1, -1), NAVY),
                ("GRID", (0, 0), (-1, -1), 0.4, LINE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, ROW_ALT]),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(juniors_table)

    doc.build(story)
    return buffer.getvalue()
