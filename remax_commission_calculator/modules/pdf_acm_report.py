"""Premium A4 ACM PDF. Real snapshot data only. Official JRH logo."""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from modules.acm_engine import PRICE_CLOSING, display_area, price_per_m2
from modules.branding import get_app_domain, get_logo_icon_path, resolve_brand_logo_path
from modules.formatting import format_money
from modules.i18n import translate
from modules.operation_summary import _brand_logo_path
from modules.pdf_images import compose_on_color, pdf_image_flowable


NAVY = colors.HexColor("#0A1633")
ELECTRIC = colors.HexColor("#0D47FF")
STEEL = colors.HexColor("#33415C")
MUTED = colors.HexColor("#5B6B7C")
LINE = colors.HexColor("#D7E2EE")
SOFT = colors.HexColor("#F2F4F8")
WHITE = colors.white
NAVY_RGB = (10, 22, 51)
HIDDEN_PORTALS = {"zonaprop", "argenprop", "mercadolibre"}


def _escape(value):
    text = str(value or "").replace("—", "-").replace("–", "-")
    return (
        text.encode("latin-1", "replace")
        .decode("latin-1")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _money(currency, value, language="es"):
    if value in (None, ""):
        return "-"
    return format_money(value, currency=currency or "USD", language=language)


def _area_label(value):
    if value in (None, ""):
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return str(int(number))
    return f"{number:.1f}".replace(".", ",")


def _short_date(value, language="es"):
    text = str(value or "")[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        year, month, day = text.split("-")
        if language == "en":
            return f"{month}/{day}/{year}"
        return f"{day}/{month}/{year}"
    return text


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="AcmKicker", parent=styles["Normal"], fontName="Helvetica", fontSize=8, textColor=ELECTRIC, leading=10))
    styles.add(ParagraphStyle(name="AcmTitle", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=22, textColor=NAVY, leading=25))
    styles.add(ParagraphStyle(name="AcmZone", parent=styles["Normal"], fontName="Helvetica", fontSize=11, textColor=STEEL, leading=14))
    styles.add(ParagraphStyle(name="AcmSubcopy", parent=styles["Normal"], fontName="Helvetica", fontSize=7.5, textColor=MUTED, leading=10))
    styles.add(ParagraphStyle(name="AcmBody", parent=styles["Normal"], fontName="Helvetica", fontSize=8.5, textColor=NAVY, leading=12))
    styles.add(ParagraphStyle(name="AcmSection", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=11, textColor=NAVY, spaceBefore=1, spaceAfter=3))
    styles.add(ParagraphStyle(name="AcmCardLabel", parent=styles["Normal"], fontName="Helvetica", fontSize=7, textColor=MUTED, leading=9))
    styles.add(ParagraphStyle(name="AcmHeroValue", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=18, textColor=NAVY, leading=21))
    styles.add(ParagraphStyle(name="AcmHeroRange", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=11, textColor=ELECTRIC, leading=14))
    styles.add(ParagraphStyle(name="AcmCompTitle", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8, textColor=NAVY, leading=10))
    styles.add(ParagraphStyle(name="AcmSmall", parent=styles["Normal"], fontName="Helvetica", fontSize=7, textColor=MUTED, leading=9))
    styles.add(ParagraphStyle(name="AcmSmallNavy", parent=styles["Normal"], fontName="Helvetica", fontSize=7.2, textColor=NAVY, leading=9.5))
    styles.add(ParagraphStyle(name="AcmTableHead", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=6.5, textColor=STEEL, leading=8))
    styles.add(ParagraphStyle(name="AcmTableCell", parent=styles["Normal"], fontName="Helvetica", fontSize=7, textColor=NAVY, leading=9))
    styles.add(ParagraphStyle(name="AcmTableCellBold", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=7, textColor=NAVY, leading=9))
    styles.add(ParagraphStyle(name="AcmReport", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9, textColor=NAVY, alignment=TA_RIGHT, leading=11))
    styles.add(ParagraphStyle(name="AcmReportDate", parent=styles["Normal"], fontName="Helvetica", fontSize=8, textColor=MUTED, alignment=TA_RIGHT, leading=10))
    return styles


