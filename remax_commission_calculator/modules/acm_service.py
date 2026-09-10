"""ACM orchestration. Deterministic valuation. Agent-scoped."""

from __future__ import annotations

from datetime import datetime

from modules.acm_engine import (
    AREA_COVERED,
    FALLBACK_SELECT_SCORE,
    MAX_SELECTED,
    MIN_VALID_COMPS,
    PRICE_CLOSING,
    PRICE_LISTING,
    PRICE_MANUAL,
    SOURCE_CLOSED,
    SOURCE_INTERNAL,
    SOURCE_MANUAL,
    choose_area_basis,
    comparable_area,
    compute_metrics,
    compute_price_scenario,
    default_selected,
    display_area,
    explain_score,
    flag_outliers,
    is_valuation_valid,
    location_parts,
    price_per_m2,
    score_comparable,
    subject_quality,
    to_decimal,
)
from modules.acm_explain import (
    build_acm_facts,
    explain_acm,
    fallback_market_highlights,
    most_similar_comparable,
)
from modules.acm_sources import (
    format_diff_label,
    format_match_label,
    source_catalog,
    source_label,
)
from modules.auth import ROLE_AGENT
from modules.database.property_acm_repository import (
    STATUS_DRAFT,
    STATUS_FINALIZED,
    STATUS_READY,
    add_comparable,
    create_acm,
    get_acm,
    get_comparable,
    list_acms,
    list_closed_candidates,
    list_comparables,
    list_internal_candidates,
    update_acm,
    update_comparable,
)
from modules.database.properties_repository import get_property_record
from modules.i18n import translate


class AcmError(Exception):
    def __init__(self, message_key, status_code=400):
        super().__init__(message_key)
        self.message_key = message_key
        self.status_code = status_code


def require_acm_agent(user):
    if not user or user.get("role") != ROLE_AGENT or not user.get("agent_id"):
        raise AcmError("acm_err_agent_only", 403)
    return user


def _subject_snapshot(property_data):
    return {
        "id": property_data.get("id"),
        "address": property_data.get("address"),
        "neighborhood": property_data.get("neighborhood"),
        "jurisdiction": property_data.get("jurisdiction"),
        "property_type": property_data.get("property_type"),
        "listing_purpose": property_data.get("listing_purpose"),
        "listing_price": property_data.get("listing_price"),
        "listing_currency": property_data.get("listing_currency"),
        "rooms": property_data.get("rooms"),
        "bedrooms": property_data.get("bedrooms"),
        "bathrooms": property_data.get("bathrooms"),
        "bathrooms": property_data.get("bathrooms"),
        "covered_m2": property_data.get("covered_m2"),
        "total_m2": property_data.get("total_m2"),
        "parking_spaces": property_data.get("parking_spaces"),
        "features": property_data.get("features") or {},
        "commercial_status": property_data.get("commercial_status"),
        "latitude": property_data.get("latitude"),
        "longitude": property_data.get("longitude"),
    }


def _location_label(item):
    parts = location_parts(item)
    if parts["primary"] and parts["secondary"]:
        return f"{parts['primary']}, {parts['secondary']}"
    return parts["primary"] or ""


def _hydrate_area_from_catalog(organization_id, candidate):
    if display_area(candidate):
        return candidate, "property"
    external_id = candidate.get("external_id")
    if not external_id:
        return candidate, None
    try:
        from modules.database.external_listings_repository import (
            get_external_listing_by_source_id,
        )
        from modules.listing_sources import LISTING_SOURCES
    except Exception:
        return candidate, None
    for source in LISTING_SOURCES:
        if source == "internal":
            continue
        try:
            listing = get_external_listing_by_source_id(
                organization_id, source, external_id
            )
        except Exception:
            continue
        if not listing:
            continue
        if listing.get("covered_m2") or listing.get("total_m2"):
            candidate["covered_m2"] = listing.get("covered_m2")
            candidate["total_m2"] = listing.get("total_m2")
            if not candidate.get("rooms") and listing.get("rooms"):
                candidate["rooms"] = listing.get("rooms")
            if not candidate.get("bedrooms") and listing.get("bedrooms"):
                candidate["bedrooms"] = listing.get("bedrooms")
            return candidate, "catalog"
    return candidate, None


def _recency_days(raw):
    if not raw:
        return None
    text = str(raw)[:10]
    try:
        day = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        try:
            day = datetime.strptime(text, "%d/%m/%Y")
        except ValueError:
            return None
    return max(0, (datetime.utcnow() - day).days)


def _area_bounds(subject, basis, pct="0.20"):
    area = comparable_area(subject, basis) or display_area(subject)
    if area is None or area <= 0:
        return None, None
    factor = to_decimal(pct) or to_decimal("0.20")
    return area * (to_decimal("1") - factor), area * (to_decimal("1") + factor)


