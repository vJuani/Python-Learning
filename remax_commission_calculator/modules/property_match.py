"""Deterministic contact ↔ listing matcher.

Scores listings from ``listing_from_property()`` /
``listing_from_external_listing()`` / ``normalize_listing()``.
The matcher does not care whether a listing is internal or indexed
from a portal. The score uses only the criteria the contact defined.
A requested fact the listing does not verify stays in that total and
adds no points. Conflicts lower the score. No LLM. No FX invention.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

from modules.contacts import (
    merge_contact_preferences,
    normalize_preferences,
    whatsapp_digits,
)
from modules.i18n import translate
from modules.listing_sources import SOURCE_INTERNAL
from modules.listings_normalize import (
    listing_from_external_listing,
    listing_from_property,
    normalize_listing,
)
from modules.property_features import normalize_wanted_features
from modules.property_inventory import (
    decorate_property_for_display,
    format_listing_money,
    is_commercially_available,
)
from modules.property_types import (
    normalize_listing_purpose,
    normalize_property_type,
)
from modules.visit_outcome import format_budget_label, normalize_visit_outcome


MATCH = "match"
CONFLICT = "conflict"
UNKNOWN = "unknown"
SKIP = "skip"

SCORE_WEIGHTS = {
    "budget": 30,
    "zone": 25,
    "type": 15,
    "rooms": 10,
    "bedrooms": 10,
    "features": 10,
}

BELOW_MIN_RATIO = 0.8
BUDGET_SOFT_OVER = 0.05
BUDGET_HARD_OVER = 0.10
BUDGET_SOFT_RATIO = 21 / 30
BUDGET_STRETCH_RATIO = 9 / 30
HIDDEN_SCORE = 60
GOOD_SCORE = 75
EXCELLENT_SCORE = 90
CANDIDATE_LIMIT = 300
RECOMMENDATION_LIMIT = 5

HISTORY_RANK = {
    "negotiation": 0,
    "interested": 1,
    "visited": 2,
    "shared": 3,
    "new": 4,
    "discarded": 5,
}
HISTORY_LABELS = {
    "new": "matches_history_new",
    "shared": "matches_history_shared",
    "visited": "matches_history_visited",
    "interested": "matches_history_interested",
    "negotiation": "matches_history_negotiation",
    "discarded": "matches_discarded",
}
_ZONE_ALIAS_GROUPS = (
    frozenset({"caba", "capital", "capital federal"}),
)
_COUNT_RATIOS = {
    "rooms": {0: 1.0, 1: 0.8, 2: 0.5, -1: 0.3},
    "bedrooms": {0: 1.0, 1: 0.9, 2: 0.5, -1: 0.3},
}
_PARKING_FEATURE = "parking"


def _fold(text):
    normalized = unicodedata.normalize("NFD", str(text or ""))
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).casefold().strip()


def _first_name(name):
    return (str(name or "").strip().split() or [""])[0]


def _as_listing(item):
    if item is None:
        return {}, {}
    if item.get("external_listing_id") and item.get("source") not in (
        None,
        SOURCE_INTERNAL,
    ):
        return listing_from_external_listing(item), item
    already_normalized = item.get("source") or (
        "price" in item and "listing_price" not in item
    )
    if already_normalized:
        return normalize_listing(item), item
    return listing_from_property(item), item


def _listing_identity(listing, raw):
    source = listing.get("source") or raw.get("source") or SOURCE_INTERNAL
    external_listing_id = raw.get("external_listing_id")
    property_id = (
        raw.get("internal_property_id")
        or raw.get("property_id")
        or (raw.get("id") if source == SOURCE_INTERNAL else None)
    )
    if source != SOURCE_INTERNAL and not external_listing_id:
        external_listing_id = raw.get("id")
    return {
        "source": source,
        "internal_property_id": property_id,
        "property_id": property_id,
        "external_listing_id": external_listing_id,
        "external_url": listing.get("external_url") or raw.get("external_url"),
    }


def _visit_entry(visit_map, identity):
    property_id = identity.get("internal_property_id")
    external_id = identity.get("external_listing_id")
    if external_id is not None:
        entry = visit_map.get(("external", int(external_id)))
        if entry:
            return entry
    if property_id is not None:
        return visit_map.get(("internal", int(property_id))) or visit_map.get(
            int(property_id)
        ) or {}
    return {}


def resolve_criteria(contact, criteria_override=None):
    stored = normalize_preferences(
        (contact or {}).get("preferences")
        or (contact or {}).get("preferences_json")
    )
    override = normalize_preferences(criteria_override)
    if not override:
        return stored
    merged = dict(stored)
    merged.update(override)
    return normalize_preferences(merged)


def criteria_is_temporary(contact, criteria):
    stored = normalize_preferences(
        (contact or {}).get("preferences")
        or (contact or {}).get("preferences_json")
    )
    current = normalize_preferences(criteria)
    return stored != current


def currencies_comparable(left, right):
    want = str(left or "").strip().upper()
    have = str(right or "").strip().upper()
    if want not in ("USD", "ARS") or have not in ("USD", "ARS"):
        return False
    return want == have


def wanted_property_types(criteria):
    types = []
    seen = set()
    for raw in (criteria or {}).get("property_types") or []:
        normalized = normalize_property_type(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        types.append(normalized)
    return types


def _criterion(key, state, ratio=0.0, **extra):
    payload = {
        "key": key,
        "state": state,
        "ratio": ratio,
        "requested": state != SKIP,
    }
    payload.update(extra)
    return payload


def _skip(key):
    return _criterion(key, SKIP, 0.0)


def _zone_keys(text):
    folded = _fold(text)
    if not folded:
        return set()
    keys = set(re.findall(r"[a-z0-9]+", folded))
    for group in _ZONE_ALIAS_GROUPS:
        for alias in group:
            if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", folded):
                keys.update(group)
        if keys & group:
            keys.update(group)
    return keys


def _zone_hits(areas, places):
    place_keys = set()
    for place in places or []:
        place_keys.update(_zone_keys(place))
    if not place_keys:
        return False
    for area in areas or []:
        wanted = _zone_keys(area)
        if wanted and wanted & place_keys:
            return True
    return False


def _place_names(listing):
    return [
        listing.get(key)
        for key in ("neighborhood", "locality", "city", "jurisdiction")
        if listing.get(key)
    ]


def budget_over_ratio(price, maximum):
    """Point ratio for a same-currency price. None means more than +10%."""
    if maximum in (None, 0) or price is None:
        return None
    if price <= maximum:
        return 1.0
    over = (price - maximum) / maximum
    if over <= BUDGET_SOFT_OVER:
        return BUDGET_SOFT_RATIO
    if over <= BUDGET_HARD_OVER:
        return BUDGET_STRETCH_RATIO
    return None


def _score_budget(criteria, listing):
    budget = (criteria or {}).get("budget") or {}
    price = listing.get("price")
    want = str((budget.get("currency") or "")).strip().upper() or None
    have = listing.get("currency")
    if budget.get("min") is None and budget.get("max") is None:
        return _skip("budget")

    if not currencies_comparable(want, have):
        return _criterion(
            "budget",
            UNKNOWN,
            0.0,
            price=price,
            currency=have,
            currency_gap=True,
        )
    if price is None:
        return _criterion("budget", UNKNOWN, 0.0, price=price, currency=have)

    minimum = budget.get("min")
    maximum = budget.get("max")
    if maximum is not None and price > maximum:
        ratio = budget_over_ratio(price, maximum)
        over_pct = round(((price - maximum) / maximum) * 100, 1)
        if ratio is None:
            return _criterion(
                "budget",
                CONFLICT,
                0.0,
                price=price,
                currency=have,
                over_pct=over_pct,
            )
        return _criterion(
            "budget",
            MATCH,
            ratio,
            price=price,
            currency=have,
            partial=True,
            over_pct=over_pct,
        )
    if minimum is not None and price < minimum:
        return _criterion(
            "budget",
            MATCH,
            BELOW_MIN_RATIO,
            price=price,
            currency=have,
            partial=True,
        )
    return _criterion("budget", MATCH, 1.0, price=price, currency=have)


def _score_zone(criteria, listing):
    from modules.maps.geo import format_distance, need_geo_from_preferences, need_geo_ratio

    geo = need_geo_from_preferences(criteria)
    if geo:
        meters = listing.get("distance_meters")
        if meters is None:
            from modules.maps.geo import distance_between_coordinates

            meters = distance_between_coordinates(
                geo["center_latitude"],
                geo["center_longitude"],
                listing.get("latitude"),
                listing.get("longitude"),
            )
        ratio = need_geo_ratio(meters, geo["radius_m"])
        if ratio is None:
            return _criterion(
                "zone",
                CONFLICT,
                0.0,
                neighborhood=listing.get("neighborhood"),
                distance_meters=meters,
            )
        return _criterion(
            "zone",
            MATCH,
            ratio,
            neighborhood=listing.get("neighborhood"),
            distance_meters=meters,
            distance_label=format_distance(meters),
        )
    areas = (criteria or {}).get("areas") or []
    places = _place_names(listing)
    neighborhood = listing.get("neighborhood")
    if not areas:
        return _skip("zone")
    if not places:
        return _criterion("zone", UNKNOWN, 0.0, neighborhood=neighborhood)
    if _zone_hits(areas, places):
        return _criterion("zone", MATCH, 1.0, neighborhood=neighborhood or places[0])
    return _criterion("zone", CONFLICT, 0.0, neighborhood=neighborhood or places[0])


def _score_type(criteria, listing):
    wanted = wanted_property_types(criteria)
    have = listing.get("property_type")
    if not wanted:
        return _skip("type")
    if not have:
        return _criterion("type", UNKNOWN, 0.0, property_type=have)
    if have in wanted:
        return _criterion("type", MATCH, 1.0, property_type=have)
    return _criterion("type", CONFLICT, 0.0, property_type=have)


def _count_ratio(key, wanted, have):
    diff = int(have) - int(wanted)
    return _COUNT_RATIOS.get(key, {}).get(diff, 0.0)


def _score_count(key, criteria, listing):
    target = (criteria or {}).get(key)
    minimum = (criteria or {}).get(f"{key}_min")
    have = listing.get(key)
    if target is None and minimum is None:
        return _skip(key)
    anchor = target if target is not None else minimum
    if have is None:
        return _criterion(key, UNKNOWN, 0.0, wanted=anchor, actual=None)
    if minimum is not None and int(have) < int(minimum):
        return _criterion(key, CONFLICT, 0.0, wanted=anchor, actual=have, below_min=True)
    ratio = _count_ratio(key, anchor, have)
    state = MATCH if ratio > 0 else CONFLICT
    return _criterion(
        key,
        state,
        ratio,
        wanted=anchor,
        actual=have,
        partial=0 < ratio < 1,
    )


def feature_groups(criteria):
    """Legacy ``features`` stay preferred. Required is optional."""
    required = normalize_wanted_features((criteria or {}).get("required_features"))
    preferred = normalize_wanted_features((criteria or {}).get("preferred_features"))
    legacy = normalize_wanted_features((criteria or {}).get("features"))
    seen = set(required) | set(preferred)
    for key in legacy:
        if key not in seen:
            preferred.append(key)
            seen.add(key)
    return required, preferred


def _feature_presence(key, listing):
    if key == _PARKING_FEATURE:
        spaces = listing.get("parking_spaces")
        if spaces is not None:
            return MATCH if int(spaces) >= 1 else CONFLICT
    have = listing.get("features") or {}
    if key not in have:
        return UNKNOWN
    return MATCH if have.get(key) else CONFLICT


def _score_features(criteria, listing):
    required, preferred = feature_groups(criteria)
    if not required and not preferred:
        return _skip("features")

    items = []
    units = 0
    earned = 0
    known = False
    for key, weight in [(item, 2) for item in required] + [(item, 1) for item in preferred]:
        state = _feature_presence(key, listing)
        items.append({
            "key": key,
            "state": state,
            "required": key in required,
        })
        units += weight
        if state == MATCH:
            earned += weight
            known = True
        elif state == CONFLICT:
            known = True
    ratio = (earned / units) if units else 0.0
    if not known:
        state = UNKNOWN
    elif ratio > 0:
        state = MATCH
    else:
        state = CONFLICT
    return _criterion("features", state, ratio, items=items)


def score_dimensions(criteria, listing):
    return {
        "budget": _score_budget(criteria, listing),
        "zone": _score_zone(criteria, listing),
        "type": _score_type(criteria, listing),
        "rooms": _score_count("rooms", criteria, listing),
        "bedrooms": _score_count("bedrooms", criteria, listing),
        "features": _score_features(criteria, listing),
    }


def normalize_score(dimensions):
    """0–100 over the criteria the contact defined.

    A requested fact the listing does not verify stays in the denominator
    and adds no points. A criterion the contact did not set is ignored.
    """
    earned = 0.0
    total = 0.0
    for key, weight in SCORE_WEIGHTS.items():
        item = dimensions.get(key) or _skip(key)
        if item.get("state") == SKIP or item.get("requested") is False:
            continue
        total += weight
        if item.get("state") == UNKNOWN:
            continue
        earned += weight * float(item.get("ratio") or 0)
    if total <= 0:
        return 0
    return max(0, min(100, int(round((earned / total) * 100))))


def match_level(score):
    if score >= EXCELLENT_SCORE:
        return "excellent"
    if score >= GOOD_SCORE:
        return "good"
    if score >= HIDDEN_SCORE:
        return "ok"
    return "low"


def passes_hard_filters(criteria, listing, property_row=None):
    row = property_row or {}
    source = listing.get("source") or row.get("source") or SOURCE_INTERNAL
    is_external = source != SOURCE_INTERNAL or row.get("external_listing_id")
    if is_external:
        if listing.get("commercial_status") in ("sold", "rented", "withdrawn"):
            return False
        if row.get("is_active") is False:
            return False
    elif "status" in row:
        if not is_commercially_available(row):
            return False
    elif listing.get("commercial_status") in ("sold", "rented", "withdrawn"):
        return False

    wanted_purpose = normalize_listing_purpose((criteria or {}).get("purpose"))
    have_purpose = listing.get("purpose")
    if wanted_purpose and have_purpose and wanted_purpose != have_purpose:
        return False

    wanted_types = wanted_property_types(criteria)
    have_type = listing.get("property_type")
    if wanted_types and have_type and have_type not in wanted_types:
        return False

    budget = (criteria or {}).get("budget") or {}
    if currencies_comparable(budget.get("currency"), listing.get("currency")):
        maximum = budget.get("max")
        price = listing.get("price")
        if (
            maximum not in (None, 0)
            and price is not None
            and budget_over_ratio(price, maximum) is None
            and price > maximum
        ):
            return False

    for key in ("rooms", "bedrooms"):
        minimum = (criteria or {}).get(f"{key}_min")
        have = listing.get(key)
        if minimum is not None and have is not None and int(have) < int(minimum):
            return False

    required, _preferred = feature_groups(criteria)
    for key in required:
        if _feature_presence(key, listing) == CONFLICT:
            return False

    from modules.maps.geo import (
        distance_between_coordinates,
        need_geo_from_preferences,
    )

    geo = need_geo_from_preferences(criteria)
    if geo:
        meters = listing.get("distance_meters")
        if meters is None:
            meters = distance_between_coordinates(
                geo["center_latitude"],
                geo["center_longitude"],
                listing.get("latitude") or row.get("latitude"),
                listing.get("longitude") or row.get("longitude"),
            )
        if meters is None or meters > geo["radius_m"]:
            return False
        listing["distance_meters"] = meters

    return True


def _history_status(identity, interactions, visits):
    property_id = identity.get("internal_property_id")
    status = "new"
    discarded = False
    if property_id is not None:
        bucket = (interactions or {}).get(int(property_id)) or {}
        if bucket.get("status"):
            return bucket["status"]
    if status == "new" and not discarded:
        visit = _visit_entry(visits or {}, identity)
        if visit.get("discarded"):
            discarded = True
        elif visit.get("visited"):
            status = "visited"
    if discarded and status in ("new", "shared", "visited"):
        status = "discarded"
    return status


def match_properties(
    contact,
    listings,
    criteria_override=None,
    visits=None,
    interactions=None,
    include_discarded=False,
):
    """Rank a mixed list of internal and external listings.

    ``listings`` are property rows, external listing rows, or already
    normalized listing dicts. Scoring is identical regardless of source.
    ``visits`` maps property_id or ``("external", id)`` → visit flags.
    """
    criteria = resolve_criteria(contact, criteria_override)
    visit_map = visits or {}
    ranked = []

    for item in listings or []:
        listing, raw = _as_listing(item)
        identity = _listing_identity(listing, raw)
        if not passes_hard_filters(criteria, listing, raw):
            continue

        dimensions = score_dimensions(criteria, listing)
        score = normalize_score(dimensions)
        status = _history_status(identity, interactions, visit_map)
        if status == "discarded" and not include_discarded:
            continue
        ranked.append(
            {
                **identity,
                "score": score,
                "level": match_level(score),
                "hidden": score < HIDDEN_SCORE,
                "dimensions": dimensions,
                "reasons": explain_dimensions(dimensions),
                "listing": listing,
                "property": raw,
                "history": status,
                "visited": status in ("visited", "interested", "negotiation"),
                "discarded": status == "discarded",
                "visit_group": HISTORY_RANK.get(status, 4),
            }
        )

    ranked.sort(
        key=lambda row: (
            -row["score"],
            row["visit_group"],
            row.get("internal_property_id") or row.get("external_listing_id") or 0,
        )
    )
    return ranked


def visit_map_for_contact(organization_id, contact_id, *, agent_id=None):
    from modules.database.agent_tasks_repository import list_agent_tasks

    if not contact_id:
        return {}

    tasks = list_agent_tasks(
        organization_id,
        agent_id=agent_id,
        contact_id=contact_id,
        task_type="visit",
        limit=500,
    )
    mapping = {}
    for task in tasks:
        property_id = task.get("property_id")
        external_id = task.get("external_listing_id")
        keys = []
        if property_id is not None:
            keys.append(("internal", int(property_id)))
            keys.append(int(property_id))
        if external_id is not None:
            keys.append(("external", int(external_id)))
        if not keys:
            continue
        outcome = normalize_visit_outcome(task.get("outcome_json"))
        discarded = (outcome or {}).get("interest") == "negative"
        for key in keys:
            entry = mapping.setdefault(key, {"visited": True, "discarded": False})
            if discarded:
                entry["discarded"] = True
    return mapping


def query_filters_from_criteria(criteria):
    """SQL pre-filter payload. Price only when currency is known."""
    criteria = normalize_preferences(criteria)
    budget = criteria.get("budget") or {}
    currency = str(budget.get("currency") or "").strip().upper() or None
    filters = {
        "property_types": wanted_property_types(criteria),
        "listing_purpose": normalize_listing_purpose(criteria.get("purpose")),
        "listing_currency": None,
        "max_listing_price": None,
    }
    if currency in ("USD", "ARS") and budget.get("max") is not None:
        filters["listing_currency"] = currency
        filters["max_listing_price"] = float(budget["max"]) * (1 + BUDGET_HARD_OVER)
    from modules.maps.geo import need_geo_from_preferences

    geo = need_geo_from_preferences(criteria)
    if geo:
        filters["center_lat"] = geo["center_latitude"]
        filters["center_lng"] = geo["center_longitude"]
        filters["radius_m"] = geo["radius_m"]
    return filters


def interaction_map_for_contact(organization_id, contact_id):
    """Latest meaningful status per property, plus any discard flag."""
    from modules.database.contacts_repository import list_property_interactions

    if not contact_id:
        return {}
    rows = list_property_interactions(
        organization_id,
        contact_id=contact_id,
        limit=500,
    )
    mapping = {}
    for row in rows or []:
        property_id = row.get("property_id")
        kind = str(row.get("interaction_type") or "").strip().lower()
        if property_id is None or kind not in HISTORY_RANK:
            continue
        property_id = int(property_id)
        if property_id in mapping:
            continue
        mapping[property_id] = {
            "status": kind,
            "discarded": kind == "discarded",
        }
    return mapping


def rank_contact_properties(
    organization_id,
    contact,
    *,
    agent_id=None,
    criteria_override=None,
    listings=None,
    include_discarded=False,
):
    criteria = resolve_criteria(contact, criteria_override)
    if listings is None:
        from modules.database.external_listings_repository import (
            list_active_external_listings,
        )
        from modules.listing_connectors.internal import InternalListingConnector
        from modules.listings_normalize import attach_listing_identity

        from modules.listing_sources import SOURCE_INTERNAL, match_visible_sources

        listings = list(
            InternalListingConnector().search(
                criteria,
                organization_id=organization_id,
                agent_id=agent_id,
            ).listings
        )
        for source in match_visible_sources():
            if source == SOURCE_INTERNAL:
                continue
            for row in list_active_external_listings(
                organization_id,
                source=source,
                limit=CANDIDATE_LIMIT,
            ):
                listing = attach_listing_identity(
                    listing_from_external_listing(row),
                    external_listing_id=row["id"],
                )
                listing["is_active"] = row.get("is_active")
                listings.append(listing)

    visits = visit_map_for_contact(
        organization_id,
        contact.get("id"),
        agent_id=agent_id,
    )
    interactions = interaction_map_for_contact(organization_id, contact.get("id"))
    return match_properties(
        contact,
        listings,
        criteria_override=criteria,
        visits=visits,
        interactions=interactions,
        include_discarded=include_discarded,
    )


def persist_search_preferences(organization_id, contact, incoming):
    from modules.database.contacts_repository import update_contact
    import json

    merged = merge_contact_preferences(
        contact.get("preferences_json") or contact.get("preferences"),
        incoming,
    )
    updated = update_contact(
        contact["id"],
        organization_id,
        preferences_json=json.dumps(merged, ensure_ascii=False) if merged else "",
    )
    try:
        from modules.notifications_service import (
            PREFS_SAVE_MATCH_NOTIFY_LIMIT,
            notify_new_property_matches_for_contact,
        )

        notify_new_property_matches_for_contact(
            organization_id,
            updated or contact,
            limit=PREFS_SAVE_MATCH_NOTIFY_LIMIT,
        )
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "property_match_notify_failed organization_id=%s contact_id=%s",
            organization_id,
            contact.get("id") if contact else None,
            exc_info=True,
        )
    return updated


def _feature_label(key, language):
    return translate(f"property_feature_{key}", language=language)


def _explain_criterion(item, language="es"):
    key = item["key"]
    state = item["state"]
    if key == "budget":
        money = format_listing_money(
            item.get("price"),
            item.get("currency"),
            language=language,
        )
        if item.get("currency_gap"):
            return translate("matches_explain_budget_currency", language)
        if state == MATCH and item.get("over_pct"):
            return translate(
                "matches_explain_budget_over_soft",
                language,
                amount=money,
                percent=_format_percent(item.get("over_pct")),
            )
        if state == MATCH and item.get("partial"):
            return translate(
                "matches_explain_budget_below",
                language,
                amount=money,
            )
        if state == MATCH:
            return translate(
                "matches_explain_budget_ok",
                language,
                amount=money,
            )
        if state == CONFLICT:
            return translate(
                "matches_explain_budget_over",
                language,
                amount=money,
            )
        return translate("matches_explain_budget_unknown", language)
    if key == "zone":
        hood = item.get("neighborhood")
        if item.get("distance_label"):
            if state == MATCH:
                return translate(
                    "matches_explain_distance_ok",
                    language,
                    distance=item["distance_label"],
                )
            return translate(
                "matches_explain_distance_miss",
                language,
                distance=item["distance_label"],
            )
        if state == MATCH:
            return hood
        if state == CONFLICT:
            return translate(
                "matches_explain_zone_miss",
                language,
                area=hood,
            )
        return translate("matches_explain_zone_unknown", language)
    if key == "type":
        if state == UNKNOWN:
            return translate("matches_explain_type_unknown", language)
        label = translate(
            f"property_type_{item.get('property_type')}",
            language,
        ) if item.get("property_type") else ""
        if state == MATCH:
            return label
        return translate("matches_explain_type_miss", language, value=label)
    if key in ("rooms", "bedrooms"):
        actual = item.get("actual")
        wanted = item.get("wanted")
        label_key = (
            "property_rooms_n" if key == "rooms" else "property_bedrooms_n"
        )
        if state == MATCH:
            return translate(label_key, language, n=actual)
        if state == CONFLICT:
            return translate(
                "matches_explain_count_miss",
                language,
                wanted=wanted,
                actual=actual,
            )
        return translate(
            "matches_explain_count_unknown",
            language,
            field=translate(
                "contacts_field_rooms" if key == "rooms" else "contacts_field_bedrooms",
                language,
            ),
        )
    return ""


def _format_percent(value):
    number = float(value or 0)
    text = f"{number:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def explain_dimensions(dimensions):
    """Structured reasons shared by JRH and the contact card."""
    matched = []
    warnings = []
    missing = []
    conflicts = []
    for key in SCORE_WEIGHTS:
        item = (dimensions or {}).get(key) or {}
        if not item.get("requested") or item.get("state") == SKIP:
            continue
        if key == "features":
            for feature in item.get("items") or []:
                bucket = {
                    MATCH: matched,
                    UNKNOWN: missing,
                    CONFLICT: conflicts,
                }.get(feature.get("state"), conflicts)
                bucket.append({"key": feature["key"], "feature": True})
            continue
        entry = {"key": key, **{name: item.get(name) for name in (
            "neighborhood",
            "property_type",
            "actual",
            "wanted",
            "price",
            "currency",
            "over_pct",
            "partial",
            "currency_gap",
            "distance_label",
        ) if item.get(name) not in (None, "")}}
        if item.get("state") == UNKNOWN or item.get("currency_gap"):
            missing.append(entry)
        elif item.get("over_pct") or (key == "budget" and item.get("partial")):
            warnings.append(entry)
        elif item.get("state") == CONFLICT or not item.get("ratio"):
            conflicts.append(entry)
        else:
            matched.append(entry)
    return {
        "matched": matched,
        "warnings": warnings,
        "missing": missing,
        "conflicts": conflicts,
    }


def decorate_match(result, *, language="es"):
    listing = dict(result.get("listing") or {})
    raw = dict(result.get("property") or {})
    display = decorate_property_for_display({**raw, **{
        "listing_price": listing.get("price"),
        "listing_currency": listing.get("currency"),
        "listing_purpose": listing.get("purpose"),
        "features": listing.get("features"),
        "neighborhood": listing.get("neighborhood"),
        "rooms": listing.get("rooms"),
        "bedrooms": listing.get("bedrooms"),
        "bathrooms": listing.get("bathrooms"),
        "covered_m2": listing.get("covered_m2"),
        "total_m2": listing.get("total_m2"),
        "parking_spaces": listing.get("parking_spaces"),
        "address": listing.get("address") or raw.get("address"),
        "description": listing.get("description"),
        "commercial_status": listing.get("commercial_status")
        or raw.get("commercial_status"),
        "property_type": listing.get("property_type"),
    }}, language)

    hits = []
    dimensions = result.get("dimensions") or {}
    for key in SCORE_WEIGHTS:
        item = dimensions.get(key)
        if not item:
            continue
        if key == "features":
            for feature in item.get("items") or []:
                label = _feature_label(feature["key"], language)
                if feature["state"] == MATCH:
                    hits.append(
                        {"key": feature["key"], "state": MATCH, "label": label}
                    )
                elif feature["state"] == UNKNOWN:
                    hits.append(
                        {
                            "key": feature["key"],
                            "state": UNKNOWN,
                            "label": translate(
                                "matches_feature_unknown",
                                language,
                                name=label,
                            ),
                        }
                    )
                else:
                    hits.append(
                        {
                            "key": feature["key"],
                            "state": CONFLICT,
                            "label": translate(
                                "matches_feature_miss",
                                language,
                                name=label,
                            ),
                        }
                    )
            continue
        if item["state"] == SKIP:
            continue
        hits.append(
            {
                "key": key,
                "state": item["state"],
                "label": _explain_criterion(item, language),
            }
        )

    share_lines = [
        display.get("address") or listing.get("address"),
        listing.get("neighborhood"),
        display.get("price_display"),
    ]
    facts = []
    if listing.get("rooms") is not None:
        facts.append(
            translate("property_rooms_n", language=language, n=listing["rooms"])
        )
    if listing.get("bedrooms") is not None:
        facts.append(
            translate(
                "property_bedrooms_n",
                language=language,
                n=listing["bedrooms"],
            )
        )
    if display.get("area_label"):
        facts.append(display["area_label"])
    active_features = [
        _feature_label(item["key"], language)
        for item in (dimensions.get("features") or {}).get("items") or []
        if item["state"] == MATCH
    ]
    facts.extend(active_features)
    if facts:
        share_lines.append(" · ".join(facts))

    source = result.get("source") or listing.get("source") or SOURCE_INTERNAL
    reasons = _label_reasons(result.get("reasons") or explain_dimensions(dimensions), language)
    history = result.get("history") or "new"
    return {
        **result,
        "source": source,
        "source_label": translate(f"listing_source_{source}", language),
        "display": display,
        "hits": hits,
        "reasons": reasons,
        "history": history,
        "history_label": translate(HISTORY_LABELS.get(history, "matches_history_new"), language),
        "share_lines": [line for line in share_lines if line],
        "level_label": translate(f"matches_level_{result['level']}", language),
        "external_url": result.get("external_url") or listing.get("external_url"),
    }


def _label_reasons(reasons, language):
    labeled = {}
    for bucket in ("matched", "warnings", "missing", "conflicts"):
        labeled[bucket] = []
        for item in reasons.get(bucket) or []:
            if item.get("feature"):
                name = _feature_label(item["key"], language)
                if bucket == "missing":
                    text = translate("matches_feature_unknown", language, name=name)
                elif bucket == "conflicts":
                    text = translate("matches_feature_miss", language, name=name)
                else:
                    text = name
            else:
                text = _explain_criterion({**item, "state": _reason_state(bucket, item)}, language)
            labeled[bucket].append({"key": item.get("key"), "label": text})
    return labeled


def _reason_state(bucket, item):
    if bucket == "missing":
        return UNKNOWN
    if bucket == "conflicts":
        return CONFLICT
    return MATCH


def search_chip_labels(criteria, *, language="es"):
    criteria = normalize_preferences(criteria)
    chips = []
    areas = criteria.get("areas") or []
    if areas:
        chips.append(" · ".join(areas))
    if criteria.get("rooms") is not None:
        chips.append(
            translate("contacts_rooms_label", language, count=criteria["rooms"])
        )
    if criteria.get("bedrooms") is not None:
        chips.append(
            translate(
                "property_bedrooms_n",
                language=language,
                n=criteria["bedrooms"],
            )
        )
    budget = criteria.get("budget") or {}
    if budget.get("max"):
        chips.append(
            translate(
                "contacts_summary_budget_max",
                language,
                amount=format_budget_label(budget),
            )
        )
    for key in normalize_wanted_features(criteria.get("features")):
        chips.append(_feature_label(key, language))
    purpose = normalize_listing_purpose(criteria.get("purpose"))
    if purpose:
        chips.append(translate(f"listing_purpose_{purpose}", language))
    from modules.maps.geo import format_distance, need_geo_from_preferences

    geo = need_geo_from_preferences(criteria)
    if geo:
        label = geo.get("location_reference_text") or ""
        distance = format_distance(geo["radius_m"], language)
        if label:
            chips.append(
                translate(
                    "matches_chip_near",
                    language,
                    distance=distance,
                    place=label,
                )
            )
        else:
            chips.append(
                translate("matches_chip_radius", language, distance=distance)
            )
    return chips


def build_whatsapp_message(contact, matches, *, language="es"):
    name = _first_name((contact or {}).get("name"))
    cards = [item if "share_lines" in item else decorate_match(item, language=language) for item in (matches or [])]
    cards = [card for card in cards if card.get("share_lines")]
    if not cards:
        return ""

    if len(cards) == 1:
        lines = [
            translate("matches_wa_hello", language, name=name),
            "",
            *cards[0]["share_lines"],
        ]
        url = cards[0].get("external_url") or cards[0].get("internal_url")
        if url:
            lines.extend(["", url])
        return "\n".join(lines).strip()

    lines = [
        translate("matches_wa_hello_many", language, name=name, count=len(cards)),
        "",
    ]
    for card in cards:
        compact = " · ".join(card["share_lines"])
        source_label = card.get("source_label")
        url = card.get("external_url") or card.get("internal_url")
        extra = " · ".join(part for part in (source_label, url) if part)
        lines.append(f"• {compact}" + (f"\n  {extra}" if extra else ""))
    return "\n".join(lines).strip()


def whatsapp_share_url(phone, message):
    digits = whatsapp_digits(phone)
    if not digits or not message:
        return None
    return f"https://wa.me/{digits}?text={quote(message)}"