class Ppm2Chart(Flowable):
    def __init__(self, items, width, height=46 * mm):
        super().__init__()
        self.items = items
        self.box_width = width
        self.height = height

    def wrap(self, avail_width, avail_height):
        self.width = min(self.box_width, avail_width)
        return self.width, self.height

    def draw(self):
        if not self.items:
            return
        values = [item["value"] for item in self.items]
        peak = max(values) or 1
        count = len(self.items)
        gap = 8
        usable = self.width - 8
        bar_width = max(12, (usable - gap * (count - 1)) / count)
        baseline = 16
        top = self.height - 12
        chart_h = max(10, top - baseline)
        self.canv.setStrokeColor(LINE)
        self.canv.setLineWidth(0.4)
        self.canv.line(0, baseline, self.width, baseline)
        for index, item in enumerate(self.items):
            x = 4 + index * (bar_width + gap)
            bar_h = chart_h * (item["value"] / peak)
            self.canv.setFillColor(item.get("color") or ELECTRIC)
            self.canv.roundRect(x, baseline, bar_width, bar_h, 3, fill=1, stroke=0)
            self.canv.setFillColor(NAVY)
            self.canv.setFont("Helvetica-Bold", 6.5)
            label = str(item.get("display") or int(round(item["value"])))
            self.canv.drawCentredString(x + bar_width / 2, baseline + bar_h + 2, label)
            self.canv.setFillColor(MUTED)
            self.canv.setFont("Helvetica", 6)
            self.canv.drawCentredString(x + bar_width / 2, 4, str(item.get("label") or "")[:18])


def _resolve_pdf_image(item, cache):
    if not item:
        return None
    path = Path(str(item))
    if path.is_file():
        return path
    url = str(item).strip()
    if url.startswith("https://"):
        from modules.property_sync.remote_media import fetch_allowed_image_bytes

        try:
            return fetch_allowed_image_bytes(url, cache=cache)
        except Exception:
            return None
    return None


def _collect_photos(view):
    cache = view.setdefault("_pdf_image_cache", {})
    found = []
    seen = set()
    candidates = list(view.get("photo_urls") or [])
    for key in ("photo_path", "photo_url", "hero_image"):
        if view.get(key):
            candidates.append(view[key])
    for blob in (view.get("subject") or {}, view.get("property") or {}):
        for key in ("photo_path", "photo_url", "hero_image"):
            if blob.get(key):
                candidates.append(blob[key])
    for item in candidates:
        resolved = _resolve_pdf_image(item, cache)
        if resolved is None:
            continue
        identity = str(item)
        if identity in seen:
            continue
        seen.add(identity)
        found.append(resolved)
        if len(found) >= 5:
            break
    return found


def _public_source(row):
    key = str(row.get("source_key") or row.get("source_type") or "").strip().lower()
    if key in HIDDEN_PORTALS:
        return None
    return row.get("source_label") or None


def _official_logo_path():
    icon = get_logo_icon_path()
    if icon.is_file() and icon.stat().st_size <= 400_000:
        return icon
    logo = resolve_brand_logo_path()
    try:
        if logo and Path(logo).stat().st_size <= 400_000:
            return logo
    except OSError:
        return icon if icon.is_file() else None
    return icon if icon.is_file() else logo


def _header(styles, usable_width, language, generated_at):
    logo_path = _official_logo_path()
    logo = pdf_image_flowable(
        logo_path,
        32 * mm,
        11 * mm,
        mode="contain",
        background=(255, 255, 255),
        preserve_alpha=True,
    )
    right = [
        Paragraph(_escape(translate("acm_pdf_report_label", language=language)), styles["AcmReport"]),
        Paragraph(_escape(generated_at or ""), styles["AcmReportDate"]),
    ]
    right_table = Table([[item] for item in right], colWidths=[usable_width * 0.38])
    right_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    table = Table([[logo or "", right_table]], colWidths=[usable_width * 0.62, usable_width * 0.38])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