def find_candidates(organization_id, subject, filters=None):
    filters = filters or {}
    basis = choose_area_basis(subject) or AREA_COVERED
    area_pct = filters.get("area_pct") or "0.20"
    min_area, max_area = _area_bounds(subject, basis, area_pct)
    same_zone = filters.get("same_zone", True)
    from modules.maps.geo import (
        ACM_UNLIMITED_SEARCH_KM,
        attach_distance,
        bounding_box,
        parse_radius_km,
        radius_km_to_meters,
    )
    from modules.maps.location import has_coordinates

    max_distance_km = parse_radius_km(filters.get("max_distance_km"))
    subject_geo = has_coordinates(subject)
    search_km = max_distance_km
    if search_km is None and subject_geo:
        search_km = ACM_UNLIMITED_SEARCH_KM
    box = None
    if subject_geo and search_km:
        box = bounding_box(
            subject.get("latitude"),
            subject.get("longitude"),
            radius_km_to_meters(search_km),
        )
    use_text_zone = same_zone and not subject_geo
    internal = list_internal_candidates(
        organization_id,
        exclude_property_id=subject["id"],
        listing_purpose=subject.get("listing_purpose"),
        property_type=subject.get("property_type"),
        currency=subject.get("listing_currency"),
        neighborhood=subject.get("neighborhood") if use_text_zone else None,
        jurisdiction=subject.get("jurisdiction") if use_text_zone else None,
        min_area=min_area,
        max_area=max_area,
        area_column=basis or AREA_COVERED,
        min_lat=(box or {}).get("south"),
        max_lat=(box or {}).get("north"),
        min_lng=(box or {}).get("west"),
        max_lng=(box or {}).get("east"),
        limit=80,
    )
    closed = list_closed_candidates(
        organization_id,
        exclude_property_id=subject["id"],
        listing_purpose=subject.get("listing_purpose"),
        property_type=subject.get("property_type"),
        currency=subject.get("listing_currency"),
        neighborhood=subject.get("neighborhood") if use_text_zone else None,
        jurisdiction=subject.get("jurisdiction") if use_text_zone else None,
        min_lat=(box or {}).get("south"),
        max_lat=(box or {}).get("north"),
        min_lng=(box or {}).get("west"),
        max_lng=(box or {}).get("east"),
        limit=40,
    )
    include_closing = filters.get("include_closing", True)
    include_listing = filters.get("include_listing", True)
    rooms_delta = to_decimal(filters.get("rooms_delta"))
    max_age_months = to_decimal(filters.get("max_age_months"))
    seen = set()
    ranked = []
    if include_closing:
        for item in closed:
            item = dict(item)
            item = attach_distance(
                item,
                subject.get("latitude"),
                subject.get("longitude"),
            )
            if max_distance_km is not None:
                meters = item.get("distance_meters")
                if meters is None or meters > max_distance_km * 1000:
                    continue
            item["source_type"] = SOURCE_CLOSED
            item["price"] = item.get("sale_price")
            item["currency"] = item.get("operation_currency") or item.get("listing_currency")
            item["price_kind"] = PRICE_CLOSING
            item["recency_days"] = _recency_days(item.get("operation_date"))
            item, area_source = _hydrate_area_from_catalog(organization_id, item)
            item["area_source"] = area_source or "property"
            if not _passes_candidate_filters(subject, item, rooms_delta, max_age_months):
                continue
            item["score"] = score_comparable(subject, item)
            seen.add(("prop", item["id"]))
            ranked.append(item)
    if include_listing:
        for item in internal:
            if ("prop", item["id"]) in seen:
                continue
            item = dict(item)
            item = attach_distance(
                item,
                subject.get("latitude"),
                subject.get("longitude"),
            )
            if max_distance_km is not None:
                meters = item.get("distance_meters")
                if meters is None or meters > max_distance_km * 1000:
                    continue
            item["source_type"] = SOURCE_INTERNAL
            item["price"] = item.get("listing_price")
            item["currency"] = item.get("listing_currency")
            item["price_kind"] = PRICE_LISTING
            item["recency_days"] = _recency_days(item.get("last_synced_at"))
            item, area_source = _hydrate_area_from_catalog(organization_id, item)
            item["area_source"] = area_source or "property"
            if not _passes_candidate_filters(subject, item, rooms_delta, max_age_months):
                continue
            item["score"] = score_comparable(subject, item)
            ranked.append(item)
    ranked.sort(key=lambda row: row.get("score") or 0, reverse=True)
    return ranked[:MAX_SELECTED * 2]


def _passes_candidate_filters(subject, item, rooms_delta, max_age_months):
    if rooms_delta is not None and subject.get("rooms") not in (None, ""):
        cand_rooms = to_decimal(item.get("rooms"))
        sub_rooms = to_decimal(subject.get("rooms"))
        if cand_rooms is not None and sub_rooms is not None:
            if abs(cand_rooms - sub_rooms) > rooms_delta:
                return False
    if max_age_months is not None and item.get("recency_days") is not None:
        if item["recency_days"] > int(max_age_months) * 30:
            return False
    return True


