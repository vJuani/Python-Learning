"""Deterministic ACM scoring and valuation. No LLM. Decimal only for money."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from statistics import median, mean

from modules.property_features import normalize_property_features
from modules.search import fold_text


SOURCE_INTERNAL = "internal_property"
SOURCE_CLOSED = "closed_operation"
SOURCE_MANUAL = "manual_external"
SOURCE_PORTAL = "external_portal"

PRICE_LISTING = "listing"
PRICE_CLOSING = "closing"
PRICE_MANUAL = "manual"

AREA_COVERED = "covered_m2"
AREA_TOTAL = "total_m2"

CANDIDATE_LIMIT = 80
MIN_SELECT_SCORE = Decimal("55")
FALLBACK_SELECT_SCORE = Decimal("40")
MAX_SELECTED = 8

# Only weights for fields that exist in JRH One today.
# No antiquity, condition, or lat/lng — those are not stored.
SCORE_WEIGHTS = {
    "zone": Decimal("30"),
    "type": Decimal("15"),
    "area": Decimal("20"),
    "rooms": Decimal("10"),
    "bedrooms": Decimal("5"),
    "parking": Decimal("5"),
    "features": Decimal("5"),
    "recency": Decimal("5"),
    "currency": Decimal("5"),
}

MONEY_QUANT = Decimal("1")
PPM2_QUANT = Decimal("0.01")
SCORE_QUANT = Decimal("0.01")


def to_decimal(value):
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def round_money(value):
    number = to_decimal(value)
    if number is None:
        return None
    return number.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def round_ppm2(value):
    number = to_decimal(value)
    if number is None:
        return None
    return number.quantize(PPM2_QUANT, rounding=ROUND_HALF_UP)


def round_score(value):
    number = to_decimal(value)
    if number is None:
        return Decimal("0")
    clamped = max(Decimal("0"), min(Decimal("100"), number))
    return clamped.quantize(SCORE_QUANT, rounding=ROUND_HALF_UP)


def choose_area_basis(subject):
    covered = to_decimal(subject.get("covered_m2"))
    total = to_decimal(subject.get("total_m2"))
    if covered is not None and covered > 0:
        return AREA_COVERED
    if total is not None and total > 0:
        return AREA_TOTAL
    return ""


def comparable_area(item, basis):
    if basis == AREA_COVERED:
        return to_decimal(item.get("covered_m2") or item.get("snapshot_covered_area"))
    if basis == AREA_TOTAL:
        return to_decimal(item.get("total_m2") or item.get("snapshot_total_area"))
    return None


def price_per_m2(price, area):
    amount = to_decimal(price)
    surface = to_decimal(area)
    if amount is None or surface is None or surface <= 0:
        return None
    return round_ppm2(amount / surface)


def _features(item):
    raw = item.get("features")
    if isinstance(raw, dict):
        return raw
    return normalize_property_features(item.get("features_json"))


def score_comparable(subject, candidate):
    """Return 0–100. Missing fields drop out of the denominator."""
    earned = Decimal("0")
    applicable = Decimal("0")

    sub_hood = fold_text(subject.get("neighborhood"))
    cand_hood = fold_text(
        candidate.get("neighborhood") or candidate.get("snapshot_location")
    )
    sub_jur = fold_text(subject.get("jurisdiction"))
    cand_jur = fold_text(candidate.get("jurisdiction"))
    if sub_hood or cand_hood or sub_jur or cand_jur:
        applicable += SCORE_WEIGHTS["zone"]
        if sub_hood and cand_hood and sub_hood == cand_hood:
            earned += SCORE_WEIGHTS["zone"]
        elif sub_jur and cand_jur and sub_jur == cand_jur:
            earned += SCORE_WEIGHTS["zone"] * Decimal("0.5")

    sub_type = fold_text(subject.get("property_type"))
    cand_type = fold_text(
        candidate.get("property_type") or candidate.get("snapshot_property_type")
    )
    if sub_type or cand_type:
        applicable += SCORE_WEIGHTS["type"]
        if sub_type and cand_type and sub_type == cand_type:
            earned += SCORE_WEIGHTS["type"]

    basis = choose_area_basis(subject)
    sub_area = comparable_area(subject, basis)
    cand_area = comparable_area(candidate, basis)
    if sub_area and cand_area and sub_area > 0 and cand_area > 0:
        applicable += SCORE_WEIGHTS["area"]
        ratio = abs(sub_area - cand_area) / max(sub_area, cand_area)
        earned += SCORE_WEIGHTS["area"] * max(Decimal("0"), Decimal("1") - ratio)

    sub_rooms = to_decimal(subject.get("rooms"))
    cand_rooms = to_decimal(candidate.get("rooms") or candidate.get("snapshot_rooms"))
    if sub_rooms is not None and cand_rooms is not None:
        applicable += SCORE_WEIGHTS["rooms"]
        if sub_rooms == cand_rooms:
            earned += SCORE_WEIGHTS["rooms"]
        elif abs(sub_rooms - cand_rooms) == 1:
            earned += SCORE_WEIGHTS["rooms"] * Decimal("0.5")

    sub_beds = to_decimal(subject.get("bedrooms"))
    cand_beds = to_decimal(
        candidate.get("bedrooms") or candidate.get("snapshot_bedrooms")
    )
    if sub_beds is not None and cand_beds is not None:
        applicable += SCORE_WEIGHTS["bedrooms"]
        if sub_beds == cand_beds:
            earned += SCORE_WEIGHTS["bedrooms"]
        elif abs(sub_beds - cand_beds) == 1:
            earned += SCORE_WEIGHTS["bedrooms"] * Decimal("0.5")

    sub_park = to_decimal(subject.get("parking_spaces")) or Decimal("0")
    cand_park = to_decimal(candidate.get("parking_spaces")) or Decimal("0")
    if subject.get("parking_spaces") is not None or candidate.get("parking_spaces") is not None:
        applicable += SCORE_WEIGHTS["parking"]
        if (sub_park > 0) == (cand_park > 0):
            earned += SCORE_WEIGHTS["parking"]

    sub_feat = {key for key, value in _features(subject).items() if value}
    cand_feat = {key for key, value in _features(candidate).items() if value}
    compared = {"balcony", "terrace", "garden"}
    if sub_feat & compared or cand_feat & compared:
        applicable += SCORE_WEIGHTS["features"]
        overlap = len((sub_feat & cand_feat) & compared)
        union = len((sub_feat | cand_feat) & compared) or 1
        earned += SCORE_WEIGHTS["features"] * (Decimal(overlap) / Decimal(union))

    recency_days = candidate.get("recency_days")
    if recency_days is not None:
        applicable += SCORE_WEIGHTS["recency"]
        if recency_days <= 180:
            earned += SCORE_WEIGHTS["recency"]
        elif recency_days <= 365:
            earned += SCORE_WEIGHTS["recency"] * Decimal("0.5")

    sub_cur = (subject.get("listing_currency") or subject.get("currency") or "").upper()
    cand_cur = (
        candidate.get("listing_currency")
        or candidate.get("snapshot_currency")
        or candidate.get("currency")
        or ""
    ).upper()
    if sub_cur or cand_cur:
        applicable += SCORE_WEIGHTS["currency"]
        if sub_cur and cand_cur and sub_cur == cand_cur:
            earned += SCORE_WEIGHTS["currency"]

    if applicable <= 0:
        return Decimal("0")
    return round_score((earned / applicable) * Decimal("100"))


def _quartile(sorted_values, percentile):
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = (len(sorted_values) - 1) * percentile
    low = int(index)
    high = min(low + 1, len(sorted_values) - 1)
    weight = Decimal(str(index - low))
    return sorted_values[low] * (Decimal("1") - weight) + sorted_values[high] * weight


def flag_outliers(ppm2_values):
    """IQR fences, or median ± 40% when n < 4. Never deletes rows."""
    numbers = [to_decimal(item) for item in ppm2_values]
    numbers = [item for item in numbers if item is not None]
    flags = [False] * len(ppm2_values)
    if len(numbers) < 3:
        return flags
    ordered = sorted(numbers)
    mid = median(ordered)
    if len(ordered) >= 4:
        q1 = _quartile(ordered, Decimal("0.25"))
        q3 = _quartile(ordered, Decimal("0.75"))
        iqr = q3 - q1
        if iqr > 0:
            low = q1 - (Decimal("1.5") * iqr)
            high = q3 + (Decimal("1.5") * iqr)
        else:
            low = mid * Decimal("0.6")
            high = mid * Decimal("1.4")
    else:
        low = mid * Decimal("0.6")
        high = mid * Decimal("1.4")
    result = []
    for item in ppm2_values:
        value = to_decimal(item)
        result.append(bool(value is not None and (value < low or value > high)))
    return result


def compute_metrics(selected_rows, subject):
    """Valuation from selected, non-outlier rows with a valid $/m²."""
    basis = choose_area_basis(subject)
    subject_area = comparable_area(subject, basis)
    usable = []
    for row in selected_rows:
        if row.get("is_outlier") or not row.get("selected"):
            continue
        ppm2 = to_decimal(row.get("snapshot_price_per_m2") or row.get("price_per_m2"))
        if ppm2 is None:
            continue
        usable.append(
            {
                **row,
                "ppm2": ppm2,
                "area": comparable_area(row, basis),
            }
        )
    metrics = {
        "area_basis": basis,
        "used_count": len(usable),
        "selected_count": sum(1 for row in selected_rows if row.get("selected")),
        "min_ppm2": None,
        "average_ppm2": None,
        "median_ppm2": None,
        "max_ppm2": None,
        "estimated_value": None,
        "suggested_min": None,
        "suggested_max": None,
        "spread": None,
    }
    if not usable or subject_area is None or subject_area <= 0:
        return metrics
    values = [row["ppm2"] for row in usable]
    ordered = sorted(values)
    metrics["min_ppm2"] = round_ppm2(min(values))
    metrics["max_ppm2"] = round_ppm2(max(values))
    metrics["average_ppm2"] = round_ppm2(Decimal(str(mean(values))))
    metrics["median_ppm2"] = round_ppm2(Decimal(str(median(values))))
    base = metrics["median_ppm2"] * subject_area
    if len(ordered) >= 4:
        q1 = _quartile(ordered, Decimal("0.25"))
        q3 = _quartile(ordered, Decimal("0.75"))
        if metrics["median_ppm2"] > 0 and q3 > q1:
            spread = (q3 - q1) / (Decimal("2") * metrics["median_ppm2"])
        else:
            spread = Decimal("0.06")
    else:
        spread = Decimal("0.06")
    if spread < Decimal("0.03"):
        spread = Decimal("0.03")
    if spread > Decimal("0.12"):
        spread = Decimal("0.12")
    metrics["spread"] = spread
    metrics["estimated_value"] = round_money(base)
    metrics["suggested_min"] = round_money(base * (Decimal("1") - spread))
    metrics["suggested_max"] = round_money(base * (Decimal("1") + spread))
    return metrics


def default_selected(score, is_outlier):
    if is_outlier:
        return False
    return score >= MIN_SELECT_SCORE