def _photo_collage(photos, width, height=58 * mm):
    if not photos:
        return None
    if len(photos) == 1:
        return pdf_image_flowable(photos[0], width, height, mode="cover")
    hero_w = width * 0.62
    side_w = width * 0.36
    extras = photos[1:5]
    rows = 2 if len(extras) > 2 else 1
    cols = 2 if len(extras) > 1 else 1
    cell_h = (height - 2 * mm) / rows
    cell_w = (side_w - 2 * mm) / cols
    cells = []
    for item in extras:
        cells.append(pdf_image_flowable(item, cell_w, cell_h, mode="cover") or "")
    while len(cells) < rows * cols:
        cells.append("")
    grid_data = [cells[index : index + cols] for index in range(0, rows * cols, cols)]
    grid = Table(grid_data, colWidths=[cell_w] * cols, rowHeights=[cell_h] * rows)
    grid.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 1),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    hero = pdf_image_flowable(photos[0], hero_w, height, mode="cover") or ""
    table = Table([[hero, grid]], colWidths=[hero_w, side_w + 2 * mm])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


def _hero(styles, view, language, usable_width):
    subject = view.get("subject") or {}
    address = _escape(subject.get("address") or "")
    loc = " · ".join(part for part in (subject.get("neighborhood"), subject.get("jurisdiction")) if part)
    copy = [
        Paragraph(_escape(translate("acm_pdf_title", language=language)).upper(), styles["AcmKicker"]),
        Spacer(1, 1.2 * mm),
        Paragraph(address, styles["AcmTitle"]),
    ]
    if loc:
        copy.append(Paragraph(_escape(loc), styles["AcmZone"]))
    copy.append(Spacer(1, 1.4 * mm))
    copy.append(Paragraph(_escape(translate("acm_pdf_hero_line", language=language)).upper(), styles["AcmSubcopy"]))
    photos = _collect_photos(view)
    collage = _photo_collage(photos, usable_width)
    block = copy
    if collage is not None:
        block = copy + [Spacer(1, 3 * mm), collage]
    return KeepTogether(block)


def _value_hero(styles, view, language, usable_width):
    acm = view["acm"]
    currency = acm.get("currency") or (view.get("subject") or {}).get("listing_currency") or "USD"
    left = [
        Paragraph(_escape(translate("acm_reference_value", language=language)).upper(), styles["AcmCardLabel"]),
        Spacer(1, 1.2 * mm),
        Paragraph(_escape(_money(currency, acm.get("estimated_value"), language)), styles["AcmHeroValue"]),
    ]
    right = [
        Paragraph(_escape(translate("acm_suggested_range", language=language)).upper(), styles["AcmCardLabel"]),
        Spacer(1, 1.2 * mm),
        Paragraph(
            _escape(
                f"{_money(currency, acm.get('suggested_min_value'), language)}"
                f"  -  {_money(currency, acm.get('suggested_max_value'), language)}"
            ),
            styles["AcmHeroRange"],
        ),
    ]
    inner = Table([[left, right]], colWidths=[usable_width * 0.48, usable_width * 0.48])
    inner.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("BOX", (0, 0), (-1, -1), 0, SOFT),
                ("LINEBEFORE", (1, 0), (1, 0), 0.4, LINE),
            ]
        )
    )
    inner.hAlign = "LEFT"
    return inner


def _summary(styles, view, language):
    facts = view.get("facts") or {}
    explained = ((view.get("ai_explanation") or {}).get("text") or view["acm"].get("explanation") or "").strip()
    if explained:
        text = explained
    elif facts.get("used") and facts.get("zone"):
        text = translate("acm_pdf_intro_facts", language=language, n=facts["used"], zone=facts["zone"])
    else:
        text = translate("acm_pdf_intro", language=language)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    compact = " ".join(lines)
    if len(compact) > 520:
        compact = compact[:517].rsplit(" ", 1)[0] + "..."
    return Paragraph(_escape(compact), styles["AcmBody"])


def _comp_title(row):
    return row.get("external_reference") or row.get("snapshot_location") or row.get("address") or "-"


def _comp_zone(row):
    location = row.get("location") or {}
    if isinstance(location, dict):
        return " · ".join(part for part in (location.get("primary"), location.get("secondary")) if part)
    return row.get("snapshot_location") or ""