def _row_from_candidate(candidate, subject):
    basis = choose_area_basis(subject)
    same_area = comparable_area(candidate, basis) if basis else None
    area = same_area or display_area(candidate)
    ppm2 = price_per_m2(candidate.get("price"), area)
    reasons = explain_score(subject, candidate)
    return {
        "comparable_property_id": candidate.get("id"),
        "source_type": candidate.get("source_type"),
        "external_reference": candidate.get("address"),
        "snapshot_price": str(candidate.get("price")) if candidate.get("price") is not None else None,
        "snapshot_currency": candidate.get("currency"),
        "snapshot_total_area": (
            str(candidate.get("total_m2")) if candidate.get("total_m2") is not None else None
        ),
        "snapshot_covered_area": (
            str(candidate.get("covered_m2")) if candidate.get("covered_m2") is not None else None
        ),
        "snapshot_rooms": candidate.get("rooms"),
        "snapshot_bedrooms": candidate.get("bedrooms"),
        "snapshot_bathrooms": candidate.get("bathrooms"),
        "snapshot_parking": candidate.get("parking_spaces"),
        "snapshot_property_type": candidate.get("property_type"),
        "snapshot_location": _location_label(candidate),
        "snapshot_price_per_m2": str(ppm2) if ppm2 is not None else None,
        "snapshot_listing_purpose": candidate.get("listing_purpose"),
        "snapshot_price_kind": candidate.get("price_kind"),
        "snapshot_operation_id": candidate.get("operation_id"),
        "snapshot_operation_date": candidate.get("operation_date"),
        "snapshot_observed_at": candidate.get("operation_date") or candidate.get("last_synced_at"),
        "score": str(candidate.get("score") or reasons["score"]),
        "score_reasons": reasons,
        "is_outlier": False,
        "selected": True,
        "notes": None,
        "area_source": candidate.get("area_source") or "property",
        "features_json": candidate.get("features_json"),
        "covered_m2": candidate.get("covered_m2"),
        "total_m2": candidate.get("total_m2"),
        "neighborhood": candidate.get("neighborhood"),
        "jurisdiction": candidate.get("jurisdiction"),
        "latitude": candidate.get("latitude"),
        "longitude": candidate.get("longitude"),
        "snapshot_latitude": candidate.get("latitude"),
        "snapshot_longitude": candidate.get("longitude"),
        "distance_meters": (
            str(int(round(candidate["distance_meters"])))
            if candidate.get("distance_meters") is not None
            else None
        ),
    }


def _apply_outliers_and_selection(rows):
    ppm2s = [row.get("snapshot_price_per_m2") for row in rows]
    flags = flag_outliers(ppm2s)
    scored = []
    for row, is_outlier in zip(rows, flags):
        item = dict(row)
        item["is_outlier"] = is_outlier
        score = to_decimal(item.get("score")) or 0
        if is_outlier:
            item["selected"] = False
            item["exclusion_reason"] = "outlier"
        elif score < FALLBACK_SELECT_SCORE:
            item["selected"] = False
            item["exclusion_reason"] = "low_score"
        else:
            item["selected"] = default_selected(score, False)
            item["exclusion_reason"] = None
        scored.append(item)
    selected = [row for row in scored if row.get("selected")]
    selected.sort(key=lambda row: to_decimal(row.get("score")) or 0, reverse=True)
    keep_ids = {id(row) for row in selected[:MAX_SELECTED]}
    for row in scored:
        if row.get("selected") and id(row) not in keep_ids:
            row["selected"] = False
            row["exclusion_reason"] = row.get("exclusion_reason") or "over_limit"
    if not any(row.get("selected") for row in scored):
        ranked = sorted(
            scored,
            key=lambda row: to_decimal(row.get("score")) or 0,
            reverse=True,
        )
        for row in ranked[:3]:
            if row.get("exclusion_reason") != "outlier":
                row["selected"] = True
                row["exclusion_reason"] = None
    return scored


def _persist_metrics(acm_id, organization_id, subject, rows, language="es"):
    metrics = compute_metrics(rows, subject)
    explanation = build_acm_explanation(subject, rows, metrics, language=language)
    status = STATUS_READY if metrics.get("can_finalize") else STATUS_DRAFT
    update_acm(
        acm_id,
        organization_id,
        status=status,
        estimated_value=(
            str(metrics["estimated_value"]) if metrics.get("estimated_value") is not None else None
        ),
        suggested_min_value=(
            str(metrics["suggested_min"]) if metrics.get("suggested_min") is not None else None
        ),
        suggested_max_value=(
            str(metrics["suggested_max"]) if metrics.get("suggested_max") is not None else None
        ),
        median_price_per_m2=(
            str(metrics["median_ppm2"]) if metrics.get("median_ppm2") is not None else None
        ),
        average_price_per_m2=(
            str(metrics["average_ppm2"]) if metrics.get("average_ppm2") is not None else None
        ),
        explanation=explanation,
        area_basis=metrics.get("area_basis") or choose_area_basis(subject),
        metrics=metrics,
    )
    return metrics


