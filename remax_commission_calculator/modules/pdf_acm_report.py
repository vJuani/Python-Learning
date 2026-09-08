"""Commercial one-page ACM PDF. Branding from organization. No invented numbers."""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
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

from modules.acm_engine import PRICE_CLOSING, display_area, price_per_m2
from modules.branding import get_app_domain
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
    _image_flowable,
)


GREEN = colors.HexColor("#1b7a3a")
GREEN_SOFT = colors.HexColor("#e3f6ea")
PURPLE = colors.HexColor("#5b3a9a")
PURPLE_SOFT = colors.HexColor("#f3eef8")
AMBER = colors.HexColor("#8a6d1d")
AMBER_SOFT = colors.HexColor("#fff4d6")
NAVY_DEEP = colors.HexColor("#0a2a4a")
STEEL = colors.HexColor("#33415c")
CHART_BAR = colors.HexColor("#4f8fd4")


def _escape(value):
    text = str(value or "").replace("—", "-").replace("–", "-")
    return (
        text.encode("latin-1", "replace").decode("latin-1")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _money(currency, value, language="es"):
    if value in (None, ""):
        return "-"
    return format_money(value, currency=currency or "USD", language=language)


def _ppm2_label(currency, value, language="es"):
    if value in (None, ""):
        return "—"
    return f"{_money(currency, value, language).replace(f'{currency} ', '')}"


def _area_label(value):
    if value in (None, ""):
        return "—"
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
    styles.add(
        ParagraphStyle(
            name="AcmBrand",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=NAVY,
            leading=13,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmBrandSub",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            textColor=MUTED,
            leading=9,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmKicker",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            textColor=ACCENT,
            leading=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=20,
            textColor=NAVY,
            leading=23,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmZone",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=11,
            textColor=MUTED,
            leading=14,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmMeta",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=STEEL,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmBody",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            textColor=NAVY,
            leading=11.5,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmSection",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=NAVY,
            spaceBefore=2,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmCardLabel",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7,
            textColor=MUTED,
            leading=9,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmCardLabelLight",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7,
            textColor=colors.HexColor("#c5d7ea"),
            leading=9,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmCardValue",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=NAVY,
            leading=13,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmHeroValue",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=16,
            textColor=WHITE,
            leading=19,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmHeroSub",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            textColor=colors.HexColor("#d7e6f5"),
            leading=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmCompTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=NAVY,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmSmall",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            textColor=MUTED,
            leading=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmSmallNavy",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            textColor=NAVY,
            leading=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmBadge",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7,
            textColor=NAVY,
            alignment=TA_CENTER,
            leading=9,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmTableHead",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=6.5,
            textColor=STEEL,
            leading=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmTableCell",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7,
            textColor=NAVY,
            leading=9,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmTableCellBold",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7,
            textColor=NAVY,
            leading=9,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmAgentName",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=NAVY,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmFooter",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7,
            textColor=MUTED,
            alignment=TA_CENTER,
            leading=9,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AcmRightMuted",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=MUTED,
            alignment=TA_RIGHT,
            leading=10,
        )
    )
    return styles


def _card_table(inner, width, *, bg=SOFT, border=LINE, padding=7, accent=None):
    table = Table([[inner]], colWidths=[width])
    commands = [
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.4, border),
        ("LEFTPADDING", (0, 0), (-1, -1), padding),
        ("RIGHTPADDING", (0, 0), (-1, -1), padding),
        ("TOPPADDING", (0, 0), (-1, -1), padding),
        ("BOTTOMPADDING", (0, 0), (-1, -1), padding),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    if accent is not None:
        commands.append(("LINEABOVE", (0, 0), (-1, 0), 2.4, accent))
    table.setStyle(TableStyle(commands))
    table.hAlign = "LEFT"
    return table


def _badge(text, styles, *, fg=NAVY, bg=SOFT):
    table = Table(
        [[Paragraph(_escape(text), styles["AcmBadge"])]],
        colWidths=[28 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("TEXTCOLOR", (0, 0), (-1, -1), fg),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


class SimilarityBar(Flowable):
    def __init__(self, percent, width, color=ACCENT):
        super().__init__()
        self.percent = max(0, min(100, int(percent or 0)))
        self.box_width = width
        self.color = color
        self.height = 7

    def wrap(self, avail_width, avail_height):
        self.width = min(self.box_width, avail_width)
        return self.width, self.height

    def draw(self):
        self.canv.setFillColor(SOFT)
        self.canv.roundRect(0, 0, self.width, self.height, 3, fill=1, stroke=0)
        filled = max(4, self.width * self.percent / 100.0)
        self.canv.setFillColor(self.color)
        self.canv.roundRect(0, 0, filled, self.height, 3, fill=1, stroke=0)


class Ppm2Chart(Flowable):
    def __init__(self, items, width, height=42 * mm):
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
        gap = 6
        usable = self.width - 8
        bar_width = max(10, (usable - gap * (count - 1)) / count)
        baseline = 16
        top = self.height - 12
        chart_h = max(10, top - baseline)
        self.canv.setStrokeColor(LINE)
        self.canv.setLineWidth(0.4)
        self.canv.line(0, baseline, self.width, baseline)
        for index, item in enumerate(self.items):
            x = 4 + index * (bar_width + gap)
            bar_h = chart_h * (item["value"] / peak)
            self.canv.setFillColor(item.get("color") or CHART_BAR)
            self.canv.roundRect(x, baseline, bar_width, bar_h, 2, fill=1, stroke=0)
            self.canv.setFillColor(NAVY)
            self.canv.setFont("Helvetica-Bold", 6.5)
            label = str(item.get("display") or int(round(item["value"])))
            self.canv.drawCentredString(x + bar_width / 2, baseline + bar_h + 2, label)
            self.canv.setFillColor(MUTED)
            self.canv.setFont("Helvetica", 6)
            name = str(item.get("label") or "").strip()
            parts = name.rsplit(" ", 1)
            if len(name) > 16 and len(parts) == 2:
                self.canv.drawCentredString(x + bar_width / 2, 8, parts[0][:16])
                self.canv.drawCentredString(x + bar_width / 2, 1, parts[1][:10])
            else:
                self.canv.drawCentredString(x + bar_width / 2, 4, name[:18])


def _collect_photos(view):
    found = []
    seen = set()
    candidates = []
    for key in ("photos", "gallery"):
        items = view.get(key) or []
        if isinstance(items, (list, tuple)):
            candidates.extend(items)
    for key in ("photo_path", "photo_url", "hero_image"):
        if view.get(key):
            candidates.append(view[key])
    for blob in (view.get("subject") or {}, view.get("property") or {}):
        for key in ("photo_path", "photo_url", "hero_image"):
            if blob.get(key):
                candidates.append(blob[key])
        images = blob.get("images") or blob.get("gallery") or []
        if isinstance(images, str):
            images = [images]
        if isinstance(images, (list, tuple)):
            candidates.extend(images)
    for item in candidates:
        if not item:
            continue
        path = Path(str(item))
        key = str(path.resolve()) if path.exists() else str(item)
        if key in seen:
            continue
        if path.is_file():
            seen.add(key)
            found.append(path)
        if len(found) >= 3:
            break
    return found


def _header(styles, usable_width, brand_name, logo_path, language):
    brand = _escape(brand_name or "JRH One")
    role = _escape(translate("acm_pdf_brand_role", language=language))
    logo = _image_flowable(logo_path, 28 * mm, 10 * mm)
    identity = [
        Paragraph(f"{brand}  |  {role}", styles["AcmBrand"]),
    ]
    left = identity
    if logo is not None:
        left = [logo, Spacer(1, 1.2 * mm)] + identity
    left_table = Table([[cell] for cell in left], colWidths=[usable_width * 0.62])
    left_table.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    table = Table([[left_table, ""]], colWidths=[usable_width * 0.62, usable_width * 0.38])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


def _photo_block(photos, width, height=42 * mm):
    if not photos:
        return None
    if len(photos) == 1:
        image = _image_flowable(photos[0], width, height)
        return image
    hero_w = width * 0.66
    side_w = width * 0.32
    hero = _image_flowable(photos[0], hero_w, height)
    extras = []
    extra_h = (height - 2 * mm) / 2 if len(photos) > 2 else height
    for path in photos[1:3]:
        extras.append(_image_flowable(path, side_w, extra_h) or "")
    if not extras:
        return hero
    side = Table([[item] for item in extras], colWidths=[side_w])
    side.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    table = Table([[hero or "", side]], colWidths=[hero_w, side_w + 2 * mm])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _title_block(styles, view, language, usable_width):
    acm = view["acm"]
    subject = view.get("subject") or {}
    facts = view.get("facts") or {}
    address = _escape(subject.get("address") or "")
    loc = " · ".join(
        part for part in (subject.get("neighborhood"), subject.get("jurisdiction")) if part
    )
    date_label = _short_date(
        acm.get("finalized_at") or acm.get("updated_at") or acm.get("created_at"),
        language,
    )
    copy = []
    copy.append(
        Paragraph(
            _escape(translate("acm_pdf_cover", language=language)).upper(),
            styles["AcmKicker"],
        )
    )
    copy.append(Paragraph(address, styles["AcmTitle"]))
    if loc:
        copy.append(Paragraph(_escape(loc), styles["AcmZone"]))
    chips = []
    if date_label:
        chips.append(_badge(date_label, styles, fg=STEEL, bg=SOFT))
    if acm.get("status") != "finalized":
        chips.append(
            _badge(
                translate("acm_status_draft", language=language),
                styles,
                fg=AMBER,
                bg=AMBER_SOFT,
            )
        )
    if chips:
        copy.append(Spacer(1, 1.6 * mm))
        chip_row = Table([chips], colWidths=[28 * mm] * len(chips))
        chip_row.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        chip_row.hAlign = "LEFT"
        copy.append(chip_row)
    zone = facts.get("zone") or loc
    used = facts.get("used") or 0
    if used and zone:
        intro = translate("acm_pdf_intro_facts", language=language, n=used, zone=zone)
    else:
        intro = translate("acm_pdf_intro", language=language)
    copy.append(Spacer(1, 2 * mm))
    copy.append(Paragraph(_escape(intro), styles["AcmBody"]))

    photos = _collect_photos(view)
    photo = _photo_block(photos, usable_width * 0.46)
    if photo is None:
        return KeepTogether(copy)

    left = Table([[item] for item in copy], colWidths=[usable_width * 0.52])
    left.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    left.hAlign = "LEFT"
    table = Table([[left, photo]], colWidths=[usable_width * 0.52, usable_width * 0.48])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


def _valuation_row(styles, view, language, usable_width):
    acm = view["acm"]
    metrics = view.get("metrics") or {}
    scenarios = metrics.get("scenarios") or {}
    currency = acm.get("currency") or (view.get("subject") or {}).get("listing_currency") or "USD"
    estimated = _money(currency, acm.get("estimated_value"), language)
    range_text = (
        f"{_money(currency, acm.get('suggested_min_value'), language)}"
        f"  —  {_money(currency, acm.get('suggested_max_value'), language)}"
    )
    hero_inner = [
        Paragraph(_escape(translate("acm_reference_value", language=language)), styles["AcmCardLabelLight"]),
        Spacer(1, 1.2 * mm),
        Paragraph(_escape(estimated), styles["AcmHeroValue"]),
        Spacer(1, 1 * mm),
        Paragraph(_escape(range_text), styles["AcmHeroSub"]),
    ]
    hero = _card_table(
        hero_inner,
        usable_width * 0.34,
        bg=NAVY_DEEP,
        border=NAVY_DEEP,
        padding=8,
        accent=ACCENT,
    )
    cards = [
        (
            translate("acm_scenario_agile_short", language=language),
            _money(currency, scenarios.get("agile"), language),
            GREEN_SOFT,
            "#1b7a3a",
        ),
        (
            translate("acm_scenario_market", language=language),
            _money(currency, scenarios.get("market") or acm.get("estimated_value"), language),
            SOFT,
            "#0860c8",
        ),
        (
            translate("acm_scenario_aspirational", language=language),
            _money(currency, scenarios.get("aspirational"), language),
            PURPLE_SOFT,
            "#5b3a9a",
        ),
    ]
    side_width = usable_width * 0.21
    side = []
    for label, value, bg, accent in cards:
        inner = [
            Paragraph(_escape(label), styles["AcmCardLabel"]),
            Spacer(1, 1.4 * mm),
            Paragraph(
                f'<font color="{accent}"><b>{_escape(value)}</b></font>',
                styles["AcmCardValue"],
            ),
        ]
        side.append(
            _card_table(inner, side_width, bg=bg, border=LINE, padding=7, accent=colors.HexColor(accent))
        )
    table = Table([[hero] + side], colWidths=[usable_width * 0.35, side_width, side_width, side_width])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1.5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1.5),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


def _comp_title(row):
    return (
        row.get("external_reference")
        or row.get("snapshot_location")
        or row.get("address")
        or "—"
    )


def _comp_zone(row):
    location = row.get("location") or {}
    if isinstance(location, dict):
        parts = [location.get("primary"), location.get("secondary")]
        return " · ".join(part for part in parts if part)
    return row.get("snapshot_location") or ""


def _is_closing(row):
    kind = row.get("snapshot_price_kind") or row.get("price_kind") or ""
    return kind == PRICE_CLOSING or row.get("source_type") == "closed_operation"


def _comparable_card(styles, row, language, width, currency):
    score = int(row.get("score_int") or 0)
    very = score >= 80
    source = translate(
        "acm_price_closing" if _is_closing(row) else "acm_price_listing",
        language=language,
    )
    badge_bg = GREEN_SOFT if _is_closing(row) else SOFT
    badge_fg = GREEN if _is_closing(row) else ACCENT
    quality = translate(
        "acm_pdf_very_comparable" if very else "acm_pdf_comparable_badge",
        language=language,
    )
    area = row.get("display_area") or row.get("snapshot_covered_area") or row.get("snapshot_total_area")
    rooms = row.get("snapshot_rooms")
    metrics = [
        (
            translate("acm_col_price", language=language),
            _money(row.get("snapshot_currency") or currency, row.get("snapshot_price"), language),
        ),
        (translate("acm_col_area", language=language), f"{_area_label(area)} m²" if area not in (None, "") else "—"),
        (
            translate("acm_col_ppm2", language=language),
            f"{_money(row.get('snapshot_currency') or currency, row.get('snapshot_price_per_m2'), language)}",
        ),
        (
            translate("acm_col_rooms", language=language),
            str(rooms) if rooms not in (None, "") else "—",
        ),
    ]
    metric_cells = [
        Paragraph(
            f'<font color="#5b6b7c" size="6">{_escape(label)}</font><br/>'
            f'<font color="#001838" size="7.5"><b>{_escape(value)}</b></font>',
            styles["AcmSmallNavy"],
        )
        for label, value in metrics
    ]
    metric_table = Table([metric_cells], colWidths=[width / 4] * 4)
    metric_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    inner = [
        Paragraph(_escape(_comp_title(row)), styles["AcmCompTitle"]),
        Paragraph(_escape(_comp_zone(row) or " "), styles["AcmSmall"]),
        Spacer(1, 1.2 * mm),
        _badge(source, styles, fg=badge_fg, bg=badge_bg),
        Spacer(1, 2 * mm),
        metric_table,
        Spacer(1, 2 * mm),
        Paragraph(
            f'{_escape(translate("acm_col_similarity", language=language))}  <b>{score}%</b>',
            styles["AcmSmallNavy"],
        ),
        Spacer(1, 1 * mm),
        SimilarityBar(score, width - 10 * mm, GREEN if very else ACCENT),
        Spacer(1, 1.2 * mm),
        Paragraph(_escape(quality), styles["AcmSmall"]),
    ]
    return _card_table(inner, width, bg=WHITE, border=LINE, padding=6)


def _comparables_row(styles, rows, language, usable_width, currency):
    featured = list(rows[:3])
    if not featured:
        return None
    gap = 2 * mm
    width = (usable_width - gap * (len(featured) - 1)) / len(featured)
    cards = [_comparable_card(styles, row, language, width, currency) for row in featured]
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
    area = display_area(subject)
    subject_ppm2 = price_per_m2(acm.get("estimated_value"), area)
    if subject_ppm2 is None:
        raw = view.get("subject_ppm2")
        try:
            subject_ppm2 = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            subject_ppm2 = None
    if subject_ppm2 is not None:
        items.append(
            {
                "label": (subject.get("address") or translate("acm_pdf_subject_row", language=language))[:16],
                "value": float(subject_ppm2),
                "display": _ppm2_label(currency, subject_ppm2, language),
                "color": NAVY,
            }
        )
    selected = [row for row in (view.get("comparables") or []) if row.get("selected")]
    selected.sort(key=lambda row: int(row.get("score_int") or 0), reverse=True)
    for row in selected[:3]:
        ppm2 = row.get("snapshot_price_per_m2")
        if ppm2 in (None, ""):
            continue
        try:
            value = float(ppm2)
        except (TypeError, ValueError):
            continue
        items.append(
            {
                "label": _comp_title(row)[:16],
                "value": value,
                "display": _ppm2_label(row.get("snapshot_currency") or currency, ppm2, language),
                "color": CHART_BAR,
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
            Paragraph("—", styles["AcmTableCell"]),
            Paragraph(_escape(_money(currency, acm.get("estimated_value"), language)), styles["AcmTableCellBold"]),
            Paragraph(_escape(_area_label(area)), styles["AcmTableCell"]),
            Paragraph(_escape(_money(currency, subject_ppm2, language)), styles["AcmTableCell"]),
            Paragraph(_escape(str(subject.get("rooms") or "—")), styles["AcmTableCell"]),
            Paragraph("—", styles["AcmTableCell"]),
        ]
    )
    selected = [row for row in (view.get("comparables") or []) if row.get("selected")]
    selected.sort(key=lambda row: int(row.get("score_int") or 0), reverse=True)
    for row in selected[:5]:
        source = translate(
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
                Paragraph(_escape(str(row.get("snapshot_rooms") or "—")), styles["AcmTableCell"]),
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
    style_cmds = [
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
    table.setStyle(TableStyle(style_cmds))
    table.hAlign = "LEFT"
    return table


def _agent_card(styles, contact, language, width):
    if not contact or not any(contact.get(key) for key in ("name", "phone", "email")):
        return None
    lines = []
    photo = _image_flowable(contact.get("photo_path") or contact.get("photo"), 16 * mm, 16 * mm)
    identity = [
        Paragraph(_escape(contact.get("name") or ""), styles["AcmAgentName"]),
        Paragraph(_escape(translate("acm_agent_role", language=language)), styles["AcmSmall"]),
    ]
    if contact.get("email"):
        identity.append(Paragraph(_escape(contact["email"]), styles["AcmSmallNavy"]))
    if contact.get("phone"):
        identity.append(Paragraph(_escape(contact["phone"]), styles["AcmSmallNavy"]))
        identity.append(
            Paragraph(
                f'{_escape(translate("acm_pdf_whatsapp", language=language))}  {_escape(contact["phone"])}',
                styles["AcmSmallNavy"],
            )
        )
    if photo is None:
        inner = identity
    else:
        stacked = Table([[item] for item in identity], colWidths=[width - 22 * mm])
        stacked.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        pair = Table([[photo, stacked]], colWidths=[18 * mm, width - 22 * mm])
        pair.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        inner = [pair]
    return _card_table(inner, width, bg=SOFT, border=LINE, padding=7)


def _conclusion_block(styles, view, include_agent, language, usable_width):
    explained = (view.get("ai_explanation") or {}).get("text") or view["acm"].get("explanation")
    highlights = view.get("market_highlights") or []
    left = [
        Paragraph(_escape(translate("acm_pdf_insights", language=language)), styles["AcmSection"]),
    ]
    if explained:
        left.append(Paragraph(_escape(explained), styles["AcmBody"]))
    if highlights:
        left.append(Spacer(1, 2 * mm))
        left.append(
            _card_table(
                Paragraph(_escape(highlights[0]), styles["AcmSmallNavy"]),
                usable_width * (0.62 if include_agent else 1),
                bg=AMBER_SOFT,
                border=colors.HexColor("#ead9a8"),
                padding=6,
            )
        )
    left_table = Table([[item] for item in left], colWidths=[usable_width * (0.62 if include_agent else 1)])
    left_table.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    if not include_agent:
        return left_table
    agent = _agent_card(styles, view.get("agent_contact") or {}, language, usable_width * 0.36)
    if agent is None:
        return left_table
    table = Table([[left_table, agent]], colWidths=[usable_width * 0.62, usable_width * 0.38])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


def _make_footer(brand_name, website, disclaimer):
    def _draw(canvas, doc):
        width, _height = A4
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.4)
        canvas.line(11 * mm, 16 * mm, width - 11 * mm, 16 * mm)
        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.drawString(11 * mm, 11 * mm, (brand_name or "JRH One")[:42])
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7)
        if website:
            canvas.drawRightString(width - 11 * mm, 11 * mm, website)
        canvas.setFont("Helvetica", 6)
        text_object = canvas.beginText(11 * mm, 7.2 * mm)
        text_object.setFillColor(MUTED)
        wrapped = disclaimer or ""
        if len(wrapped) > 140:
            split_at = wrapped.rfind(" ", 0, 140)
            if split_at > 0:
                text_object.textLine(wrapped[:split_at])
                text_object.textLine(wrapped[split_at + 1 :])
            else:
                text_object.textLine(wrapped[:140])
        else:
            text_object.textLine(wrapped)
        canvas.drawText(text_object)
        canvas.restoreState()

    return _draw


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
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=11 * mm,
        rightMargin=11 * mm,
        topMargin=11 * mm,
        bottomMargin=18 * mm,
        title=translate("acm_pdf_cover", language=language),
    )
    if not compress:
        doc.compress = 0
    usable_width = A4[0] - (22 * mm)
    acm = view["acm"]
    currency = acm.get("currency") or (view.get("subject") or {}).get("listing_currency") or "USD"
    contact = view.get("agent_contact") or {} if include_agent else {}
    if include_agent:
        view = dict(view)
        view["agent_contact"] = contact
    selected = [row for row in (view.get("comparables") or []) if row.get("selected")]
    selected.sort(key=lambda row: int(row.get("score_int") or 0), reverse=True)

    story = []
    story.append(_header(styles, usable_width, brand, logo_path, language))
    story.append(Spacer(1, 4 * mm))
    story.append(_title_block(styles, view, language, usable_width))
    story.append(Spacer(1, 4 * mm))
    story.append(_valuation_row(styles, view, language, usable_width))

    if selected:
        story.append(Spacer(1, 5 * mm))
        story.append(
            Paragraph(
                _escape(translate("acm_pdf_comps_title", language=language)),
                styles["AcmSection"],
            )
        )
        comps = _comparables_row(styles, selected, language, usable_width, currency)
        if comps is not None:
            story.append(comps)

    chart_items = _chart_items(view, language, currency)
    if len(chart_items) >= 2:
        story.append(Spacer(1, 5 * mm))
        story.append(
            Paragraph(
                _escape(translate("acm_pdf_ppm2_title", language=language)),
                styles["AcmSection"],
            )
        )
        story.append(_card_table(Ppm2Chart(chart_items, usable_width - 8 * mm), usable_width, bg=WHITE, border=LINE, padding=6))

    story.append(Spacer(1, 5 * mm))
    story.append(
        Paragraph(
            _escape(translate("acm_compare_table", language=language)),
            styles["AcmSection"],
        )
    )
    story.append(_compare_table(styles, view, language, usable_width, currency))
    story.append(Spacer(1, 5 * mm))
    story.append(_conclusion_block(styles, view, include_agent, language, usable_width))

    footer = _make_footer(brand, domain, disclaimer)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
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
        website=f"www.{get_app_domain()}",
    )