def _is_closing(row):
    kind = row.get("snapshot_price_kind") or row.get("price_kind") or ""
    return kind == PRICE_CLOSING or row.get("source_type") == "closed_operation"


def _comparable_card(styles, row, language, width, currency, cache, number):
    photo = pdf_image_flowable(
        _resolve_pdf_image(row.get("photo_url") or row.get("photo_path"), cache),
        width - 4,
        28 * mm,
        mode="cover",
    )
    source = _public_source(row)
    area = row.get("display_area") or row.get("snapshot_covered_area") or row.get("snapshot_total_area")
    bits = [
        Paragraph(f'<font size="7"><b>{int(number)}</b></font>  {_escape(_comp_title(row))}', styles["AcmCompTitle"]),
        Paragraph(_escape(_comp_zone(row) or " "), styles["AcmSmall"]),
        Paragraph(
            _escape(_money(row.get("snapshot_currency") or currency, row.get("snapshot_price"), language)),
            styles["AcmSmallNavy"],
        ),
        Paragraph(
            _escape(
                " · ".join(
                    part
                    for part in (
                        f"{_area_label(area)} m2" if area not in (None, "") else "",
                        _money(row.get("snapshot_currency") or currency, row.get("snapshot_price_per_m2"), language) + "/m2"
                        if row.get("snapshot_price_per_m2") not in (None, "")
                        else "",
                        row.get("distance_label") or "",
                    )
                    if part
                )
            ),
            styles["AcmSmall"],
        ),
    ]
    if source:
        bits.append(Paragraph(_escape(source), styles["AcmSmall"]))
    body = bits if photo is None else [photo, Spacer(1, 1.4 * mm)] + bits
    table = Table([[body]], colWidths=[width])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), WHITE),
                ("BOX", (0, 0), (-1, -1), 0.4, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


def _comparables_row(styles, rows, language, usable_width, currency, cache):
    featured = list(rows[:4])
    if not featured:
        return None
    gap = 2 * mm
    width = (usable_width - gap * (len(featured) - 1)) / len(featured)
    cards = [
        _comparable_card(styles, row, language, width, currency, cache, index + 1)
        for index, row in enumerate(featured)
    ]
    table = Table([cards], colWidths=[width] * len(featured))
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


def _chart_items(view, language, currency):
    subject = view.get("subject") or {}
    acm = view["acm"]
    items = []
    selected = [row for row in (view.get("comparables") or []) if row.get("selected")]
    selected.sort(key=lambda row: int(row.get("score_int") or 0), reverse=True)
    for index, row in enumerate(selected[:4], start=1):
        ppm2 = row.get("snapshot_price_per_m2")
        if ppm2 in (None, ""):
            continue
        try:
            value = float(ppm2)
        except (TypeError, ValueError):
            continue
        items.append(
            {
                "label": translate("acm_chart_comp_n", language=language, n=index),
                "value": value,
                "display": _money(row.get("snapshot_currency") or currency, ppm2, language).replace(
                    f"{row.get('snapshot_currency') or currency} ", ""
                ),
                "color": ELECTRIC,
            }
        )
    area = display_area(subject)
    subject_ppm2 = price_per_m2(acm.get("estimated_value"), area)
    if subject_ppm2 is None:
        raw = view.get("reference_ppm2") or view.get("subject_ppm2")
        try:
            subject_ppm2 = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            subject_ppm2 = None
    if subject_ppm2 is not None:
        items.append(
            {
                "label": translate("acm_chart_reference", language=language),
                "value": float(subject_ppm2),
                "display": _money(currency, subject_ppm2, language).replace(f"{currency} ", ""),
                "color": NAVY,
            }
        )
    return items


def _compare_table(styles, view, language, usable_width, currency):
    subject = view.get("subject") or {}
    acm = view["acm"]
    area = display_area(subject)
    subject_ppm2 = price_per_m2(acm.get("estimated_value"), area)
    headers = [
        translate("acm_col_property", language=language),
        translate("acm_col_source", language=language),
        translate("acm_col_price", language=language),
        translate("acm_col_area", language=language),
        translate("acm_col_ppm2", language=language),
        translate("acm_col_rooms", language=language),
        translate("acm_col_similarity", language=language),
    ]
    data = [[Paragraph(_escape(item), styles["AcmTableHead"]) for item in headers]]
    subject_name = subject.get("address") or translate("acm_pdf_subject", language=language)
    data.append(
        [
            Paragraph(
                f'{_escape(subject_name)}<br/><font color="#5b6b7c" size="6">{_escape(translate("acm_pdf_subject_row", language=language))}</font>',
                styles["AcmTableCellBold"],
            ),
            Paragraph("-", styles["AcmTableCell"]),
            Paragraph(_escape(_money(currency, acm.get("estimated_value"), language)), styles["AcmTableCellBold"]),
            Paragraph(_escape(_area_label(area)), styles["AcmTableCell"]),
            Paragraph(_escape(_money(currency, subject_ppm2, language)), styles["AcmTableCell"]),
            Paragraph(_escape(str(subject.get("rooms") or "-")), styles["AcmTableCell"]),
            Paragraph("-", styles["AcmTableCell"]),
        ]
    )
    selected = [row for row in (view.get("comparables") or []) if row.get("selected")]
    selected.sort(key=lambda row: int(row.get("score_int") or 0), reverse=True)
    for row in selected[:8]:
        source = _public_source(row) or translate(
            "acm_price_closing" if _is_closing(row) else "acm_price_listing",
            language=language,
        )
        data.append(
            [
                Paragraph(_escape(_comp_title(row)), styles["AcmTableCell"]),
                Paragraph(_escape(source), styles["AcmTableCell"]),
                Paragraph(
                    _escape(_money(row.get("snapshot_currency") or currency, row.get("snapshot_price"), language)),
                    styles["AcmTableCell"],
                ),
                Paragraph(
                    _escape(_area_label(row.get("display_area") or row.get("snapshot_covered_area"))),
                    styles["AcmTableCell"],
                ),
                Paragraph(
                    _escape(
                        _money(
                            row.get("snapshot_currency") or currency,
                            row.get("snapshot_price_per_m2"),
                            language,
                        )
                    ),
                    styles["AcmTableCell"],
                ),
                Paragraph(_escape(str(row.get("snapshot_rooms") or "-")), styles["AcmTableCell"]),
                Paragraph(f'{int(row.get("score_int") or 0)}%', styles["AcmTableCellBold"]),
            ]
        )
    widths = [
        usable_width * 0.24,
        usable_width * 0.13,
        usable_width * 0.16,
        usable_width * 0.12,
        usable_width * 0.15,
        usable_width * 0.10,
        usable_width * 0.10,
    ]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), SOFT),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#e8f1fb")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.4, LINE),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


