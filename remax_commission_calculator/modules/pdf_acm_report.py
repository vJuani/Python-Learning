"""Commercial ACM PDF. Branding from organization. No invented numbers."""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import Image as RLImage

from modules.formatting import format_money
from modules.i18n import translate
from modules.operation_summary import _brand_logo_path
from modules.pdf_property_brochure import ACCENT, LINE, MUTED, NAVY, SOFT, WHITE


def _money(currency, value, language="es"):
    if value in (None, ""):
        return "—"
    return format_money(value, currency=currency or "USD", language=language)


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="AcmKicker", parent=styles["Normal"], fontName="Helvetica", fontSize=9, textColor=MUTED, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="AcmTitle", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=22, textColor=NAVY, leading=26, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="AcmZone", parent=styles["Normal"], fontName="Helvetica", fontSize=12, textColor=MUTED, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="AcmSection", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=13, textColor=NAVY, spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(name="AcmBody", parent=styles["Normal"], fontName="Helvetica", fontSize=10, textColor=NAVY, leading=14))
    styles.add(ParagraphStyle(name="AcmPrice", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=26, textColor=ACCENT, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="AcmFooter", parent=styles["Normal"], fontName="Helvetica", fontSize=8, textColor=MUTED, alignment=TA_CENTER, leading=11))
    styles.add(ParagraphStyle(name="AcmSmall", parent=styles["Normal"], fontName="Helvetica", fontSize=9, textColor=NAVY, leading=12))
    styles.add(ParagraphStyle(name="AcmCenter", parent=styles["Normal"], fontName="Helvetica", fontSize=11, textColor=NAVY, alignment=TA_CENTER, leading=15))
    styles.add(ParagraphStyle(name="AcmCardLabel", parent=styles["Normal"], fontName="Helvetica", fontSize=8, textColor=MUTED, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="AcmCardValue", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=12, textColor=NAVY, alignment=TA_CENTER, leading=15))
    styles.add(ParagraphStyle(name="AcmLeft", parent=styles["Normal"], fontName="Helvetica", fontSize=10, textColor=NAVY, alignment=TA_LEFT, leading=14))
    return styles