def create_acm_for_property(
    organization_id,
    *,
    user,
    property_id,
    language="es",
):
    user = require_acm_agent(user)
    property_data = get_property_record(property_id, organization_id)
    if property_data is None:
        raise AcmError("acm_err_property_missing", 404)
    if int(property_data.get("agent_id") or 0) != int(user["agent_id"]):
        raise AcmError("acm_err_forbidden", 403)
    subject = _subject_snapshot(property_data)
    basis = choose_area_basis(subject)
    acm_id = create_acm(
        organization_id,
        agent_id=user["agent_id"],
        property_id=property_id,
        created_by_user_id=user.get("id"),
        currency=property_data.get("listing_currency") or "",
        subject_snapshot=subject,
        area_basis=basis,
    )
    rows = [_row_from_candidate(item, subject) for item in find_candidates(organization_id, property_data)]
    rows = _apply_outliers_and_selection(rows)
    for row in rows:
        add_comparable(organization_id, acm_id, row)
    stored = list_comparables(acm_id, organization_id)
    _persist_metrics(acm_id, organization_id, subject, stored, language=language)
    return get_acm_view(acm_id, organization_id, user=user, language=language)


def complete_property_area_and_create(
    organization_id,
    *,
    user,
    property_id,
    area,
    language="es",
):
    user = require_acm_agent(user)
    property_data = get_property_record(property_id, organization_id)
    if property_data is None:
        raise AcmError("acm_err_property_missing", 404)
    if int(property_data.get("agent_id") or 0) != int(user["agent_id"]):
        raise AcmError("acm_err_forbidden", 403)
    value = to_decimal(area)
    if value is None or value <= 0:
        raise AcmError("acm_err_manual_area", 400)
    from modules.database.properties_repository import update_property

    update_property(
        property_id,
        property_data.get("address") or "",
        property_data.get("jurisdiction") or "",
        organization_id,
        agent_id=property_data.get("agent_id"),
        covered_m2=str(value),
        total_m2=str(value),
    )
    return create_acm_for_property(
        organization_id,
        user=user,
        property_id=property_id,
        language=language,
    )


def get_owned_acm(acm_id, organization_id, user):
    user = require_acm_agent(user)
    acm = get_acm(acm_id, organization_id, agent_id=user["agent_id"])
    if acm is None:
        raise AcmError("acm_err_forbidden", 403)
    return acm


def _enrich_comparable(row, subject, language="es"):
    item = dict(row)
    item["display_area"] = display_area(item)
    item["valuation_valid"] = bool(
        item.get("selected") and is_valuation_valid(item, subject)
    )
    snapshot_location = item.get("snapshot_location") or ""
    bits = [part.strip() for part in snapshot_location.split(",") if part.strip()]
    item["location"] = location_parts(
        {
            "neighborhood": bits[0] if bits else "",
            "jurisdiction": bits[-1] if len(bits) > 1 else "",
        }
    )
    reasons = item.get("score_reasons") or explain_score(subject, item)
    item["score_reasons"] = reasons
    item["match_labels"] = [
        format_match_label(key, language=language)
        for key in reasons.get("matches") or []
        if isinstance(key, str)
    ]
    item["diff_labels"] = [
        format_diff_label(diff, language=language)
        for diff in reasons.get("diffs") or []
    ]
    item["source_label"] = source_label(item.get("source_type"), language=language)
    score = to_decimal(item.get("score")) or reasons.get("score")
    item["score_int"] = int(round(float(score or 0)))
    from modules.maps.geo import format_distance
    from modules.maps.location import parse_coordinate

    item["latitude"] = parse_coordinate(
        item.get("snapshot_latitude") or item.get("latitude"),
        kind="lat",
    )
    item["longitude"] = parse_coordinate(
        item.get("snapshot_longitude") or item.get("longitude"),
        kind="lng",
    )
    try:
        meters = float(item["distance_meters"]) if item.get("distance_meters") not in (None, "") else None
    except (TypeError, ValueError):
        meters = None
    item["distance_meters"] = meters
    item["distance_label"] = format_distance(meters, language) if meters is not None else ""
    if item["distance_label"]:
        item["match_labels"] = [item["distance_label"]] + [
            label for label in item["match_labels"] if label != translate("acm_match_distance", language)
        ]
    return item