def _scenarios_block(styles, view, language, usable_width, currency):
    scenarios = (view.get("metrics") or {}).get("scenarios") or {}
    if not scenarios:
        return None
    cards = []
    width = usable_width / 3
    for key, label_key in (
        ("agile", "acm_scenario_agile"),
        ("market", "acm_scenario_market"),
        ("aspirational", "acm_scenario_aspirational"),
    ):
        inner = [
            Paragraph(_escape(translate(label_key, language=language)), styles["AcmCardLabel"]),
            Spacer(1, 1.4 * mm),
            Paragraph(_escape(_money(currency, scenarios.get(key), language)), styles["AcmHeroRange"]),
        ]
        card = Table([[inner]], colWidths=[width - 3 * mm])
        card.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        cards.append(card)
    table = Table([cards], colWidths=[width] * 3)
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    table.hAlign = "LEFT"
    return table


def _attach_agent_photo(contact):
    if not contact or contact.get("photo_path"):
        return contact
    if not contact.get("has_photo"):
        return contact
    from modules.agent_photo import resolve_agent_photo_path
    from modules.database.agents_repository import get_agent_record

    agent = get_agent_record(contact.get("agent_id"), contact.get("organization_id"))
    photo = resolve_agent_photo_path(agent)
    if photo:
        contact["photo_path"] = str(photo)
    return contact


