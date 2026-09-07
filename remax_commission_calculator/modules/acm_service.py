"""ACM orchestration. Deterministic valuation. Agent-scoped."""

from __future__ import annotations

from datetime import datetime

from modules.acm_engine import (
    AREA_COVERED,
    FALLBACK_SELECT_SCORE,
    MAX_SELECTED,
    PRICE_CLOSING,
    PRICE_LISTING,
    PRICE_MANUAL,
    SOURCE_CLOSED,
    SOURCE_INTERNAL,
    SOURCE_MANUAL,
    choose_area_basis,
    comparable_area,
    compute_metrics,
    default_selected,
    flag_outliers,
    price_per_m2,
    score_comparable,
    to_decimal,
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
from modules.database.users_repository import get_user_by_agent_id
from modules.i18n import translate
from modules.property_brochure import resolve_property_agent_contact


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
        "covered_m2": property_data.get("covered_m2"),
        "total_m2": property_data.get("total_m2"),
        "parking_spaces": property_data.get("parking_spaces"),
        "features": property_data.get("features") or {},
        "commercial_status": property_data.get("commercial_status"),
    }


def _location_label(item):
    hood = (item.get("neighborhood") or "").strip()
    jur = (item.get("jurisdiction") or "").strip()
    if hood and jur:
        return f"{hood}, {jur}"
    return hood or jur or ""


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


def _area_bounds(subject, basis):
    area = comparable_area(subject, basis)
    if area is None or area <= 0:
        return None, None
    return area * to_decimal("0.6"), area * to_decimal("1.4")


def find_candidates(organization_id, subject):
    basis = choose_area_basis(subject) or AREA_COVERED
    min_area, max_area = _area_bounds(subject, basis)
    internal = list_internal_candidates(
        organization_id,
        exclude_property_id=subject["id"],
        listing_purpose=subject.get("listing_purpose"),
        property_type=subject.get("property_type"),
        currency=subject.get("listing_currency"),
        neighborhood=subject.get("neighborhood"),
        jurisdiction=subject.get("jurisdiction"),
        min_area=min_area,
        max_area=max_area,
        area_column=basis or AREA_COVERED,
        limit=80,
    )
    closed = list_closed_candidates(
        organization_id,
        exclude_property_id=subject["id"],
        listing_purpose=subject.get("listing_purpose"),
        property_type=subject.get("property_type"),
        currency=subject.get("listing_currency"),
        neighborhood=subject.get("neighborhood"),
        jurisdiction=subject.get("jurisdiction"),
        limit=40,
    )
    seen = set()
    ranked = []
    for item in closed:
        item = dict(item)
        item["source_type"] = SOURCE_CLOSED
        item["price"] = item.get("sale_price")
        item["currency"] = item.get("operation_currency") or item.get("listing_currency")
        item["price_kind"] = PRICE_CLOSING
        item["recency_days"] = _recency_days(item.get("operation_date"))
        item["score"] = score_comparable(subject, item)
        key = ("closed", item.get("operation_id") or item["id"])
        seen.add(("prop", item["id"]))
        ranked.append(item)
    for item in internal:
        if ("prop", item["id"]) in seen:
            continue
        item = dict(item)
        item["source_type"] = SOURCE_INTERNAL
        item["price"] = item.get("listing_price")
        item["currency"] = item.get("listing_currency")
        item["price_kind"] = PRICE_LISTING
        item["recency_days"] = _recency_days(item.get("last_synced_at"))
        item["score"] = score_comparable(subject, item)
        ranked.append(item)
    ranked.sort(key=lambda row: row.get("score") or 0, reverse=True)
    return ranked[:MAX_SELECTED * 2]


def _row_from_candidate(candidate, subject):
    basis = choose_area_basis(subject)
    area = comparable_area(candidate, basis)
    ppm2 = price_per_m2(candidate.get("price"), area)
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
        "snapshot_property_type": candidate.get("property_type"),
        "snapshot_location": _location_label(candidate),
        "snapshot_price_per_m2": str(ppm2) if ppm2 is not None else None,
        "snapshot_listing_purpose": candidate.get("listing_purpose"),
        "snapshot_price_kind": candidate.get("price_kind"),
        "snapshot_operation_id": candidate.get("operation_id"),
        "snapshot_operation_date": candidate.get("operation_date"),
        "score": str(candidate.get("score") or 0),
        "is_outlier": False,
        "selected": True,
        "notes": None,
        "features_json": candidate.get("features_json"),
        "covered_m2": candidate.get("covered_m2"),
        "total_m2": candidate.get("total_m2"),
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
    status = STATUS_READY if metrics.get("estimated_value") is not None else STATUS_DRAFT
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
    return get_acm_view(acm_id, organization_id, user=user)


def get_owned_acm(acm_id, organization_id, user):
    user = require_acm_agent(user)
    acm = get_acm(acm_id, organization_id, agent_id=user["agent_id"])
    if acm is None:
        raise AcmError("acm_err_forbidden", 403)
    return acm