def _attach_acm_photos(view, organization_id, property_data, enriched):
    from modules.property_sync.media import (
        get_property_cover_media,
        get_property_media_for_generation,
        get_property_media_url,
        list_covers_for_properties,
    )

    property_id = (property_data or {}).get("id")
    cover = get_property_cover_media(property_data) if property_data else None
    gallery = (
        get_property_media_for_generation(property_data, limit=5)
        if property_data
        else []
    )
    cover_src = get_property_media_url(cover, property_id)
    extras = []
    for item in gallery:
        src = get_property_media_url(item, property_id)
        if src and src != cover_src:
            extras.append(src)
        if len(extras) >= 4:
            break
    view["photo_url"] = cover_src
    view["photo_urls"] = ([cover_src] + extras)[:5] if cover_src else extras[:5]
    if view.get("subject") is not None:
        view["subject"]["photo_url"] = cover_src
    comp_ids = [
        row.get("comparable_property_id")
        for row in enriched or []
        if row.get("comparable_property_id")
    ]
    covers = list_covers_for_properties(organization_id, comp_ids)
    for row in enriched or []:
        linked_id = row.get("comparable_property_id")
        linked_cover = covers.get(int(linked_id)) if linked_id else None
        row["photo_url"] = get_property_media_url(linked_cover, linked_id)


def _attach_source_badges(organization_id, enriched, language):
    from modules.database.properties_repository import get_property_record

    for row in enriched or []:
        linked_id = row.get("comparable_property_id")
        if not linked_id:
            continue
        linked = get_property_record(linked_id, organization_id)
        if (linked or {}).get("external_source") == "redremax":
            row["source_key"] = "redremax"
            row["source_label"] = translate("acm_source_redremax", language)
            continue
        row["source_key"] = row.get("source_type")


def _acm_map_payload(subject, rows, language="es"):
    from modules.maps.location import has_coordinates, parse_coordinate

    target = None
    if has_coordinates(subject):
        target = {
            "lat": subject.get("latitude"),
            "lng": subject.get("longitude"),
            "title": subject.get("address") or "",
            "kind": "target",
        }
    markers = []
    for row in rows or []:
        lat = parse_coordinate(row.get("latitude"), kind="lat")
        lng = parse_coordinate(row.get("longitude"), kind="lng")
        if lat is None or lng is None:
            continue
        markers.append(
            {
                "id": row.get("id"),
                "lat": lat,
                "lng": lng,
                "title": row.get("external_reference") or row.get("snapshot_location") or "",
                "distance": row.get("distance_label") or "",
                "price": row.get("snapshot_price"),
                "currency": row.get("snapshot_currency"),
                "ppm2": row.get("snapshot_price_per_m2"),
                "score": row.get("score_int"),
                "source": row.get("source_type"),
                "href": (
                    f"/properties/{row['comparable_property_id']}"
                    if row.get("comparable_property_id")
                    else ""
                ),
                "selected": bool(row.get("selected")),
            }
        )
    return {
        "available": bool(target or markers),
        "target": target,
        "markers": markers,
        "message_key": "acm_map_placeholder" if not (target or markers) else "",
    }


def get_acm_view(acm_id, organization_id, *, user, language="es"):
    acm = get_owned_acm(acm_id, organization_id, user)
    property_data = get_property_record(acm["property_id"], organization_id)
    comparables = list_comparables(acm_id, organization_id)
    subject = acm.get("subject_snapshot") or _subject_snapshot(property_data or {})
    quality = subject_quality(subject)
    metrics = acm.get("metrics") or compute_metrics(comparables, subject)
    enriched = [_enrich_comparable(row, subject, language=language) for row in comparables]
    table_rows = [
        row for row in enriched if row.get("selected")
    ][:5]
    chart_points = []
    for row in enriched:
        if not row.get("display_area") or not row.get("snapshot_price_per_m2"):
            continue
        chart_points.append(
            {
                "label": row.get("external_reference") or row.get("snapshot_location") or "",
                "ppm2": float(to_decimal(row["snapshot_price_per_m2"])),
                "price": float(to_decimal(row.get("snapshot_price")) or 0),
                "area": float(row["display_area"]),
                "kind": row.get("snapshot_price_kind") or "",
                "selected": bool(row.get("selected")),
            }
        )
    subject_ppm2 = price_per_m2(subject.get("listing_price"), display_area(subject))
    reference_ppm2 = price_per_m2(acm.get("estimated_value"), display_area(subject))
    source_counts = {}
    for row in enriched:
        key = row.get("source_type") or "other_external"
        source_counts[key] = source_counts.get(key, 0) + 1
    source_chart = [
        {"label": source_label(key, language=language), "count": count, "id": key}
        for key, count in source_counts.items()
    ]
    checks = quality.get("checks") or {}
    key_fields = (
        "property_type",
        "price",
        "currency",
        "location",
        "area",
        "rooms",
    )
    quality_complete = sum(1 for key in key_fields if checks.get(key) == "ok")
    view = {
        "acm": acm,
        "property": property_data,
        "subject": subject,
        "quality": quality,
        "quality_summary": {
            "subject_complete": quality_complete,
            "subject_total": len(key_fields),
            "valid": int(metrics.get("valuation_count") or 0),
            "found": int(metrics.get("found_count") or len(enriched)),
            "closings": int(metrics.get("closing_count") or 0),
            "geo_available": bool(
                subject.get("latitude") is not None
                and subject.get("longitude") is not None
            ),
            "confidence": metrics.get("confidence") or "low",
        },
        "comparables": enriched,
        "table_rows": table_rows,
        "chart_points": chart_points,
        "source_chart": source_chart,
        "subject_ppm2": str(subject_ppm2) if subject_ppm2 is not None else None,
        "reference_ppm2": str(reference_ppm2) if reference_ppm2 is not None else None,
        "metrics": metrics,
        "can_finalize": bool(metrics.get("can_finalize")),
        "min_valid_required": MIN_VALID_COMPS,
        "disclaimer": translate("acm_disclaimer", language=language),
        "source_catalog": source_catalog(language=language),
        "map": _acm_map_payload(subject, enriched, language),
        "best_comparable": most_similar_comparable(enriched),
        "photo_url": None,
        "photo_urls": [],
    }
    _attach_acm_photos(view, organization_id, property_data, enriched)
    _attach_source_badges(organization_id, enriched, language)
    insight_facts = []
    seen_facts = set()
    for row in enriched:
        if not row.get("selected"):
            continue
        for label in row.get("match_labels") or []:
            if not label or label in seen_facts:
                continue
            seen_facts.add(label)
            insight_facts.append(label)
            if len(insight_facts) >= 5:
                break
        if len(insight_facts) >= 5:
            break
    view["insight_facts"] = insight_facts
    from modules.agent_branding import get_agent_branding
    from modules.acm_engine import confidence_percent

    view["agent_branding"] = get_agent_branding(acm["agent_id"], organization_id, language=language)
    metrics["confidence_pct"] = confidence_percent(metrics)
    facts = build_acm_facts(view, language=language)
    explained = explain_acm(facts, language=language)
    view["facts"] = facts
    view["market_highlights"] = fallback_market_highlights(facts, language=language)
    view["ai_explanation"] = explained
    return view