def _draw_disclaimer(canvas, brand, website, disclaimer, y=8 * mm):
    width, _height = A4
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 6.2)
    text = (disclaimer or "")[:220]
    canvas.drawString(12 * mm, y, text[:140])
    if len(text) > 140:
        canvas.drawString(12 * mm, y - 8, text[140:])
    canvas.setFillColor(NAVY)
    canvas.setFont("Helvetica-Bold", 7)
    canvas.drawRightString(width - 12 * mm, y + 9, (brand or "JRH One")[:36])
    if website:
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(width - 12 * mm, y, website)


def _draw_cover_footer(canvas, contact, include_agent, language, brand, org_logo, website, disclaimer):
    width, _height = A4
    band_h = 52 * mm
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, width, band_h, fill=1, stroke=0)
    canvas.setFillColor(ELECTRIC)
    canvas.rect(0, band_h - 2.2, width, 2.2, fill=1, stroke=0)
    x = 14 * mm
    if include_agent and contact:
        photo = None
        if contact.get("photo_path"):
            photo = compose_on_color(
                contact["photo_path"],
                NAVY_RGB,
                max_width_px=420,
                max_height_px=520,
            )
        if photo:
            try:
                from reportlab.lib.utils import ImageReader

                canvas.drawImage(
                    ImageReader(photo["buffer"]),
                    x,
                    8 * mm,
                    width=28 * mm,
                    height=36 * mm,
                    mask="auto",
                    preserveAspectRatio=True,
                    anchor="sw",
                )
                x += 32 * mm
            except Exception:
                pass
        canvas.setFillColor(ELECTRIC)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(x, 40 * mm, translate("acm_pdf_ally", language=language).upper())
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawString(x, 33 * mm, str(contact.get("name") or "")[:42])
        canvas.setFont("Helvetica", 8)
        canvas.drawString(x, 27 * mm, str(contact.get("title") or translate("acm_agent_role", language=language))[:46])
        y = 21 * mm
        canvas.setFont("Helvetica", 7.5)
        for key in ("phone", "email", "instagram", "linkedin", "location"):
            value = contact.get(key)
            if not value:
                continue
            canvas.drawString(x, y, str(value)[:48])
            y -= 8
    else:
        org_mark = pdf_image_flowable(
            org_logo or resolve_brand_logo_path(),
            28 * mm,
            10 * mm,
            mode="contain",
            background=NAVY_RGB,
            preserve_alpha=True,
        )
        if org_logo:
            composed = compose_on_color(org_logo, NAVY_RGB, max_width_px=360, max_height_px=120)
            if composed:
                from reportlab.lib.utils import ImageReader

                canvas.drawImage(
                    ImageReader(composed["buffer"]),
                    x,
                    28 * mm,
                    width=32 * mm,
                    height=12 * mm,
                    mask="auto",
                    preserveAspectRatio=True,
                    anchor="sw",
                )
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(x, 22 * mm, (brand or "JRH One")[:40])
        canvas.setFont("Helvetica", 8)
        canvas.drawString(x, 15 * mm, translate("acm_pdf_brand_role", language=language)[:48])
    canvas.setFillColor(colors.HexColor("#C5D0DC"))
    canvas.setFont("Helvetica", 6)
    wrapped = (disclaimer or "")[:180]
    canvas.drawString(12 * mm, 5.5 * mm, wrapped[:110])
    if website:
        canvas.drawRightString(width - 12 * mm, 5.5 * mm, website)
    canvas.restoreState()


