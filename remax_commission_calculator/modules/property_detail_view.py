"""Display helpers for the property detail page. No valuation or sync changes."""

from __future__ import annotations

import json
import re
from datetime import datetime

from modules.agent_branding import get_agent_branding
from modules.formatting import format_listing_money, format_money
from modules.i18n import translate
from modules.property_agent_actions import get_property_agent_actions
from modules.property_types import normalize_property_type


_POSTAL_RE = re.compile(r"\s*\([^)]+\)\s*")
_NUMERIC_RE = re.compile(r"^\d+$")
_COUNTRY_SKIP = {"argentina", "argentine republic", "ar"}
_UNIT_TOKEN_RE = re.compile(r"\b(?:piso|dpto|depto|dto\.?|uf|unidad)\b", re.I)
_STREET_TITLE_RE = re.compile(r"^(?P<title>.+?\d+[a-zA-Z]?)\s+(?P<rest>.+)$")


def parse_external_metadata(property_row):
    raw = (property_row or {}).get("external_metadata_json")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def compact_property_title(property_row):
    meta = parse_external_metadata(property_row)
    street = " ".join(
        part
        for part in (meta.get("street"), meta.get("street_number"))
        if part not in (None, "")
    ).strip()
    if street:
        return street
    address = str((property_row or {}).get("address") or "").strip()
    if not address:
        return ""
    first = _POSTAL_RE.sub("", address.split(",")[0]).strip()
    match = _STREET_TITLE_RE.match(first)
    if match and _UNIT_TOKEN_RE.search(match.group("rest")):
        return match.group("title").strip()
    return first


def compact_property_unit(property_row, language="es"):
    meta = parse_external_metadata(property_row)
    bits = []
    floor = str(meta.get("floor") or "").strip()
    apartment = str(meta.get("apartment") or "").strip()
    if floor:
        bits.append(
            floor
            if _UNIT_TOKEN_RE.search(floor)
            else translate("property_floor_n", language=language, n=floor)
        )
    if apartment:
        bits.append(
            apartment
            if _UNIT_TOKEN_RE.search(apartment)
            else translate("property_unit_n", language=language, n=apartment)
        )
    if bits:
        return " · ".join(bits)
    address = str((property_row or {}).get("address") or "").strip()
    first = _POSTAL_RE.sub("", address.split(",")[0] if address else "").strip()
    match = _STREET_TITLE_RE.match(first)
    if match and _UNIT_TOKEN_RE.search(match.group("rest")):
        rest = re.sub(r"\s+", " ", match.group("rest")).strip()
        rest = re.sub(r"\b(dpto|depto|dto)\.?\b", "Dpto", rest, flags=re.I)
        rest = re.sub(r"\bpiso\b", "Piso", rest, flags=re.I)
        parts = re.split(r"\s+(?=(?:Piso|Dpto|UF|Unidad)\b)", rest)
        return " · ".join(part.strip() for part in parts if part.strip())
    return ""


def compact_property_location(property_row):
    row = property_row or {}
    parts = []
    for value in (
        row.get("neighborhood"),
        row.get("locality"),
        row.get("administrative_area") or row.get("jurisdiction"),
    ):
        text = str(value or "").strip()
        if not text:
            continue
        folded = text.casefold()
        if folded in _COUNTRY_SKIP:
            continue
        if text not in parts:
            parts.append(text)
    return " · ".join(parts[:3])


def secondary_address_bits(property_row):
    row = property_row or {}
    bits = []
    if row.get("postal_code"):
        bits.append(str(row["postal_code"]))
    country = str(row.get("country") or "").strip()
    if country and country.casefold() not in _COUNTRY_SKIP:
        bits.append(country)
    return " · ".join(bits)


def public_source_badge(property_row, language="es"):
    source = str((property_row or {}).get("external_source") or "").strip().lower()
    if source == "redremax":
        return translate("acm_source_redremax", language=language)
    if source == "mock_network":
        return translate("property_source_imported", language=language)
    if source:
        return translate("property_source_imported", language=language)
    return translate("property_source_manual", language=language)


