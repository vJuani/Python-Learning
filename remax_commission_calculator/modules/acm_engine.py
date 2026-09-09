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
MIN_VALID_COMPS = 3
CLOSING_SCORE_BONUS = Decimal("8")
GENERIC_JURISDICTIONS = {
    "pba",
    "caba",
    "bs as",
    "buenos aires",
    "argentina",
    "gba",
}

# Only weights for fields that exist in JRH One today.
# Distance uses the zone weight when both points have coordinates.
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
    covered = to_decimal(subject.get("covered_m2") or subject.get("snapshot_covered_area"))
    total = to_decimal(subject.get("total_m2") or subject.get("snapshot_total_area"))
    if covered is not None and covered > 0:
        return AREA_COVERED
    if total is not None and total > 0:
        return AREA_TOTAL
    return ""


def display_area(item):
    """Covered if present, else total. Never mixes both into one number."""
    covered = to_decimal(item.get("covered_m2") or item.get("snapshot_covered_area"))
    if covered is not None and covered > 0:
        return covered
    total = to_decimal(item.get("total_m2") or item.get("snapshot_total_area"))
    if total is not None and total > 0:
        return total
    return None


def comparable_area(item, basis=None):
    if basis == AREA_COVERED:
        value = to_decimal(item.get("covered_m2") or item.get("snapshot_covered_area"))
        return value if value is not None and value > 0 else None
    if basis == AREA_TOTAL:
        value = to_decimal(item.get("total_m2") or item.get("snapshot_total_area"))
        return value if value is not None and value > 0 else None
    return display_area(item)


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
        candidate.get("neighborhood")
        or (candidate.get("snapshot_location") or "").split(",")[0]
    )
    sub_jur = fold_text(subject.get("jurisdiction"))
    cand_jur = fold_text(candidate.get("jurisdiction"))
    from modules.maps.geo import acm_geo_ratio, distance_between_coordinates

    distance_m = candidate.get("distance_meters")
    if distance_m is None:
        distance_m = distance_between_coordinates(
            subject.get("latitude") or subject.get("snapshot_latitude"),
            subject.get("longitude") or subject.get("snapshot_longitude"),
            candidate.get("latitude") or candidate.get("snapshot_latitude"),
            candidate.get("longitude") or candidate.get("snapshot_longitude"),
        )
        if distance_m is not None:
            candidate["distance_meters"] = distance_m
    geo_ratio = acm_geo_ratio(distance_m)
    if geo_ratio is not None:
        applicable += SCORE_WEIGHTS["zone"]
        earned += SCORE_WEIGHTS["zone"] * Decimal(str(geo_ratio))
    elif sub_hood or cand_hood or sub_jur or cand_jur:
        applicable += SCORE_WEIGHTS["zone"]
        if sub_hood and cand_hood and sub_hood == cand_hood:
            earned += SCORE_WEIGHTS["zone"]
        elif sub_jur and cand_jur and sub_jur == cand_jur:
            factor = Decimal("0.25") if cand_jur in GENERIC_JURISDICTIONS else Decimal("0.5")
            earned += SCORE_WEIGHTS["zone"] * factor

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
        score = Decimal("0")
    else:
        score = round_score((earned / applicable) * Decimal("100"))
    price_kind = (candidate.get("price_kind") or candidate.get("snapshot_price_kind") or "")
    if price_kind == PRICE_CLOSING:
        score = round_score(min(Decimal("100"), score + CLOSING_SCORE_BONUS))
    return score