def preview_exclude_comparable(acm_id, organization_id, *, user, comparable_id, language="es"):
    view = get_acm_view(acm_id, organization_id, user=user, language=language)
    row = next(
        (
            item
            for item in view["comparables"]
            if int(item.get("id") or 0) == int(comparable_id)
        ),
        None,
    )
    if row is None:
        raise AcmError("acm_err_comparable_missing", 404)
    return {
        "acm_id": acm_id,
        "comparable": row,
        "confirm_required": True,
        "locked": view["acm"]["status"] == STATUS_FINALIZED,
    }


def simulate_list_price(acm_id, organization_id, *, user, proposed_price, language="es"):
    view = get_acm_view(acm_id, organization_id, user=user, language=language)
    acm = view["acm"]
    scenario = compute_price_scenario(
        proposed_price,
        acm.get("estimated_value"),
        acm.get("suggested_min_value"),
        acm.get("suggested_max_value"),
    )
    view["scenario"] = scenario
    return view


def recalculate_acm(acm_id, organization_id, *, user, language="es"):
    acm = get_owned_acm(acm_id, organization_id, user)
    if acm["status"] == STATUS_FINALIZED:
        raise AcmError("acm_err_finalized_locked", 400)
    property_data = get_property_record(acm["property_id"], organization_id)
    subject = acm.get("subject_snapshot") or _subject_snapshot(property_data or {})
    rows = list_comparables(acm_id, organization_id)
    ppm2_rows = []
    for row in rows:
        basis = choose_area_basis(subject)
        area = comparable_area(
            {
                "covered_m2": row.get("snapshot_covered_area"),
                "total_m2": row.get("snapshot_total_area"),
            },
            basis,
        ) or display_area(
            {
                "covered_m2": row.get("snapshot_covered_area"),
                "total_m2": row.get("snapshot_total_area"),
            }
        )
        ppm2 = price_per_m2(row.get("snapshot_price"), area)
        update_comparable(
            row["id"],
            organization_id,
            snapshot_price_per_m2=str(ppm2) if ppm2 is not None else None,
        )
        item = dict(row)
        item["snapshot_price_per_m2"] = str(ppm2) if ppm2 is not None else None
        ppm2_rows.append(item)
    flags = flag_outliers([row.get("snapshot_price_per_m2") for row in ppm2_rows])
    for row, is_outlier in zip(ppm2_rows, flags):
        reason = row.get("exclusion_reason")
        if is_outlier:
            reason = "outlier"
        elif reason == "outlier":
            reason = None
        update_comparable(
            row["id"],
            organization_id,
            is_outlier=is_outlier,
            exclusion_reason=reason,
            selected=False if is_outlier else row.get("selected"),
        )
        row["is_outlier"] = is_outlier
        row["exclusion_reason"] = reason
        if is_outlier:
            row["selected"] = False
    _persist_metrics(acm_id, organization_id, subject, ppm2_rows, language=language)
    return get_acm_view(acm_id, organization_id, user=user, language=language)