def display_type_label(property_row, language="es"):
    raw = normalize_property_type((property_row or {}).get("property_type"))
    if raw and raw != "other":
        return translate(f"property_type_{raw}", language=language)
    meta = parse_external_metadata(property_row)
    external = str(meta.get("property_type") or "").strip()
    if external and not _NUMERIC_RE.match(external) and external.casefold() not in {
        "residencial",
        "residential",
        "otro",
        "other",
    }:
        return external
    if raw:
        return translate(f"property_type_{raw}", language=language)
    return None


def extra_feature_chips(property_row, language="es"):
    labels = []
    seen = set()
    for label in (property_row or {}).get("feature_labels") or []:
        text = str(label or "").strip()
        if not text or _NUMERIC_RE.match(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        labels.append(text)
    meta = parse_external_metadata(property_row)
    extras = (
        ("apt_credit", "property_chip_credit"),
        ("in_private_community", "property_chip_private"),
        ("financing", "property_chip_financing"),
    )
    for key, i18n_key in extras:
        if meta.get(key) in (True, "true", "True", 1, "1"):
            text = translate(i18n_key, language=language)
            if text not in seen:
                seen.add(text)
                labels.append(text)
    return labels


def _number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def surface_rows(property_row, language="es"):
    row = property_row or {}
    meta = parse_external_metadata(row)
    dimensions = meta.get("dimensions") if isinstance(meta.get("dimensions"), dict) else {}
    rows = []
    covered = _number(row.get("covered_m2")) or _number(dimensions.get("covered"))
    land = _number(dimensions.get("land"))
    built = _number(dimensions.get("total_built"))
    uncovered = _number(dimensions.get("uncovered"))
    semicovered = _number(dimensions.get("semicovered"))
    total = _number(row.get("total_m2"))
    mapping = (
        (covered, "property_covered_m2"),
        (uncovered, "property_uncovered_m2"),
        (semicovered, "property_semicovered_m2"),
        (built, "property_built_m2"),
        (land, "property_land_m2"),
    )
    used = set()
    for value, key in mapping:
        if value is None:
            continue
        rows.append((translate(key, language=language), _area_label(value)))
        used.add(round(value, 2))
    if total is not None and round(total, 2) not in used:
        rows.append((translate("property_total_m2", language=language), _area_label(total)))
    return rows


def _area_label(value):
    if value == int(value):
        return f"{int(value)} m²"
    return f"{value:.1f}".replace(".", ",") + " m²"


def fact_chips(property_row, language="es"):
    row = property_row or {}
    chips = list(row.get("fact_parts") or [])
    if row.get("area_label"):
        chips.append(row["area_label"])
    if row.get("parking_label"):
        chips.append(row["parking_label"])
    return chips


def characteristic_rows(property_row, language="es"):
    row = property_row or {}
    items = []
    pairs = (
        ("rooms", "property_rooms"),
        ("bedrooms", "property_bedrooms"),
        ("bathrooms", "property_bathrooms"),
        ("parking_spaces", "property_parking_spaces"),
    )
    for field, key in pairs:
        if row.get(field) not in (None, ""):
            items.append((translate(key, language=language), str(row[field])))
    items.extend(surface_rows(row, language))
    year = parse_external_metadata(row).get("year_build")
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = None
    if year and 1800 <= year <= datetime.utcnow().year + 1:
        age = datetime.utcnow().year - year
        if 0 <= age < 200:
            items.append((translate("property_age", language=language), translate("property_age_n", language=language, n=age)))
    return items


def maintenance_line(property_row, language="es"):
    meta = parse_external_metadata(property_row)
    block = meta.get("maintenance")
    if not isinstance(block, dict):
        return None
    amount = block.get("value")
    if amount in (None, ""):
        amount = block.get("amount") or block.get("price")
    if amount in (None, ""):
        return None
    currency = block.get("currency") or "ARS"
    return format_listing_money(amount, currency, language=language)


def latest_acm_for_property(organization_id, property_id, *, agent_id):
    if not agent_id or not property_id:
        return None
    from modules.database.property_acm_repository import list_acms

    for item in list_acms(organization_id, agent_id=agent_id, limit=50):
        try:
            if int(item.get("property_id") or 0) == int(property_id):
                return item
        except (TypeError, ValueError):
            continue
    return None


def relative_sync_label(value, language="es"):
    text = str(value or "").strip()
    if not text:
        return None
    stamp = text[:19]
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return text
    delta = datetime.utcnow() - moment
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return translate("property_sync_just_now", language=language)
    if minutes < 60:
        return translate("property_sync_minutes_ago", language=language, n=minutes)
    hours = minutes // 60
    if hours < 24:
        return translate("property_sync_hours_ago", language=language, n=hours)
    days = hours // 24
    return translate("property_sync_days_ago", language=language, n=days)


def price_history_rows(organization_id, property_id, language="es"):
    from modules.database.external_price_history_repository import list_external_price_history

    rows = list_external_price_history(organization_id, property_id) or []
    items = []
    for row in rows:
        amount = row.get("price")
        if amount in (None, ""):
            continue
        currency = row.get("currency") or "USD"
        items.append(
            {
                "label": format_money(amount, currency=currency, language=language),
                "date": _short_date(row.get("date")),
            }
        )
    return items


def _short_date(value):
    text = str(value or "").strip()
    if not text:
        return ""
    stamp = text[:10]
    if len(stamp) == 10 and stamp[4] == "-" and stamp[7] == "-":
        return f"{stamp[8:10]}/{stamp[5:7]}/{stamp[:4]}"
    return stamp


def agent_initials(name):
    parts = [part for part in str(name or "").split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _can_view_agent_profile(user, agent_id):
    if not user or agent_id in (None, ""):
        return False
    try:
        agent_id = int(agent_id)
    except (TypeError, ValueError):
        return False
    if user.get("role") == "admin":
        return True
    try:
        return user.get("role") == "agent" and int(user.get("agent_id") or 0) == agent_id
    except (TypeError, ValueError):
        return False


def _acm_summary(latest_acm, language="es"):
    if not latest_acm:
        return None
    value = latest_acm.get("estimated_value")
    currency = latest_acm.get("currency") or "USD"
    return {
        "id": latest_acm.get("id"),
        "date_label": _short_date(latest_acm.get("finalized_at") or latest_acm.get("created_at")),
        "value_label": (
            format_listing_money(value, currency, language=language)
            if value not in (None, "")
            else None
        ),
    }


def build_property_detail_view(property_row, *, organization_id, user, language="es", is_guest=False):
    row = dict(property_row or {})
    meta = parse_external_metadata(row)
    agent_id = row.get("agent_id")
    branding = get_agent_branding(
        agent_id,
        organization_id,
        language=language,
        agent_login_only=True,
    )
    current_agent_id = (user or {}).get("agent_id") if (user or {}).get("role") == "agent" else None
    latest_acm = latest_acm_for_property(
        organization_id,
        row.get("id"),
        agent_id=current_agent_id,
    )
    history = price_history_rows(organization_id, row.get("id"), language) if row.get("id") else []
    extra_count = max(0, int(row.get("media_total") or 0) - int(row.get("gallery_count") or 0))
    status_label = row.get("commercial_status_label") or translate(
        f"property_status_{row.get('status') or 'approved'}",
        language=language,
    )
    return {
        "title": compact_property_title(row),
        "location_line": compact_property_location(row),
        "secondary_line": secondary_address_bits(row),
        "full_address": row.get("formatted_address") or row.get("address") or "",
        "source_badge": public_source_badge(row, language),
        "source_key": str(row.get("external_source") or "manual").strip().lower() or "manual",
        "type_label": display_type_label(row, language),
        "chips": fact_chips(row, language),
        "features_extra": extra_feature_chips(row, language),
        "characteristics": characteristic_rows(row, language),
        "maintenance": maintenance_line(row, language),
        "mls": meta.get("mlsid") or row.get("external_id"),
        "metadata": meta,
        "branding": branding,
        "agent_initials": agent_initials((branding or {}).get("name")),
        "show_agent_profile": _can_view_agent_profile(user, agent_id),
        "agent_actions": get_property_agent_actions(
            user,
            row,
            is_guest=is_guest or (user or {}).get("role") == "guest",
            branding=branding,
        ),
        "latest_acm": _acm_summary(latest_acm, language),
        "price_history": history,
        "sync_ago": relative_sync_label(row.get("last_synced_at"), language),
        "extra_photo_count": extra_count,
        "status_label": status_label,
        "sync_warning": bool(row.get("sync_error")),
        "category_label": (
            translate("properties_category_residential", language=language)
            if normalize_property_type(row.get("property_type"))
            in {"apartment", "house", "ph"}
            else None
        ),
    }
