"""Professional ACM PDF. Reuses brochure branding colors."""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from modules.formatting import format_money
from modules.i18n import translate
from modules.operation_summary import _brand_logo_path
from modules.pdf_property_brochure import (
    ACCENT,
    LINE,
    MUTED,
    NAVY,
    SOFT,
    WHITE,
)
from reportlab.platypus import Image as RLImage


def _money(currency, value, language="es"):
    if value in (None, ""):
        return "—"
    return format_money(value, currency=currency or "USD", language=language)


def _styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="AcmTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=18,
            textColor=NAVY,
            leading=22,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmZone",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=10,
            textColor=MUTED,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmSection",
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
            name="AcmBody",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            textColor=NAVY,
            leading=13,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmPrice",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=16,
            textColor=ACCENT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmFooter",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=MUTED,
            alignment=TA_CENTER,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmSmall",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=NAVY,
        )
    )
    return styles


def build_acm_pdf(
    view,
    *,
    include_agent=True,
    language="es",
    brand_name="",
    logo_path=None,
    compress=True,
):
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
    )
    if not compress:
        doc.compress = 0
    acm = view["acm"]
    subject = view.get("subject") or {}
    currency = acm.get("currency") or subject.get("listing_currency") or "USD"
    language = language if language in ("es", "en") else "es"
    story = []
    if logo_path:
        try:
            story.append(RLImage(str(logo_path), width=28 * mm, height=10 * mm))
            story.append(Spacer(1, 4 * mm))
        except Exception:
            pass
    story.append(Paragraph(brand_name or "JRH One", styles["AcmZone"]))
    story.append(Paragraph(translate("acm_pdf_title", language=language), styles["AcmTitle"]))
    if acm.get("status") != "finalized":
        story.append(Paragraph(translate("acm_pdf_draft", language=language), styles["AcmZone"]))
    story.append(Paragraph(subject.get("address") or "", styles["AcmSection"]))
    loc = " · ".join(
        part for part in (subject.get("neighborhood"), subject.get("jurisdiction")) if part
    )
    if loc:
        story.append(Paragraph(loc, styles["AcmZone"]))
    chips = []
    if subject.get("rooms"):
        chips.append(f"{subject['rooms']} amb.")
    if subject.get("covered_m2") or subject.get("total_m2"):
        chips.append(f"{subject.get('covered_m2') or subject.get('total_m2')} m²")
    if subject.get("bedrooms"):
        chips.append(f"{subject['bedrooms']} dorm.")
    if subject.get("parking_spaces"):
        chips.append(translate("acm_parking", language=language))
    if chips:
        story.append(Paragraph(" · ".join(chips), styles["AcmBody"]))
    date_label = acm.get("finalized_at") or acm.get("updated_at") or acm.get("created_at") or ""
    story.append(
        Paragraph(
            translate("acm_pdf_date", language=language, date=str(date_label)[:10]),
            styles["AcmZone"],
        )
    )
    story.append(PageBreak())
    story.append(Paragraph(translate("acm_step_1", language=language), styles["AcmSection"]))
    story.append(
        Paragraph(
            f"{translate('acm_current_price', language=language)}: {_money(currency, subject.get('listing_price'), language)}",
            styles["AcmBody"],
        )
    )
    story.append(PageBreak())
    story.append(Paragraph(translate("acm_pdf_market", language=language), styles["AcmSection"]))
    metrics = view.get("metrics") or {}
    metric_table = Table(
        [
            [
                translate("acm_found_label", language=language),
                str(metrics.get("found_count") or 0),
            ],
            [
                translate("acm_valid_label", language=language),
                str(metrics.get("valuation_count") or metrics.get("used_count") or 0),
            ],
            [
                translate("acm_closings_label", language=language),
                str(metrics.get("closing_count") or 0),
            ],
            [
                translate("acm_ppm2_median", language=language),
                _money(currency, acm.get("median_price_per_m2"), language),
            ],
            [
                translate("acm_ppm2_avg", language=language),
                _money(currency, acm.get("average_price_per_m2"), language),
            ],
            [
                translate("acm_confidence", language=language),
                translate(
                    f"acm_confidence_{metrics.get('confidence') or 'low'}",
                    language=language,
                ),
            ],
        ],
        colWidths=[90 * mm, 70 * mm],
    )
    metric_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("TEXTCOLOR", (0, 0), (-1, -1), NAVY),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("BOX", (0, 0), (-1, -1), 0.4, LINE),
            ]
        )
    )
    story.append(metric_table)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(translate("acm_reference_value", language=language), styles["AcmZone"]))
    story.append(
        Paragraph(_money(currency, acm.get("estimated_value"), language), styles["AcmPrice"])
    )
    story.append(Paragraph(translate("acm_suggested_range", language=language), styles["AcmZone"]))
    story.append(
        Paragraph(
            f"{_money(currency, acm.get('suggested_min_value'), language)} – {_money(currency, acm.get('suggested_max_value'), language)}",
            styles["AcmBody"],
        )
    )
    scenarios = metrics.get("scenarios") or {}
    if scenarios:
        story.append(Paragraph(translate("acm_scenario_agile", language=language) + ": " + _money(currency, scenarios.get("agile"), language), styles["AcmBody"]))
        story.append(Paragraph(translate("acm_scenario_market", language=language) + ": " + _money(currency, scenarios.get("market"), language), styles["AcmBody"]))
        story.append(Paragraph(translate("acm_scenario_aspirational", language=language) + ": " + _money(currency, scenarios.get("aspirational"), language), styles["AcmBody"]))
    story.append(PageBreak())
    story.append(Paragraph(translate("acm_comparables", language=language), styles["AcmSection"]))
    header = [
        translate("acm_col_property", language=language),
        translate("acm_col_price", language=language),
        "m²",
        translate("acm_col_ppm2", language=language),
        translate("acm_col_source", language=language),
    ]
    data = [header]
    selected_rows = [row for row in (view.get("comparables") or []) if row.get("selected")]
    appendix = selected_rows[5:]
    for row in selected_rows[:5]:
        source = row.get("source_type") or ""
        if source == "manual_external":
            source_label = translate("acm_source_manual", language=language)
        elif source == "closed_operation" or row.get("snapshot_price_kind") == "closing":
            source_label = translate("acm_source_closing", language=language)
        else:
            source_label = translate("acm_source_listing", language=language)
        data.append(
            [
                Paragraph(
                    row.get("external_reference") or row.get("snapshot_location") or "—",
                    styles["AcmSmall"],
                ),
                _money(row.get("snapshot_currency") or currency, row.get("snapshot_price"), language),
                str(row.get("display_area") or row.get("snapshot_covered_area") or row.get("snapshot_total_area") or "—"),
                _money(row.get("snapshot_currency") or currency, row.get("snapshot_price_per_m2"), language),
                source_label,
            ]
        )
    table = Table(data, colWidths=[55 * mm, 32 * mm, 20 * mm, 32 * mm, 33 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TEXTCOLOR", (0, 1), (-1, -1), NAVY),
                ("GRID", (0, 0), (-1, -1), 0.3, LINE),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    story.append(PageBreak())
    story.append(Paragraph(translate("acm_pdf_analysis", language=language), styles["AcmSection"]))
    if acm.get("explanation"):
        story.append(Paragraph(translate("acm_explanation_title", language=language), styles["AcmSection"]))
        story.append(Paragraph(acm["explanation"], styles["AcmBody"]))
    if appendix:
        story.append(Paragraph("Apéndice", styles["AcmSection"]))
        for row in appendix:
            story.append(
                Paragraph(
                    f"{row.get('external_reference') or ''} · {_money(row.get('snapshot_currency') or currency, row.get('snapshot_price'), language)}",
                    styles["AcmSmall"],
                )
            )
    if include_agent:
        contact = view.get("agent_contact") or {}
        if contact:
            story.append(Paragraph(translate("acm_agent_block", language=language), styles["AcmSection"]))
            parts = [
                contact.get("name"),
                contact.get("phone"),
                contact.get("email"),
            ]
            story.append(
                Paragraph(" · ".join(part for part in parts if part), styles["AcmBody"])
            )
    story.append(Spacer(1, 6 * mm))
    story.append(
        Paragraph(
            translate("acm_disclaimer", language=language),
            styles["AcmFooter"],
        )
    )
    doc.build(story)
    buffer.seek(0)
    return buffer


def generate_acm_pdf_bytes(view, *, include_agent=True, language="es"):
    from modules.database.organization_settings_repository import (
        get_organization_settings,
    )

    org_id = view["acm"]["organization_id"]
    settings = get_organization_settings(org_id) or {}
    brand = settings.get("display_name") or "JRH One"
    logo = _brand_logo_path(settings.get("logo_path"))
    try:
        if logo and Path(logo).stat().st_size > 400_000:
            logo = None
    except OSError:
        logo = None
    return build_acm_pdf(
        view,
        include_agent=include_agent,
        language=language,
        brand_name=brand,
        logo_path=logo,
    )