def set_comparable_selected(acm_id, organization_id, *, user, comparable_id, selected, language="es"):
    acm = get_owned_acm(acm_id, organization_id, user)
    if acm["status"] == STATUS_FINALIZED:
        raise AcmError("acm_err_finalized_locked", 400)
    row = get_comparable(comparable_id, organization_id, acm_id=acm_id)
    if row is None:
        raise AcmError("acm_err_comparable_missing", 404)
    reason = row.get("exclusion_reason")
    if selected and reason == "outlier":
        reason = "included_outlier"
    elif not selected and not reason:
        reason = "agent_excluded"
    elif selected and reason == "agent_excluded":
        reason = None
    update_comparable(
        comparable_id,
        organization_id,
        selected=bool(selected),
        exclusion_reason=reason,
    )
    return recalculate_acm(acm_id, organization_id, user=user, language=language)


def add_manual_comparable(acm_id, organization_id, *, user, payload, language="es"):
    acm = get_owned_acm(acm_id, organization_id, user)
    if acm["status"] == STATUS_FINALIZED:
        raise AcmError("acm_err_finalized_locked", 400)
    price = to_decimal(payload.get("price"))
    covered = to_decimal(payload.get("covered_m2") or payload.get("area"))
    total = to_decimal(payload.get("total_m2") or payload.get("area"))
    area = covered or total
    if price is None or price <= 0:
        raise AcmError("acm_err_manual_price")
    subject = acm.get("subject_snapshot") or {}
    price_kind = PRICE_CLOSING if payload.get("price_kind") == PRICE_CLOSING else PRICE_MANUAL
    candidate = {
        "covered_m2": covered,
        "total_m2": total,
        "rooms": payload.get("rooms"),
        "bedrooms": payload.get("bedrooms"),
        "bathrooms": payload.get("bathrooms"),
        "property_type": payload.get("property_type") or subject.get("property_type"),
        "neighborhood": payload.get("neighborhood") or payload.get("location"),
        "jurisdiction": payload.get("jurisdiction") or subject.get("jurisdiction"),
        "listing_currency": payload.get("currency") or acm.get("currency"),
        "parking_spaces": payload.get("parking_spaces"),
        "price_kind": price_kind,
        "features_json": payload.get("features_json"),
    }
    reasons = explain_score(subject, candidate) if subject else {"score": 0, "matches": [], "diffs": []}
    ppm2 = price_per_m2(price, area)
    location = payload.get("location") or candidate["neighborhood"] or ""
    add_comparable(
        organization_id,
        acm_id,
        {
            "comparable_property_id": None,
            "source_type": SOURCE_MANUAL,
            "external_reference": payload.get("reference") or payload.get("address") or location,
            "selected": True,
            "snapshot_price": str(price),
            "snapshot_currency": payload.get("currency") or acm.get("currency"),
            "snapshot_total_area": str(total) if total is not None else None,
            "snapshot_covered_area": str(covered) if covered is not None else None,
            "snapshot_rooms": payload.get("rooms"),
            "snapshot_bedrooms": payload.get("bedrooms"),
            "snapshot_bathrooms": payload.get("bathrooms"),
            "snapshot_parking": payload.get("parking_spaces"),
            "snapshot_property_type": candidate["property_type"],
            "snapshot_location": location,
            "snapshot_price_per_m2": str(ppm2) if ppm2 is not None else None,
            "snapshot_price_kind": price_kind,
            "snapshot_url": payload.get("url"),
            "snapshot_observed_at": payload.get("observed_at") or payload.get("date"),
            "score": str(reasons.get("score") or 0),
            "score_reasons": reasons,
            "area_source": "manual_external",
            "notes": payload.get("notes") or "",
        },
    )
    return recalculate_acm(acm_id, organization_id, user=user, language=language)


def override_comparable_area(acm_id, organization_id, *, user, comparable_id, area, language="es"):
    """Store an ACM-only area override. Does not change Property."""
    acm = get_owned_acm(acm_id, organization_id, user)
    if acm["status"] == STATUS_FINALIZED:
        raise AcmError("acm_err_finalized_locked", 400)
    row = get_comparable(comparable_id, organization_id, acm_id=acm_id)
    if row is None:
        raise AcmError("acm_err_comparable_missing", 404)
    value = to_decimal(area)
    if value is None or value <= 0:
        raise AcmError("acm_err_manual_area")
    basis = choose_area_basis(acm.get("subject_snapshot") or {}) or AREA_COVERED
    fields = {
        "area_source": "manual_acm",
        "area_override_by_user_id": user.get("id"),
    }
    if basis == AREA_COVERED:
        fields["snapshot_covered_area"] = str(value)
    else:
        fields["snapshot_total_area"] = str(value)
    update_comparable(comparable_id, organization_id, **fields)
    return recalculate_acm(acm_id, organization_id, user=user, language=language)


def preview_acm_property(organization_id, *, user, property_id, language="es"):
    user = require_acm_agent(user)
    property_data = get_property_record(property_id, organization_id)
    if property_data is None:
        raise AcmError("acm_err_property_missing", 404)
    if int(property_data.get("agent_id") or 0) != int(user["agent_id"]):
        raise AcmError("acm_err_forbidden", 403)
    subject = _subject_snapshot(property_data)
    return {
        "property": property_data,
        "subject": subject,
        "quality": subject_quality(subject),
        "language": language,
    }