def explain_score(subject, candidate):
    """Deterministic match/diff bullets. Does not invent facts."""
    matches = []
    diffs = []
    sub_type = fold_text(subject.get("property_type"))
    cand_type = fold_text(
        candidate.get("property_type") or candidate.get("snapshot_property_type")
    )
    if sub_type and cand_type and sub_type == cand_type:
        matches.append("type")
    elif sub_type and cand_type:
        diffs.append("type")

    sub_cur = (subject.get("listing_currency") or subject.get("currency") or "").upper()
    cand_cur = (
        candidate.get("listing_currency")
        or candidate.get("snapshot_currency")
        or candidate.get("currency")
        or ""
    ).upper()
    if sub_cur and cand_cur and sub_cur == cand_cur:
        matches.append("currency")
    elif sub_cur and cand_cur:
        diffs.append("currency")

    sub_hood = fold_text(subject.get("neighborhood"))
    cand_hood = fold_text(
        candidate.get("neighborhood")
        or (candidate.get("snapshot_location") or "").split(",")[0]
    )
    sub_jur = fold_text(subject.get("jurisdiction"))
    cand_jur = fold_text(candidate.get("jurisdiction"))
    from modules.maps.geo import distance_between_coordinates

    distance_m = candidate.get("distance_meters")
    if distance_m is None:
        distance_m = distance_between_coordinates(
            subject.get("latitude") or subject.get("snapshot_latitude"),
            subject.get("longitude") or subject.get("snapshot_longitude"),
            candidate.get("latitude") or candidate.get("snapshot_latitude"),
            candidate.get("longitude") or candidate.get("snapshot_longitude"),
        )
    if distance_m is not None:
        matches.append("distance")
    elif sub_hood and cand_hood and sub_hood == cand_hood:
        matches.append("neighborhood")
    elif sub_jur and cand_jur and sub_jur == cand_jur:
        if cand_jur in GENERIC_JURISDICTIONS:
            diffs.append("broad_jurisdiction")
        else:
            matches.append("jurisdiction")
    elif sub_hood or cand_hood:
        diffs.append("zone")

    basis = choose_area_basis(subject)
    sub_area = comparable_area(subject, basis) or display_area(subject)
    cand_area = comparable_area(candidate, basis) or display_area(candidate)
    if sub_area and cand_area:
        ratio = (cand_area - sub_area) / sub_area
        if abs(ratio) <= Decimal("0.08"):
            matches.append("area")
        else:
            diffs.append(("area_delta", str(round(ratio * 100, 1))))
    elif not cand_area:
        diffs.append("area_missing")

    sub_rooms = to_decimal(subject.get("rooms"))
    cand_rooms = to_decimal(candidate.get("rooms") or candidate.get("snapshot_rooms"))
    if sub_rooms is not None and cand_rooms is not None:
        if sub_rooms == cand_rooms:
            matches.append("rooms")
        else:
            diffs.append(("rooms_delta", str(int(cand_rooms - sub_rooms))))

    sub_beds = to_decimal(subject.get("bedrooms"))
    cand_beds = to_decimal(candidate.get("bedrooms") or candidate.get("snapshot_bedrooms"))
    if sub_beds is not None and cand_beds is not None:
        if sub_beds == cand_beds:
            matches.append("bedrooms")
        else:
            diffs.append(("bedrooms_delta", str(int(cand_beds - sub_beds))))

    sub_park = to_decimal(subject.get("parking_spaces") or candidate.get("snapshot_parking"))
    cand_park = to_decimal(
        candidate.get("parking_spaces") or candidate.get("snapshot_parking")
    )
    sub_has_park = (to_decimal(subject.get("parking_spaces")) or Decimal("0")) > 0
    cand_has_park = (cand_park or Decimal("0")) > 0
    if subject.get("parking_spaces") is not None or cand_park is not None:
        if sub_has_park == cand_has_park:
            matches.append("parking")
        elif sub_has_park and not cand_has_park:
            diffs.append("no_parking")
        elif cand_has_park and not sub_has_park:
            diffs.append("extra_parking")

    return {
        "score": score_comparable(subject, candidate),
        "matches": matches,
        "diffs": diffs,
    }


def is_valuation_valid(row, subject=None):
    """Price + area (or an already computed $/m²). Outliers stay as reference."""
    if row.get("is_outlier"):
        return False
    ppm2 = to_decimal(row.get("snapshot_price_per_m2") or row.get("price_per_m2"))
    price = to_decimal(row.get("snapshot_price") or row.get("price"))
    basis = choose_area_basis(subject or {})
    area = comparable_area(row, basis) if basis else display_area(row)
    if ppm2 is None and (price is None or price <= 0 or area is None or area <= 0):
        return False
    if subject:
        sub_cur = (
            subject.get("listing_currency") or subject.get("currency") or ""
        ).upper()
        row_cur = (
            row.get("snapshot_currency") or row.get("currency") or ""
        ).upper()
        if sub_cur and row_cur and sub_cur != row_cur:
            return False
    return True