def get_acm_view(acm_id, organization_id, *, user, language="es"):
    acm = get_owned_acm(acm_id, organization_id, user)
    property_data = get_property_record(acm["property_id"], organization_id)
    comparables = list_comparables(acm_id, organization_id)
    subject = acm.get("subject_snapshot") or _subject_snapshot(property_data or {})
    return {
        "acm": acm,
        "property": property_data,
        "subject": subject,
        "comparables": comparables,
        "metrics": acm.get("metrics") or {},
        "disclaimer": translate("acm_disclaimer", language=language),
    }


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
    area = to_decimal(payload.get("area") or payload.get("covered_m2") or payload.get("total_m2"))
    if price is None or price <= 0:
        raise AcmError("acm_err_manual_price")
    subject = acm.get("subject_snapshot") or {}
    candidate = {
        "covered_m2": payload.get("covered_m2") or payload.get("area"),
        "total_m2": payload.get("total_m2") or payload.get("area"),
        "rooms": payload.get("rooms"),
        "bedrooms": payload.get("bedrooms"),
        "property_type": payload.get("property_type") or subject.get("property_type"),
        "neighborhood": payload.get("location") or payload.get("neighborhood"),
        "jurisdiction": subject.get("jurisdiction"),
        "listing_currency": payload.get("currency") or acm.get("currency"),
        "parking_spaces": payload.get("parking_spaces"),
        "features_json": payload.get("features_json"),
    }
    score = score_comparable(subject, candidate) if subject else 0
    ppm2 = price_per_m2(price, area)
    add_comparable(
        organization_id,
        acm_id,
        {
            "comparable_property_id": None,
            "source_type": SOURCE_MANUAL,
            "external_reference": payload.get("reference") or payload.get("location") or "",
            "selected": True,
            "snapshot_price": str(price),
            "snapshot_currency": payload.get("currency") or acm.get("currency"),
            "snapshot_total_area": str(payload.get("total_m2") or area or ""),
            "snapshot_covered_area": str(payload.get("covered_m2") or area or ""),
            "snapshot_rooms": payload.get("rooms"),
            "snapshot_bedrooms": payload.get("bedrooms"),
            "snapshot_property_type": candidate["property_type"],
            "snapshot_location": payload.get("location") or "",
            "snapshot_price_per_m2": str(ppm2) if ppm2 is not None else None,
            "snapshot_price_kind": PRICE_MANUAL,
            "score": str(score),
            "notes": payload.get("notes") or "",
        },
    )
    return recalculate_acm(acm_id, organization_id, user=user, language=language)


def refresh_draft(acm_id, organization_id, *, user, language="es"):
    acm = get_owned_acm(acm_id, organization_id, user)
    if acm["status"] == STATUS_FINALIZED:
        raise AcmError("acm_err_finalized_locked", 400)
    property_data = get_property_record(acm["property_id"], organization_id)
    if property_data is None:
        raise AcmError("acm_err_property_missing", 404)
    manuals = [
        row
        for row in list_comparables(acm_id, organization_id)
        if row.get("source_type") == SOURCE_MANUAL
    ]
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
    rows = [_row_from_candidate(item, subject) for item in find_candidates(organization_id, property_data)]
    rows = _apply_outliers_and_selection(rows)
    for row in rows:
        add_comparable(organization_id, acm_id, row)
    return recalculate_acm(acm_id, organization_id, user=user, language=language)


def finalize_acm(acm_id, organization_id, *, user, language="es"):
    view = recalculate_acm(acm_id, organization_id, user=user, language=language)
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
    return items


def agent_contact_for_acm(view):
    property_data = view.get("property") or {}
    contact = resolve_property_agent_contact(property_data)
    if contact:
        return contact
    user = get_user_by_agent_id(
        view["acm"]["agent_id"],
        view["acm"]["organization_id"],
    )
    if not user:
        return None
    name = " ".join(
        part for part in (user.get("first_name"), user.get("last_name")) if part
    ).strip() or user.get("username")
    return {
        "name": name,
        "phone": user.get("phone"),
        "email": user.get("email"),
        "role": "agent",
    }


def build_acm_explanation(subject, rows, metrics, language="es"):
    used = [
        row
        for row in rows
        if row.get("selected") and not row.get("is_outlier") and row.get("snapshot_price_per_m2")
    ]
    zone = subject.get("neighborhood") or subject.get("jurisdiction") or ""
    areas = []
    for row in used:
        area = comparable_area(
            {
                "covered_m2": row.get("snapshot_covered_area"),
                "total_m2": row.get("snapshot_total_area"),
            },
            metrics.get("area_basis") or choose_area_basis(subject),
        )
        if area:
            areas.append(area)
    facts = {
        "count": len(used),
        "zone": zone,
        "median": metrics.get("median_ppm2"),
        "currency": subject.get("listing_currency") or "USD",
        "area_min": min(areas) if areas else None,
        "area_max": max(areas) if areas else None,
    }
    if facts["count"] <= 0:
        return translate("acm_explanation_empty", language=language)
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