def refresh_draft(acm_id, organization_id, *, user, language="es", filters=None):
    acm = get_owned_acm(acm_id, organization_id, user)
    if acm["status"] == STATUS_FINALIZED:
        raise AcmError("acm_err_finalized_locked", 400)
    property_data = get_property_record(acm["property_id"], organization_id)
    if property_data is None:
        raise AcmError("acm_err_property_missing", 404)
    from modules.database.connection import get_connection

    connection = get_connection()
    try:
        connection.execute(
            "DELETE FROM property_acm_comparables "
            "WHERE acm_id = ? AND organization_id = ? AND source_type != ?",
            (acm_id, organization_id, SOURCE_MANUAL),
        )
        connection.commit()
    finally:
        connection.close()
    subject = _subject_snapshot(property_data)
    update_acm(acm_id, organization_id, subject_snapshot=subject, currency=property_data.get("listing_currency"))
    rows = [
        _row_from_candidate(item, subject)
        for item in find_candidates(organization_id, property_data, filters=filters)
    ]
    rows = _apply_outliers_and_selection(rows)
    for row in rows:
        add_comparable(organization_id, acm_id, row)
    return recalculate_acm(acm_id, organization_id, user=user, language=language)


def finalize_acm(acm_id, organization_id, *, user, language="es"):
    view = recalculate_acm(acm_id, organization_id, user=user, language=language)
    if not view.get("can_finalize"):
        raise AcmError("acm_err_not_ready", 400)
    update_acm(
        acm_id,
        organization_id,
        status=STATUS_FINALIZED,
        finalized_at=datetime.utcnow().replace(microsecond=0).isoformat(),
    )
    return get_acm_view(acm_id, organization_id, user=user, language=language)


def duplicate_acm(acm_id, organization_id, *, user, language="es"):
    view = get_acm_view(acm_id, organization_id, user=user, language=language)
    created = create_acm_for_property(
        organization_id,
        user=user,
        property_id=view["acm"]["property_id"],
        language=language,
    )
    return created


def list_agent_acms(organization_id, *, user):
    user = require_acm_agent(user)
    items = list_acms(organization_id, agent_id=user["agent_id"])
    for item in items:
        property_data = get_property_record(item["property_id"], organization_id)
        item["address"] = (property_data or {}).get("address") or ""
        metrics = item.get("metrics") or {}
        item["confidence"] = metrics.get("confidence") or "low"
        item["valuation_count"] = metrics.get("valuation_count") or metrics.get("used_count") or 0
        item["found_count"] = metrics.get("found_count") or 0
    return items


def agent_contact_for_acm(view):
    """Contact comes from the ACM Agent, never from current_user or admin."""
    from modules.agent_branding import get_agent_branding

    acm = view.get("acm") or {}
    branding = get_agent_branding(
        acm.get("agent_id"),
        acm.get("organization_id"),
    )
    if not branding:
        return None
    return branding


def build_acm_explanation(subject, rows, metrics, language="es"):
    used = [
        row
        for row in rows
        if row.get("selected") and is_valuation_valid(row, subject)
    ]
    loc = location_parts(subject)
    zone = loc["primary"] or loc["secondary"] or ""
    areas = [display_area(row) for row in used]
    areas = [item for item in areas if item]
    closings = int(metrics.get("closing_count") or 0)
    facts = {
        "count": len(used),
        "found": int(metrics.get("found_count") or len(rows)),
        "closings": closings,
        "zone": zone,
        "median": metrics.get("median_ppm2"),
        "currency": subject.get("listing_currency") or "USD",
        "area_min": min(areas) if areas else None,
        "area_max": max(areas) if areas else None,
        "subject_area": display_area(subject),
        "low": metrics.get("suggested_min"),
        "high": metrics.get("suggested_max"),
    }
    if facts["count"] <= 0:
        return translate("acm_explanation_empty", language=language)
    if facts["subject_area"] and facts["low"] and facts["high"]:
        return translate(
            "acm_explanation_full",
            language=language,
            count=facts["count"],
            closings=facts["closings"],
            zone=facts["zone"],
            currency=facts["currency"],
            median=facts["median"],
            subject_area=facts["subject_area"],
            low=facts["low"],
            high=facts["high"],
        )
    if facts["area_min"] is not None and facts["area_max"] is not None:
        return translate(
            "acm_explanation",
            language=language,
            count=facts["count"],
            zone=facts["zone"],
            currency=facts["currency"],
            median=facts["median"],
            area_min=facts["area_min"],
            area_max=facts["area_max"],
        )
    return translate(
        "acm_explanation_no_area_range",
        language=language,
        count=facts["count"],
        zone=facts["zone"],
        currency=facts["currency"],
        median=facts["median"],
    )