def location_parts(item):
    hood = (item.get("neighborhood") or "").strip()
    jur = (item.get("jurisdiction") or "").strip()
    if not hood and item.get("snapshot_location"):
        bits = [part.strip() for part in str(item["snapshot_location"]).split(",")]
        hood = bits[0] if bits else ""
        if len(bits) > 1:
            jur = jur or bits[-1]
    primary = hood or jur
    secondary = jur if hood and jur and fold_text(jur) != fold_text(hood) else ""
    weak = (not hood) and fold_text(jur) in GENERIC_JURISDICTIONS
    return {"primary": primary, "secondary": secondary, "weak": weak}


def subject_quality(subject):
    area = display_area(subject)
    price = to_decimal(subject.get("listing_price") or subject.get("price"))
    currency = (subject.get("listing_currency") or subject.get("currency") or "").strip()
    location = location_parts(subject)
    checks = {
        "property_type": "ok" if subject.get("property_type") else "missing",
        "price": "ok" if price and price > 0 else "missing",
        "currency": "ok" if currency else "missing",
        "location": (
            "ok"
            if location["primary"] and not location["weak"]
            else ("weak" if location["primary"] else "missing")
        ),
        "area": "ok" if area else "missing",
        "rooms": "ok" if subject.get("rooms") not in (None, "") else "missing",
        "bedrooms": "ok" if subject.get("bedrooms") not in (None, "") else "missing",
        "bathrooms": "ok" if subject.get("bathrooms") not in (None, "") else "missing",
        "parking": "ok" if subject.get("parking_spaces") not in (None, "") else "unknown",
    }
    critical_ok = all(
        checks[key] == "ok" for key in ("price", "currency", "area", "property_type")
    ) and checks["location"] != "missing"
    return {
        "checks": checks,
        "critical_ok": critical_ok,
        "can_valuate": bool(area and price),
        "area": area,
        "price": price,
        "currency": currency,
    }


def compute_confidence(metrics, quality):
    valid = int(metrics.get("used_count") or 0)
    closings = int(metrics.get("closing_count") or 0)
    spread = to_decimal(metrics.get("spread")) or Decimal("1")
    if (
        valid >= 5
        and closings >= 1
        and spread <= Decimal("0.08")
        and quality.get("critical_ok")
    ):
        return "high"
    if valid >= MIN_VALID_COMPS and quality.get("can_valuate"):
        return "medium"
    return "low"


def compute_positioning(listing_price, estimated, suggested_min, suggested_max):
    listing = to_decimal(listing_price)
    ref = to_decimal(estimated)
    low = to_decimal(suggested_min)
    high = to_decimal(suggested_max)
    if listing is None or listing <= 0 or ref is None or ref <= 0:
        return None
    delta_pct = ((listing - ref) / ref * Decimal("100")).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    if low is not None and listing < low:
        band = "below"
    elif high is not None and listing > high:
        band = "above"
    elif listing > ref:
        band = "upper"
    elif listing < ref:
        band = "lower"
    else:
        band = "inside"
    return {"band": band, "delta_pct": delta_pct, "listing_price": round_money(listing)}


def compute_price_scenario(proposed_price, estimated, suggested_min, suggested_max):
    """Deterministic 'what if I list at X'. No time-to-sell promise."""
    proposed = to_decimal(proposed_price)
    ref = to_decimal(estimated)
    low = to_decimal(suggested_min)
    high = to_decimal(suggested_max)
    if proposed is None or proposed <= 0 or ref is None or ref <= 0:
        return None
    delta_pct = ((proposed - ref) / ref * Decimal("100")).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    if low is not None and proposed < low:
        band = "below"
    elif high is not None and proposed > high:
        band = "above"
    else:
        band = "inside"
    return {
        "proposed": round_money(proposed),
        "estimated": round_money(ref),
        "suggested_min": round_money(low) if low is not None else None,
        "suggested_max": round_money(high) if high is not None else None,
        "band": band,
        "in_range": band == "inside",
        "delta_vs_market_pct": delta_pct,
    }