def _metric_cards(pairs):
    data = []
    row = []
    for label, value in pairs:
        row.append(
            [
                Paragraph(f'<font color="#5b6b7c" size="8">{label}</font><br/><font color="#001838" size="12"><b>{value}</b></font>', getSampleStyleSheet()["Normal"])
            ]
        )
        if len(row) == 2:
            data.append([item[0] for item in row])
            row = []
    if row:
        while len(row) < 2:
            row.append([Paragraph("", getSampleStyleSheet()["Normal"])])
        data.append([item[0] for item in row])
    table = Table(data, colWidths=[85 * mm, 85 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("BOX", (0, 0), (-1, -1), 0.3, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.2, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return table


def _range_bar(language, currency, agile, market, aspirational, current):
    styles = _styles()
    data = [[
        Paragraph(f"{translate('acm_scenario_agile', language=language)}<br/><b>{_money(currency, agile, language)}</b>", styles["AcmCenter"]),
        Paragraph(f"{translate('acm_scenario_market', language=language)}<br/><b>{_money(currency, market, language)}</b>", styles["AcmCenter"]),
        Paragraph(f"{translate('acm_scenario_aspirational', language=language)}<br/><b>{_money(currency, aspirational, language)}</b>", styles["AcmCenter"]),
        Paragraph(f"{translate('acm_current_price', language=language)}<br/><b>{_money(currency, current, language)}</b>", styles["AcmCenter"]),
    ]]
    table = Table(data, colWidths=[42 * mm, 42 * mm, 42 * mm, 42 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (2, 0), SOFT),
                ("BACKGROUND", (3, 0), (3, 0), ACCENT),
                ("TEXTCOLOR", (3, 0), (3, 0), WHITE),
                ("BOX", (0, 0), (-1, -1), 0.3, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.2, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return table


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
    metrics = view.get("metrics") or {}
    currency = acm.get("currency") or subject.get("listing_currency") or "USD"
    language = language if language in ("es", "en") else "es"
    contact = view.get("agent_contact") or {} if include_agent else {}
    story = []

    if logo_path:
        try:
            story.append(RLImage(str(logo_path), width=34 * mm, height=12 * mm))
            story.append(Spacer(1, 4 * mm))
        except Exception:
            pass
    story.append(Paragraph(brand_name or "JRH One", styles["AcmKicker"]))
    story.append(Paragraph(translate("acm_pdf_cover", language=language).replace(" ", "<br/>", 1), styles["AcmTitle"]))
    if acm.get("status") != "finalized":
        story.append(Paragraph(translate("acm_pdf_draft", language=language), styles["AcmZone"]))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(subject.get("address") or "", styles["AcmTitle"]))
    loc = " · ".join(part for part in (subject.get("neighborhood"), subject.get("jurisdiction")) if part)
    if loc:
        story.append(Paragraph(loc, styles["AcmZone"]))
    photo = view.get("photo_path") or view.get("photo_url")
    if photo and Path(str(photo)).exists():
        try:
            story.append(Spacer(1, 4 * mm))
            story.append(RLImage(str(photo), width=160 * mm, height=70 * mm))
        except Exception:
            pass
    date_label = acm.get("finalized_at") or acm.get("updated_at") or acm.get("created_at") or ""
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(translate("acm_reference_value", language=language), styles["AcmKicker"]))
    story.append(Paragraph(_money(currency, acm.get("estimated_value"), language), styles["AcmPrice"]))
    story.append(Paragraph(translate("acm_suggested_range", language=language), styles["AcmKicker"]))
    story.append(
        Paragraph(
            f"{_money(currency, acm.get('suggested_min_value'), language)} — {_money(currency, acm.get('suggested_max_value'), language)}",
            styles["AcmCenter"],
        )
    )
    story.append(
        Paragraph(
            translate("acm_pdf_date", language=language, date=str(date_label)[:10]),
            styles["AcmZone"],
        )
    )
    if include_agent and contact.get("name"):
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph(contact.get("name"), styles["AcmCenter"]))
        story.append(Paragraph(translate("acm_agent_role", language=language), styles["AcmZone"]))
        bits = [part for part in (contact.get("phone"), contact.get("email")) if part]
        if bits:
            story.append(Paragraph(" · ".join(bits), styles["AcmSmall"]))

    story.append(PageBreak())
    story.append(Paragraph(translate("acm_pdf_property", language=language), styles["AcmSection"]))
    chips = []
    area = subject.get("covered_m2") or subject.get("total_m2")
    if area:
        chips.append(f"{area} m²")
    if subject.get("rooms"):
        chips.append(f"{subject['rooms']} {translate('acm_rooms_short', language=language)}")
    if subject.get("bedrooms"):
        chips.append(f"{subject['bedrooms']} {translate('acm_bedrooms_short', language=language)}")
    if subject.get("bathrooms"):
        chips.append(f"{subject['bathrooms']} {translate('property_bathrooms', language=language)}")
    if subject.get("parking_spaces"):
        chips.append(translate("acm_parking", language=language))
    story.append(
        Paragraph(
            f"{translate('acm_current_price', language=language)}: {_money(currency, subject.get('listing_price'), language)}",
            styles["AcmBody"],
        )
    )
    if chips:
        story.append(Paragraph(" · ".join(chips), styles["AcmBody"]))
    if loc:
        story.append(Paragraph(loc, styles["AcmSmall"]))
    description = subject.get("description") or (view.get("property") or {}).get("description")
    if description:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(str(description)[:400], styles["AcmBody"]))

    story.append(PageBreak())
    story.append(Paragraph(translate("acm_pdf_market", language=language), styles["AcmSection"]))
    story.append(
        _metric_cards(
            [
                [translate("acm_used_similar", language=language, n=metrics.get("valuation_count") or 0), str(metrics.get("valuation_count") or 0)],
                [translate("acm_ppm2_median", language=language), _money(currency, acm.get("median_price_per_m2"), language)],
                [translate("acm_reference_value", language=language), _money(currency, acm.get("estimated_value"), language)],
                [
                    translate("acm_suggested_range", language=language),
                    f"{_money(currency, acm.get('suggested_min_value'), language)} — {_money(currency, acm.get('suggested_max_value'), language)}",
                ],
                [
                    translate("acm_confidence", language=language),
                    translate(f"acm_confidence_{metrics.get('confidence') or 'low'}", language=language),
                ],
            ]
        )
    )
    scenarios = metrics.get("scenarios") or {}
    if scenarios:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(translate("acm_pdf_positioning", language=language), styles["AcmSection"]))
        story.append(
            _range_bar(
                language,
                currency,
                scenarios.get("agile"),
                scenarios.get("market") or acm.get("estimated_value"),
                scenarios.get("aspirational"),
                subject.get("listing_price"),
            )
        )
        positioning = metrics.get("positioning") or {}
        if positioning.get("delta_pct") not in (None, ""):
            story.append(Spacer(1, 4 * mm))
            story.append(
                Paragraph(
                    translate(
                        "acm_pdf_where",
                        language=language,
                        value=str(positioning.get("delta_pct")).replace(".", ",") if language == "es" else positioning.get("delta_pct"),
                    ),
                    styles["AcmBody"],
                )
            )

    selected_rows = [row for row in (view.get("comparables") or []) if row.get("selected")]
    if selected_rows:
        story.append(PageBreak())
        story.append(Paragraph(translate("acm_comps_used_title", language=language), styles["AcmSection"]))
    for row in selected_rows:
        block = []
        block.append(Paragraph(row.get("external_reference") or row.get("snapshot_location") or "—", styles["AcmSection"]))
        block.append(
            Paragraph(
                f"{_money(row.get('snapshot_currency') or currency, row.get('snapshot_price'), language)}",
                styles["AcmBody"],
            )
        )
        block.append(
            Paragraph(
                f"{row.get('display_area') or row.get('snapshot_covered_area') or row.get('snapshot_total_area') or '—'} m² · "
                f"{_money(row.get('snapshot_currency') or currency, row.get('snapshot_price_per_m2'), language)}/m²",
                styles["AcmBody"],
            )
        )
        traits = []
        if row.get("snapshot_rooms"):
            traits.append(f"{row['snapshot_rooms']} {translate('acm_rooms_short', language=language)}")
        if row.get("snapshot_bedrooms"):
            traits.append(f"{row['snapshot_bedrooms']} {translate('acm_bedrooms_short', language=language)}")
        if traits:
            block.append(Paragraph(" · ".join(traits), styles["AcmSmall"]))
        source = row.get("source_label") or ""
        if source:
            block.append(Paragraph(f"{translate('acm_col_source', language=language)}: {source}", styles["AcmSmall"]))
        diffs = " · ".join(row.get("diff_labels") or [])
        if diffs:
            block.append(Paragraph(diffs, styles["AcmSmall"]))
        story.append(KeepTogether(block + [Spacer(1, 4 * mm)]))

    story.append(PageBreak())
    story.append(Paragraph(translate("acm_pdf_conclusion", language=language), styles["AcmSection"]))
    explained = (view.get("ai_explanation") or {}).get("text") or acm.get("explanation")
    if explained:
        story.append(Paragraph(explained, styles["AcmBody"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(translate("acm_reference_value", language=language), styles["AcmKicker"]))
    story.append(Paragraph(_money(currency, acm.get("estimated_value"), language), styles["AcmPrice"]))
    story.append(
        Paragraph(
            f"{_money(currency, acm.get('suggested_min_value'), language)} — {_money(currency, acm.get('suggested_max_value'), language)}",
            styles["AcmCenter"],
        )
    )
    if include_agent and contact.get("name"):
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(contact.get("name"), styles["AcmCenter"]))
        bits = [part for part in (contact.get("phone"), contact.get("email")) if part]
        if bits:
            story.append(Paragraph(" · ".join(bits), styles["AcmSmall"]))
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(translate("acm_disclaimer", language=language), styles["AcmFooter"]))
    story.append(Paragraph(brand_name or "JRH One", styles["AcmFooter"]))
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
