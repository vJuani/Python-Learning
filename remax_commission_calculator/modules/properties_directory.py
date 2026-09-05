"""
Properties directory view-model (properties page mockup).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from modules.dashboard_home import _stage_for_operation
from modules.database.operations_repository import filter_operations
from modules.database.properties_repository import STATUS_APPROVED
from modules.database.tenant import require_organization_id
from modules.i18n import translate
from modules.property_inventory import decorate_property_for_display
from modules.property_types import (
    COMMERCIAL_STATUSES,
    LISTING_CURRENCIES,
    LISTING_PURPOSES,
    PROPERTY_TYPES,
)
from modules.validators import date_to_sortable


RESIDENTIAL_TYPES = frozenset({"apartment", "house", "ph", "land"})
COMMERCIAL_TYPES = frozenset({"commercial", "office"})

DISPLAY_STATUS_BUCKETS = (
    "active",
    "in_progress",
    "pending",
    "reserved",
    "closed",
    "rejected",
)


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


def _today():
    return date.today()


def _pct_change(current, previous):
    current = float(current or 0)
    previous = float(previous or 0)
    if previous <= 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def _parse_submitted_date(value):
    if not value:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    if "T" in raw:
        raw = raw.split("T", 1)[0]

    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    return None


def _property_type_label(property_type, language):
    if not property_type:
        return "—"

    return _t(f"property_type_{property_type}", language)


def _property_category(property_type):
    if property_type in COMMERCIAL_TYPES:
        return "commercial"
    return "residential"


def _property_category_label(property_type, language):
    category = _property_category(property_type)
    if category == "commercial":
        return _t("properties_category_commercial", language)
    return _t("properties_category_residential", language)


def _property_title(property_row, language):
    address = (property_row.get("address") or "").strip()
    type_label = _property_type_label(
        property_row.get("property_type"),
        language,
    )

    if address and type_label != "—":
        short_address = address.split(",")[0].strip()
        if short_address.lower().startswith(type_label.lower()):
            return short_address
        return f"{type_label} {short_address}"

    return address or type_label or "—"


def _build_operation_map(organization_id, *, agent_id=None):
    operation_map = {}

    for status in ("draft", "pending", "approved"):
        for operation in filter_operations(
            organization_id,
            agent_id=agent_id,
            status=status,
        ):
            property_db_id = operation.get("property_db_id")
            if property_db_id is None:
                continue

            current = operation_map.get(property_db_id)
            if current is None:
                operation_map[property_db_id] = operation
                continue

            if date_to_sortable(operation.get("date") or "") > date_to_sortable(
                current.get("date") or ""
            ):
                operation_map[property_db_id] = operation

    return operation_map


def _resolve_display_status(property_row, operation_map, language):
    status = property_row.get("status") or STATUS_APPROVED

    if status == "pending":
        return {
            "label": _t("properties_status_pending", language),
            "tone": "pending",
            "bucket": "pending",
        }

    if status == "rejected":
        return {
            "label": _t("properties_status_rejected", language),
            "tone": "rejected",
            "bucket": "rejected",
        }

    operation = operation_map.get(property_row["id"])
    if not operation:
        return {
            "label": _t("properties_status_active", language),
            "tone": "active",
            "bucket": "active",
        }

    stage = _stage_for_operation(operation, language)
    tone = stage.get("tone") or "neutral"
    bucket = "in_progress"

    if tone == "reservation":
        bucket = "reserved"
    elif tone == "closing":
        bucket = "closed"
    elif tone in ("proposal", "negotiation"):
        bucket = "in_progress"

    if bucket == "closed":
        label = _t("properties_status_closed", language)
        css_tone = "closed"
    elif bucket == "reserved":
        label = _t("properties_status_reserved", language)
        css_tone = "reserved"
    elif bucket == "in_progress":
        label = _t("properties_status_in_progress", language)
        css_tone = "progress"
    else:
        label = stage.get("label") or _t("properties_status_in_progress", language)
        css_tone = "progress"

    return {
        "label": label,
        "tone": css_tone,
        "bucket": bucket,
    }


def _count_created_on_day(properties, day):
    count = 0
    for item in properties:
        submitted = _parse_submitted_date(item.get("submitted_at"))
        if submitted == day:
            count += 1
    return count


def _occupancy_rate(items, operation_map):
    if not items:
        return 0.0

    occupied = sum(
        1 for item in items if item["id"] in operation_map
    )
    return round((occupied / len(items)) * 100, 1)


def _submitted_at_sort_key(value):
    parsed = _parse_submitted_date(value)
    if parsed is None:
        return ""
    return parsed.strftime("%Y%m%d")


def _sort_properties(rows, sort_key):
    if sort_key == "price_asc":
        return sorted(
            rows,
            key=lambda item: (
                item["listing_price"] is None,
                item["listing_price"] or 0,
                -item["id"],
            ),
        )
    if sort_key == "price_desc":
        return sorted(
            rows,
            key=lambda item: (
                item["listing_price"] is None,
                -(item["listing_price"] or 0),
                -item["id"],
            ),
        )

    return sorted(
        rows,
        key=lambda item: (
            _submitted_at_sort_key(item.get("submitted_at")),
            item["id"],
        ),
        reverse=True,
    )


def build_properties_directory(
    organization_id,
    properties,
    raw_filters,
    *,
    agents=None,
    language="es",
):
    language = language if language in ("es", "en") else "es"
    organization_id = require_organization_id(organization_id)
    agents = agents or []

    operation_map = _build_operation_map(organization_id)

    rows = []
    for property_row in properties:
        property_type = property_row.get("property_type")
        display_status = _resolve_display_status(
            property_row,
            operation_map,
            language,
        )
        agent_name = property_row.get("agent_name") or "—"
        parts = agent_name.split()

        decorated = decorate_property_for_display(property_row, language)
        rows.append(
            {
                **decorated,
                "title": _property_title(property_row, language),
                "code": f"PROP-{property_row['id']:05d}",
                "type_label": _property_type_label(property_type, language),
                "category_label": _property_category_label(
                    property_type,
                    language,
                ),
                "category": _property_category(property_type),
                "currency": decorated.get("listing_currency"),
                "display_status": display_status,
                "agent_initials": (
                    f"{parts[0][0:1].upper()}"
                    f"{parts[1][0:1].upper() if len(parts) > 1 else ''}"
                ),
            }
        )

    today = _today()
    yesterday = today - timedelta(days=1)

    total_count = len(properties)
    residential_items = [
        item for item in properties
        if _property_category(item.get("property_type")) == "residential"
    ]
    commercial_items = [
        item for item in properties
        if _property_category(item.get("property_type")) == "commercial"
    ]

    occupancy = _occupancy_rate(
        [item for item in properties if item.get("status") == STATUS_APPROVED],
        operation_map,
    )
    residential_occupancy = _occupancy_rate(residential_items, operation_map)
    commercial_occupancy = _occupancy_rate(commercial_items, operation_map)

    all_rows = list(rows)

    search_q = (raw_filters.get("q") or raw_filters.get("address") or "").strip().lower()
    type_filter = (raw_filters.get("type") or "").strip()
    status_filter = (raw_filters.get("status") or "").strip()
    agent_filter = (raw_filters.get("agent_id") or "").strip()
    sort_key = (raw_filters.get("sort") or "recent").strip() or "recent"

    if search_q:
        rows = [
            row
            for row in rows
            if search_q in (row.get("address") or "").lower()
            or search_q in (row.get("title") or "").lower()
            or search_q in row["code"].lower()
            or search_q in (row.get("external_id") or "").lower()
            or search_q in (row.get("neighborhood") or "").lower()
        ]

    if type_filter and type_filter in PROPERTY_TYPES:
        rows = [
            row for row in rows
            if row.get("property_type") == type_filter
        ]

    if status_filter:
        rows = [
            row
            for row in rows
            if row["display_status"]["bucket"] == status_filter
            or row.get("status") == status_filter
        ]

    if agent_filter:
        try:
            agent_id = int(agent_filter)
            rows = [row for row in rows if row.get("agent_id") == agent_id]
        except (TypeError, ValueError):
            pass

    rows = _sort_properties(rows, sort_key)

    page_size = int(raw_filters.get("per_page") or 8)
    page_size = max(5, min(page_size, 50))
    page = int(raw_filters.get("page") or 1)
    page = max(1, page)
    total_rows = len(rows)
    total_pages = max(1, (total_rows + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]

    featured = sorted(
        [
            row for row in all_rows
            if row.get("status") == STATUS_APPROVED
            and row.get("listing_price") is not None
        ],
        key=lambda item: item.get("listing_price") or 0,
        reverse=True,
    )[:4]

    status_summary = []
    bucket_labels = {
        "active": "properties_status_active",
        "in_progress": "properties_status_in_progress",
        "pending": "properties_status_pending",
        "reserved": "properties_status_reserved",
        "closed": "properties_status_closed",
        "rejected": "properties_status_rejected",
    }
    bucket_counts = {bucket: 0 for bucket in DISPLAY_STATUS_BUCKETS}

    for row in all_rows:
        bucket = row["display_status"]["bucket"]
        if bucket in bucket_counts:
            bucket_counts[bucket] += 1

    tone_map = {
        "active": "active",
        "in_progress": "progress",
        "pending": "pending",
        "reserved": "reserved",
        "closed": "closed",
        "rejected": "rejected",
    }

    for bucket in DISPLAY_STATUS_BUCKETS:
        count = bucket_counts[bucket]
        if count <= 0:
            continue
        status_summary.append(
            {
                "bucket": bucket,
                "label": _t(bucket_labels[bucket], language),
                "count": count,
                "tone": tone_map[bucket],
            }
        )

    return {
        "kpis": {
            "total": total_count,
            "total_trend": _pct_change(
                _count_created_on_day(properties, today),
                _count_created_on_day(properties, yesterday),
            ),
            "residential": len(residential_items),
            "residential_trend": _pct_change(
                len([
                    item for item in residential_items
                    if _parse_submitted_date(item.get("submitted_at")) == today
                ]),
                len([
                    item for item in residential_items
                    if _parse_submitted_date(item.get("submitted_at")) == yesterday
                ]),
            ),
            "commercial": len(commercial_items),
            "commercial_trend": _pct_change(
                len([
                    item for item in commercial_items
                    if _parse_submitted_date(item.get("submitted_at")) == today
                ]),
                len([
                    item for item in commercial_items
                    if _parse_submitted_date(item.get("submitted_at")) == yesterday
                ]),
            ),
            "occupancy": occupancy,
            "occupancy_trend": None,
        },
        "rows": page_rows,
        "pagination": {
            "page": page,
            "per_page": page_size,
            "total": total_rows,
            "total_pages": total_pages,
            "from_row": start + 1 if total_rows else 0,
            "to_row": min(start + page_size, total_rows),
        },
        "filters": {
            "q": raw_filters.get("q") or raw_filters.get("address") or "",
            "type": type_filter,
            "status": status_filter,
            "agent_id": agent_filter,
            "sort": sort_key,
            "jurisdiction": raw_filters.get("jurisdiction") or "",
            "min_price": raw_filters.get("min_price") or "",
            "max_price": raw_filters.get("max_price") or "",
            "neighborhood": raw_filters.get("neighborhood") or "",
            "listing_purpose": raw_filters.get("listing_purpose") or "",
            "commercial_status": raw_filters.get("commercial_status") or "",
            "listing_currency": raw_filters.get("listing_currency") or "",
            "per_page": page_size,
        },
        "agent_options": agents,
        "property_types": PROPERTY_TYPES,
        "listing_purposes": LISTING_PURPOSES,
        "commercial_statuses": COMMERCIAL_STATUSES,
        "listing_currencies": LISTING_CURRENCIES,
        "featured": featured,
        "occupancy_breakdown": [
            {
                "label": _t("properties_category_residential", language),
                "percent": residential_occupancy,
            },
            {
                "label": _t("properties_category_commercial", language),
                "percent": commercial_occupancy,
            },
        ],
        "occupancy_global": occupancy,
        "status_summary": status_summary,
    }
