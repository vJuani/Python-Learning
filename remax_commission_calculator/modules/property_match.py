"""Deterministic contact ↔ listing matcher.

Scores listings from ``listing_from_property()`` /
``listing_from_external_listing()`` / ``normalize_listing()``.
The matcher does not care whether a listing is internal or indexed
from a portal. Unknown data is excluded from the denominator.
Conflicts lower the score. No LLM. No FX invention.
"""

from __future__ import annotations

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

SCORE_WEIGHTS = {
    "budget": 30,
    "zone": 25,
    "type": 15,
    "rooms": 10,
    "bedrooms": 10,
    "features": 10,
}

BELOW_MIN_RATIO = 0.6
HIDDEN_SCORE = 60
GOOD_SCORE = 75
EXCELLENT_SCORE = 90
CANDIDATE_LIMIT = 300

VISIT_GROUP_NEW = 0
VISIT_GROUP_SEEN = 1
VISIT_GROUP_DISCARDED = 2


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
    payload = {"key": key, "state": state, "ratio": ratio}
    payload.update(extra)
    return payload


def _zone_hits(areas, neighborhood):
    folded_hood = _fold(neighborhood)
    if not folded_hood:
        return False
    tokens = folded_hood.split()
    for area in areas or []:
        folded = _fold(area)
        if not folded:
            continue
        if folded == folded_hood or folded in tokens:
            return True
    return False


def _score_budget(criteria, listing):
    budget = (criteria or {}).get("budget") or {}
    price = listing.get("price")
    want = str((budget.get("currency") or "")).strip().upper() or None
    have = listing.get("currency")

    if budget.get("min") is None and budget.get("max") is None:
        return _criterion("budget", UNKNOWN, price=price, currency=have)

    if not currencies_comparable(want, have) or price is None:
        return _criterion("budget", UNKNOWN, price=price, currency=have)

    minimum = budget.get("min")
    maximum = budget.get("max")
    if maximum is not None and price > maximum:
        return _criterion(
            "budget",
            CONFLICT,
            0.0,
            price=price,
            currency=have,
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
    areas = (criteria or {}).get("areas") or []
    neighborhood = listing.get("neighborhood")
    if not areas or not neighborhood:
        return _criterion("zone", UNKNOWN, neighborhood=neighborhood)
    if _zone_hits(areas, neighborhood):
        return _criterion("zone", MATCH, 1.0, neighborhood=neighborhood)
    return _criterion("zone", CONFLICT, 0.0, neighborhood=neighborhood)


def _score_type(criteria, listing):
    wanted = wanted_property_types(criteria)
    have = listing.get("property_type")
    if not wanted or not have:
        return _criterion("type", UNKNOWN, property_type=have)
    if have in wanted:
        return _criterion("type", MATCH, 1.0, property_type=have)
    return _criterion("type", CONFLICT, 0.0, property_type=have)


def _score_count(key, wanted, have):
    if wanted is None or have is None:
        return _criterion(key, UNKNOWN, wanted=wanted, actual=have)
    if int(wanted) == int(have):
        return _criterion(key, MATCH, 1.0, wanted=wanted, actual=have)
    return _criterion(key, CONFLICT, 0.0, wanted=wanted, actual=have)


def _score_features(criteria, listing):
    wanted = normalize_wanted_features((criteria or {}).get("features"))
    have = listing.get("features") or {}
    items = []
    if not wanted:
        return _criterion("features", UNKNOWN, items=[])

    known_match = 0
    known_conflict = 0
    for key in wanted:
        if key not in have:
            items.append({"key": key, "state": UNKNOWN})
            continue
        if have.get(key):
            items.append({"key": key, "state": MATCH})
            known_match += 1
        else:
            items.append({"key": key, "state": CONFLICT})
            known_conflict += 1

    known = known_match + known_conflict
    if known == 0:
        return _criterion("features", UNKNOWN, items=items)
    ratio = known_match / known
    state = MATCH if known_conflict == 0 else CONFLICT
    if known_match and known_conflict:
        state = MATCH
    return _criterion("features", state, ratio, items=items)


def score_dimensions(criteria, listing):
    return {
        "budget": _score_budget(criteria, listing),
        "zone": _score_zone(criteria, listing),
        "type": _score_type(criteria, listing),
        "rooms": _score_count("rooms", (criteria or {}).get("rooms"), listing.get("rooms")),
        "bedrooms": _score_count(
            "bedrooms",
            (criteria or {}).get("bedrooms"),
            listing.get("bedrooms"),
        ),
        "features": _score_features(criteria, listing),
    }


def normalize_score(dimensions):
    earned = 0.0
    total = 0.0
    for key, weight in SCORE_WEIGHTS.items():
        item = dimensions.get(key) or _criterion(key, UNKNOWN)
        if item["state"] == UNKNOWN:
            continue
        total += weight
        earned += weight * float(item.get("ratio") or 0)
    if total <= 0:
        return 0
    return int(round((earned / total) * 100))


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
        if maximum is not None and price is not None and price > maximum:
            return False

    return True


def _visit_group(visited, discarded):
    if discarded:
        return VISIT_GROUP_DISCARDED
    if visited:
        return VISIT_GROUP_SEEN
    return VISIT_GROUP_NEW


def match_properties(contact, listings, criteria_override=None, visits=None):
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
        visit = _visit_entry(visit_map, identity)
        visited = bool(visit.get("visited"))
        discarded = bool(visit.get("discarded"))
        ranked.append(
            {
                **identity,
                "score": score,
                "level": match_level(score),
                "hidden": score < HIDDEN_SCORE,
                "dimensions": dimensions,
                "listing": listing,
                "property": raw,
                "visited": visited,
                "discarded": discarded,
                "visit_group": _visit_group(visited, discarded),
            }
        )

    ranked.sort(
        key=lambda row: (
            row["visit_group"],
            -row["score"],
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
        filters["max_listing_price"] = budget["max"]
    return filters


def rank_contact_properties(
    organization_id,
    contact,
    *,
    agent_id=None,
    criteria_override=None,
    listings=None,
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
    return match_properties(
        contact,
        listings,
        criteria_override=criteria,
        visits=visits,
    )


def persist_search_preferences(organization_id, contact, incoming):
    from modules.database.contacts_repository import update_contact
    import json

    merged = merge_contact_preferences(
        contact.get("preferences_json") or contact.get("preferences"),
        incoming,
    )
    return update_contact(
        contact["id"],
        organization_id,
        preferences_json=json.dumps(merged, ensure_ascii=False) if merged else "",
    )


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
        if item["state"] == UNKNOWN:
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
    return {
        **result,
        "source": source,
        "source_label": translate(f"listing_source_{source}", language),
        "display": display,
        "hits": hits,
        "share_lines": [line for line in share_lines if line],
        "level_label": translate(f"matches_level_{result['level']}", language),
        "external_url": result.get("external_url") or listing.get("external_url"),
    }


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