def compute_scenarios(suggested_min, estimated, suggested_max):
    """Agile = low range, market = median reference, aspirational = high range."""
    low = round_money(suggested_min)
    mid = round_money(estimated)
    high = round_money(suggested_max)
    if low is None or mid is None or high is None:
        return None
    return {"agile": low, "market": mid, "aspirational": high}


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
    subject_area = comparable_area(subject, basis) if basis else display_area(subject)
    quality = subject_quality(subject)
    usable = []
    for row in selected_rows:
        if not row.get("selected"):
            continue
        if not is_valuation_valid(row, subject):
            continue
        ppm2 = to_decimal(row.get("snapshot_price_per_m2") or row.get("price_per_m2"))
        if ppm2 is None:
            area = comparable_area(row, basis) or display_area(row)
            ppm2 = price_per_m2(row.get("snapshot_price") or row.get("price"), area)
        if ppm2 is None:
            continue
        usable.append(
            {
                **row,
                "ppm2": ppm2,
                "area": comparable_area(row, basis) or display_area(row),
            }
        )
    found = len(selected_rows)
    reference_count = found - len(usable)
    closing_count = sum(
        1
        for row in usable
        if (row.get("snapshot_price_kind") or row.get("price_kind")) == PRICE_CLOSING
    )
    listing_count = sum(
        1
        for row in usable
        if (row.get("snapshot_price_kind") or row.get("price_kind")) == PRICE_LISTING
    )
    manual_count = sum(
        1
        for row in usable
        if (row.get("source_type") in {SOURCE_MANUAL, "manual_external"})
        or (row.get("snapshot_price_kind") == PRICE_MANUAL)
    )
    metrics = {
        "area_basis": basis,
        "used_count": len(usable),
        "found_count": found,
        "valuation_count": len(usable),
        "reference_count": max(0, reference_count),
        "selected_count": sum(1 for row in selected_rows if row.get("selected")),
        "closing_count": closing_count,
        "listing_count": listing_count,
        "manual_count": manual_count,
        "min_ppm2": None,
        "average_ppm2": None,
        "median_ppm2": None,
        "max_ppm2": None,
        "min_price": None,
        "max_price": None,
        "average_area": None,
        "estimated_value": None,
        "suggested_min": None,
        "suggested_max": None,
        "spread": None,
        "confidence": "low",
        "positioning": None,
        "scenarios": None,
        "can_finalize": False,
        "min_valid_required": MIN_VALID_COMPS,
    }
    if not usable:
        metrics["confidence"] = compute_confidence(metrics, quality)
        return metrics
    values = [row["ppm2"] for row in usable]
    ordered = sorted(values)
    metrics["min_ppm2"] = round_ppm2(min(values))
    metrics["max_ppm2"] = round_ppm2(max(values))
    metrics["average_ppm2"] = round_ppm2(Decimal(str(mean(values))))
    metrics["median_ppm2"] = round_ppm2(Decimal(str(median(values))))
    prices = [
        to_decimal(row.get("snapshot_price") or row.get("price"))
        for row in usable
    ]
    prices = [item for item in prices if item is not None]
    if prices:
        metrics["min_price"] = round_money(min(prices))
        metrics["max_price"] = round_money(max(prices))
    areas = [row.get("area") for row in usable if row.get("area")]
    if areas:
        metrics["average_area"] = round_ppm2(Decimal(str(mean(areas))))
    if subject_area is None or subject_area <= 0:
        metrics["confidence"] = compute_confidence(metrics, quality)
        return metrics
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
    metrics["scenarios"] = compute_scenarios(
        metrics["suggested_min"],
        metrics["estimated_value"],
        metrics["suggested_max"],
    )
    metrics["positioning"] = compute_positioning(
        subject.get("listing_price") or subject.get("price"),
        metrics["estimated_value"],
        metrics["suggested_min"],
        metrics["suggested_max"],
    )
    metrics["confidence"] = compute_confidence(metrics, quality)
    metrics["can_finalize"] = bool(
        quality.get("can_valuate")
        and len(usable) >= MIN_VALID_COMPS
        and metrics["estimated_value"] is not None
    )
    return metrics


def default_selected(score, is_outlier):
    if is_outlier:
        return False
    return score >= MIN_SELECT_SCORE
