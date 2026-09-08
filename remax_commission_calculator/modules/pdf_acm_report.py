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
    styles.add(ParagraphStyle(name="AcmKicker", parent=styles["Normal"], fontName="Helvetica", fontSize=9, textColor=MUTED))
    styles.add(ParagraphStyle(name="AcmTitle", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=20, textColor=NAVY, leading=24))
    styles.add(ParagraphStyle(name="AcmZone", parent=styles["Normal"], fontName="Helvetica", fontSize=11, textColor=MUTED))
    styles.add(ParagraphStyle(name="AcmSection", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=12, textColor=NAVY, spaceBefore=8, spaceAfter=4))
    styles.add(ParagraphStyle(name="AcmBody", parent=styles["Normal"], fontName="Helvetica", fontSize=10, textColor=NAVY, leading=14))
    styles.add(ParagraphStyle(name="AcmPrice", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=22, textColor=ACCENT, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name="AcmFooter", parent=styles["Normal"], fontName="Helvetica", fontSize=8, textColor=MUTED, alignment=TA_CENTER, leading=11))
    styles.add(ParagraphStyle(name="AcmSmall", parent=styles["Normal"], fontName="Helvetica", fontSize=9, textColor=NAVY, leading=12))
    return styles


def _chip_table(pairs):
    table = Table(pairs, colWidths=[55 * mm, 115 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("TEXTCOLOR", (0, 0), (-1, -1), NAVY),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("PADDING", (0, 0), (-1, -1), 7),
                ("BOX", (0, 0), (-1, -1), 0.3, LINE),
                ("LINEBELOW", (0, 0), (-1, -2), 0.2, LINE),
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
    story = []
    if logo_path:
        try:
            story.append(RLImage(str(logo_path), width=32 * mm, height=11 * mm))
            story.append(Spacer(1, 3 * mm))
        except Exception:
            pass
    story.append(Paragraph(brand_name or "JRH One", styles["AcmKicker"]))
    story.append(Paragraph(translate("acm_pdf_cover", language=language), styles["AcmTitle"]))
    if acm.get("status") != "finalized":
        story.append(Paragraph(translate("acm_pdf_draft", language=language), styles["AcmZone"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(subject.get("address") or "", styles["AcmSection"]))
    loc = " · ".join(
        part for part in (subject.get("neighborhood"), subject.get("jurisdiction")) if part
    )
    if loc:
        story.append(Paragraph(loc, styles["AcmZone"]))
    date_label = acm.get("finalized_at") or acm.get("updated_at") or acm.get("created_at") or ""
    story.append(
        Paragraph(
            translate("acm_pdf_date", language=language, date=str(date_label)[:10]),
            styles["AcmZone"],
        )
    )
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(translate("acm_hero_title", language=language), styles["AcmKicker"]))
    story.append(Paragraph(_money(currency, acm.get("estimated_value"), language), styles["AcmPrice"]))
    story.append(Paragraph(translate("acm_hero_range", language=language), styles["AcmKicker"]))
    story.append(
        Paragraph(
            f"{_money(currency, acm.get('suggested_min_value'), language)} — {_money(currency, acm.get('suggested_max_value'), language)}",
            styles["AcmBody"],
        )
    )
    if include_agent:
        contact = view.get("agent_contact") or {}
        if contact.get("name"):
            story.append(Spacer(1, 6 * mm))
            story.append(Paragraph(translate("acm_agent_block", language=language), styles["AcmKicker"]))
            story.append(
                Paragraph(
                    " · ".join(
                        part
                        for part in (
                            contact.get("name"),
                            contact.get("phone"),
                            contact.get("email"),
                        )
                        if part
                    ),
                    styles["AcmSmall"],
                )
            )

    story.append(PageBreak())
    story.append(Paragraph(translate("acm_pdf_subject", language=language), styles["AcmSection"]))
    chips = []
    if subject.get("property_type"):
        chips.append(str(subject["property_type"]))
    if subject.get("rooms"):
        chips.append(f"{subject['rooms']} amb.")
    if subject.get("covered_m2") or subject.get("total_m2"):
        chips.append(f"{subject.get('covered_m2') or subject.get('total_m2')} m²")
    if subject.get("bedrooms"):
        chips.append(f"{subject['bedrooms']} dorm.")
    if subject.get("bathrooms"):
        chips.append(f"{subject['bathrooms']} baños")
    if subject.get("parking_spaces"):
        chips.append(translate("acm_parking", language=language))
    if chips:
        story.append(Paragraph(" · ".join(chips), styles["AcmBody"]))
    story.append(
        Paragraph(
            f"{translate('acm_current_price', language=language)}: {_money(currency, subject.get('listing_price'), language)}",
            styles["AcmBody"],
        )
    )
    positioning = metrics.get("positioning") or {}
    if positioning.get("band"):
        story.append(
            Paragraph(
                translate(f"acm_position_{positioning['band']}", language=language),
                styles["AcmBody"],
            )
        )
    highlights = view.get("market_highlights") or []
    if highlights:
        story.append(Paragraph(translate("acm_highlights_title", language=language), styles["AcmSection"]))
        for item in highlights:
            story.append(Paragraph(f"• {item}", styles["AcmBody"]))

    story.append(PageBreak())
    story.append(Paragraph(translate("acm_pdf_market", language=language), styles["AcmSection"]))
    quality = view.get("quality_summary") or {}
    story.append(
        _chip_table(
            [
                [translate("acm_found_label", language=language), str(metrics.get("found_count") or 0)],
                [
                    translate("acm_valid_label", language=language),
                    str(metrics.get("valuation_count") or metrics.get("used_count") or 0),
                ],
                [translate("acm_closings_label", language=language), str(metrics.get("closing_count") or 0)],
                [
                    translate("acm_ppm2_median", language=language),
                    _money(currency, acm.get("median_price_per_m2"), language),
                ],
                [
                    translate("acm_confidence", language=language),
                    translate(
                        f"acm_confidence_{metrics.get('confidence') or 'low'}",
                        language=language,
                    ),
                ],
                [
                    translate("acm_quality_block", language=language),
                    f"{quality.get('subject_complete') or 0}/{quality.get('subject_total') or 6}",
                ],
            ]
        )
    )
    scenarios = metrics.get("scenarios") or {}
    if scenarios:
        story.append(Spacer(1, 4 * mm))
        story.append(
            Paragraph(
                f"{translate('acm_scenario_agile', language=language)}: {_money(currency, scenarios.get('agile'), language)}",
                styles["AcmBody"],
            )
        )
        story.append(
            Paragraph(
                f"{translate('acm_scenario_market', language=language)}: {_money(currency, scenarios.get('market'), language)}",
                styles["AcmBody"],
            )
        )
        story.append(
            Paragraph(
                f"{translate('acm_scenario_aspirational', language=language)}: {_money(currency, scenarios.get('aspirational'), language)}",
                styles["AcmBody"],
            )
        )

    selected_rows = [row for row in (view.get("comparables") or []) if row.get("selected")]
    for row in selected_rows:
        block = []
        block.append(Paragraph(row.get("external_reference") or row.get("snapshot_location") or "—", styles["AcmSection"]))
        block.append(
            Paragraph(
                f"{_money(row.get('snapshot_currency') or currency, row.get('snapshot_price'), language)} · "
                f"{row.get('display_area') or row.get('snapshot_covered_area') or row.get('snapshot_total_area') or '—'} m² · "
                f"{_money(row.get('snapshot_currency') or currency, row.get('snapshot_price_per_m2'), language)}/m²",
                styles["AcmBody"],
            )
        )
        source = row.get("source_label") or row.get("source_type") or ""
        block.append(Paragraph(f"{translate('acm_col_source', language=language)}: {source}", styles["AcmSmall"]))
        diffs = " · ".join(row.get("diff_labels") or [])
        matches = " · ".join(row.get("match_labels") or [])
        if matches:
            block.append(Paragraph(matches, styles["AcmSmall"]))
        if diffs:
            block.append(Paragraph(diffs, styles["AcmSmall"]))
        story.append(KeepTogether(block + [Spacer(1, 3 * mm)]))

    story.append(PageBreak())
    story.append(Paragraph(translate("acm_pdf_conclusion", language=language), styles["AcmSection"]))
    story.append(Paragraph(_money(currency, acm.get("estimated_value"), language), styles["AcmPrice"]))
    story.append(
        Paragraph(
            f"{_money(currency, acm.get('suggested_min_value'), language)} — {_money(currency, acm.get('suggested_max_value'), language)}",
            styles["AcmBody"],
        )
    )
    explained = (view.get("ai_explanation") or {}).get("text") or acm.get("explanation")
    if explained:
        story.append(Paragraph(explained, styles["AcmBody"]))
    if include_agent:
        contact = view.get("agent_contact") or {}
        if contact:
            story.append(Paragraph(translate("acm_agent_block", language=language), styles["AcmSection"]))
            story.append(
                Paragraph(
                    " · ".join(
                        part
                        for part in (contact.get("name"), contact.get("phone"), contact.get("email"))
                        if part
                    ),
                    styles["AcmBody"],
                )
            )
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(translate("acm_disclaimer", language=language), styles["AcmFooter"]))
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