def build_acm_pdf(
    view,
    *,
    include_agent=True,
    language="es",
    brand_name="",
    logo_path=None,
    compress=True,
    website=None,
):
    styles = _styles()
    buffer = io.BytesIO()
    language = language if language in ("es", "en") else "es"
    brand = brand_name or "JRH One"
    domain = website or f"www.{get_app_domain()}"
    disclaimer = translate("acm_disclaimer", language=language)
    page_w, page_h = A4
    left = right = 12 * mm
    usable_width = page_w - left - right
    acm = view["acm"]
    currency = acm.get("currency") or (view.get("subject") or {}).get("listing_currency") or "USD"
    contact = dict(view.get("agent_contact") or {}) if include_agent else {}
    if include_agent:
        contact = _attach_agent_photo(contact) or {}
        view = dict(view)
        view["agent_contact"] = contact
    generated = _short_date(
        acm.get("finalized_at") or acm.get("updated_at") or acm.get("created_at"),
        language,
    )
    selected = [row for row in (view.get("comparables") or []) if row.get("selected")]
    selected.sort(key=lambda row: int(row.get("score_int") or 0), reverse=True)
    cache = view.setdefault("_pdf_image_cache", {})

    def on_first(canvas, doc):
        _draw_cover_footer(
            canvas,
            contact,
            include_agent,
            language,
            brand,
            logo_path,
            domain,
            disclaimer,
        )

    def on_later(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.4)
        canvas.line(12 * mm, 14 * mm, page_w - 12 * mm, 14 * mm)
        _draw_disclaimer(canvas, brand, domain, disclaimer)
        canvas.restoreState()

    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        title=translate("acm_pdf_cover", language=language),
    )
    if not compress:
        doc.compress = 0
    first_frame = Frame(left, 56 * mm, usable_width, page_h - 14 * mm - 56 * mm, id="cover")
    later_frame = Frame(left, 18 * mm, usable_width, page_h - 14 * mm - 18 * mm, id="rest")
    doc.addPageTemplates(
        [
            PageTemplate(id="Cover", frames=[first_frame], onPage=on_first),
            PageTemplate(id="Rest", frames=[later_frame], onPage=on_later),
        ]
    )

    story = [NextPageTemplate("Rest")]
    story.append(_header(styles, usable_width, language, generated))
    story.append(Spacer(1, 4 * mm))
    story.append(_hero(styles, view, language, usable_width))
    story.append(Spacer(1, 4 * mm))
    story.append(_value_hero(styles, view, language, usable_width))
    story.append(Spacer(1, 3 * mm))
    story.append(_summary(styles, view, language))
    if selected:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(_escape(translate("acm_pdf_comps_title", language=language)), styles["AcmSection"]))
        comps = _comparables_row(styles, selected, language, usable_width, currency, cache)
        if comps is not None:
            story.append(comps)

    story.append(PageBreak())
    chart_items = _chart_items(view, language, currency)
    if len(chart_items) >= 2:
        story.append(Paragraph(_escape(translate("acm_pdf_ppm2_title", language=language)), styles["AcmSection"]))
        story.append(Spacer(1, 1.5 * mm))
        story.append(Ppm2Chart(chart_items, usable_width))
        story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(_escape(translate("acm_compare_table", language=language)), styles["AcmSection"]))
    story.append(_compare_table(styles, view, language, usable_width, currency))
    scenarios = _scenarios_block(styles, view, language, usable_width, currency)
    if scenarios is not None:
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph(_escape(translate("acm_tab_scenarios", language=language)), styles["AcmSection"]))
        story.append(scenarios)
    if (view.get("map") or {}).get("available"):
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(_escape(translate("acm_see_on_map", language=language)), styles["AcmSection"]))
        story.append(Paragraph(_escape(translate("acm_map_pdf_note", language=language)), styles["AcmBody"]))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(_escape(translate("acm_pdf_methodology", language=language)), styles["AcmSection"]))
    story.append(Paragraph(_escape(translate("acm_pdf_methodology_body", language=language)), styles["AcmBody"]))

    doc.build(story)
    buffer.seek(0)
    return buffer


def generate_acm_pdf_bytes(view, *, include_agent=True, language="es"):
    from modules.database.organization_settings_repository import get_organization_settings

    org_id = view["acm"]["organization_id"]
    settings = get_organization_settings(org_id) or {}
    brand = settings.get("display_name") or "JRH One"
    logo = _brand_logo_path(settings.get("logo_path"))
    official = _official_logo_path()
    try:
        if logo and Path(logo).stat().st_size > 400_000:
            logo = official
    except OSError:
        logo = official
    return build_acm_pdf(
        view,
        include_agent=include_agent,
        language=language,
        brand_name=brand,
        logo_path=logo or official,
        website=f"www.{get_app_domain()}",
    )
