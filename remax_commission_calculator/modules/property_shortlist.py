"""Agent-built property shortlist and the WhatsApp draft.

A line uses the portal URL when the listing already has one. Otherwise
the public /p/<token> link is filled in by prepare_client_links.
The agent sends the text; nothing is delivered automatically.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from modules.database.contacts_repository import (
    list_property_interactions,
    record_property_interaction,
)
from modules.database.properties_repository import get_properties, get_property_record
from modules.i18n import translate
from modules.property_inventory import format_listing_money, is_commercially_available


SHORTLIST_LIMIT = 5
NOTE_KIND = "property_shortlist_shared"

_INTERNAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0"})


class ShortlistError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def public_share_url(value):
    """Portal URL already stored on the listing. Never an app path."""
    text = str(value or "").strip()
    if not text.lower().startswith(("http://", "https://")):
        return ""
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if not host or host in _INTERNAL_HOSTS:
        return ""
    path = (parsed.path or "").lower()
    if path.startswith("/properties") or path.startswith("/contacts"):
        return ""
    return text


def _latest_interaction(organization_id, contact_id, property_id):
    rows = list_property_interactions(
        organization_id,
        contact_id=contact_id,
        limit=500,
    )
    for row in rows or []:
        if row.get("property_id") == property_id:
            return str(row.get("interaction_type") or "").strip().lower()
    return ""


def _client_price(amount, currency):
    """Whole amounts read as USD 190.000 in the client message."""
    label = format_listing_money(amount, currency, language="es") or ""
    if label.endswith(",00"):
        return label[:-3]
    if label.endswith(".00"):
        return label[:-3]
    return label


def _item_from_record(record):
    spaces = record.get("parking_spaces")
    return {
        "property_id": record.get("id"),
        "address": record.get("address") or "",
        "neighborhood": record.get("neighborhood") or "",
        "price_label": _client_price(
            record.get("listing_price"),
            record.get("listing_currency"),
        ),
        "rooms": record.get("rooms"),
        "bedrooms": record.get("bedrooms"),
        "parking": spaces is not None and int(spaces) >= 1,
        "public_url": public_share_url(record.get("external_url")),
        "score": None,
    }


def select_properties(organization_id, contact_id, property_ids, *, agent_id=None):
    """Keep the agent's order. Reject anything they must not send."""
    ordered = []
    seen = set()
    for raw in property_ids or []:
        try:
            property_id = int(raw)
        except (TypeError, ValueError):
            continue
        if property_id in seen:
            continue
        seen.add(property_id)
        ordered.append(property_id)
    if not ordered:
        raise ShortlistError("empty")
    if len(ordered) > SHORTLIST_LIMIT:
        raise ShortlistError("too_many")

    items = []
    for property_id in ordered:
        record = get_property_record(property_id, organization_id)
        if record is None:
            raise ShortlistError("not_found")
        if agent_id is not None and record.get("agent_id") != agent_id:
            raise ShortlistError("forbidden")
        if not is_commercially_available(record):
            raise ShortlistError("unavailable")
        if _latest_interaction(organization_id, contact_id, property_id) == "discarded":
            raise ShortlistError("discarded")
        items.append(_item_from_record(record))
    return items


def attach_match_scores(organization_id, contact, items, *, agent_id=None):
    from modules.property_match import rank_contact_properties

    ranked = rank_contact_properties(
        organization_id,
        contact,
        agent_id=agent_id,
    )
    scores = {
        row.get("property_id"): row.get("score")
        for row in ranked or []
    }
    for item in items:
        item["score"] = scores.get(item["property_id"])
    return items


def _facts(item, language):
    parts = []
    if item.get("price_label"):
        parts.append(item["price_label"])
    if item.get("rooms") is not None:
        parts.append(translate("property_rooms_n", language, n=item["rooms"]))
    if item.get("bedrooms") is not None:
        parts.append(translate("property_bedrooms_n", language, n=item["bedrooms"]))
    if item.get("parking"):
        parts.append(translate("property_feature_parking", language))
    return parts


def _listing_lines(items, language, *, include_urls):
    lines = []
    for index, item in enumerate(items or [], start=1):
        title = item.get("address") or ""
        hood = item.get("neighborhood") or ""
        if hood and hood.casefold() not in title.casefold():
            title = f"{title}, {hood}" if title else hood
        lines.append(f"{index}. {title}".rstrip())
        facts = _facts(item, language)
        if facts:
            lines.append(" · ".join(facts))
        if include_urls and item.get("public_url"):
            lines.append(item["public_url"])
        lines.append("")
    return lines


def draft_whatsapp_message(contact, items, *, language="es", collection_url=""):
    """Client-facing text. Compatibility scores stay off this draft."""
    name = ((contact or {}).get("name") or "").split()
    first = name[0] if name else ""
    lines = [
        translate("shortlist_wa_hello", language, name=first),
        "",
        translate("shortlist_wa_intro", language),
        "",
    ]
    lines.extend(_listing_lines(items, language, include_urls=not collection_url))
    if collection_url:
        lines.append(translate("shortlist_wa_collection", language))
        lines.append(collection_url)
        lines.append("")
    lines.append(translate("shortlist_wa_close", language))
    return "\n".join(lines).strip()


def record_shortlist_share(
    organization_id,
    contact,
    items,
    *,
    agent_id,
    now,
    tz,
    language="es",
):
    """One shared row per property, and one timeline line for the batch."""
    from modules.contact_follow_up import record_timeline_note

    written = []
    for position, item in enumerate(items or [], start=1):
        property_id = item.get("property_id")
        latest = _latest_interaction(organization_id, contact["id"], property_id)
        if latest == "shared":
            continue
        record_property_interaction(
            organization_id,
            agent_id,
            contact_id=contact["id"],
            property_id=property_id,
            interaction_type="shared",
            label=f"{position}|{item.get('address') or ''}",
        )
        written.append(property_id)
    addresses = [item.get("address") or "" for item in items if item.get("address")]
    if len(addresses) == 1:
        detail = addresses[0]
    elif len(addresses) == 2:
        detail = f"{addresses[0]} y {addresses[1]}"
    else:
        detail = ", ".join(addresses[:-1]) + f" y {addresses[-1]}" if addresses else ""
    text = translate(
        "shortlist_timeline",
        language,
        count=len(items or []),
        names=detail,
    )
    updated = record_timeline_note(
        contact,
        text,
        kind=NOTE_KIND,
        now=now,
        tz=tz,
    )
    return updated or contact, written


def top_matches(organization_id, contact, *, agent_id=None, limit=3):
    from modules.property_match import rank_contact_properties

    ranked = rank_contact_properties(
        organization_id,
        contact,
        agent_id=agent_id,
    )
    ids = []
    for row in ranked or []:
        property_id = row.get("property_id")
        if not property_id or int(row.get("score") or 0) <= 0:
            continue
        ids.append(property_id)
        if len(ids) >= limit:
            break
    if not ids:
        return []
    return attach_match_scores(
        organization_id,
        contact,
        select_properties(organization_id, contact["id"], ids, agent_id=agent_id),
        agent_id=agent_id,
    )


def find_properties_by_name(organization_id, queries, *, agent_id=None):
    from modules.jrh_ai_classify import fold_text

    rows = get_properties(organization_id, agent_id=agent_id, status="approved")
    chosen = []
    ambiguous = []
    for query in queries or []:
        needle = fold_text(query)
        if not needle:
            continue
        hits = [
            row for row in rows
            if needle in fold_text(row.get("address") or "")
            and is_commercially_available(row)
        ]
        if len(hits) == 1:
            chosen.append(hits[0]["id"])
        elif len(hits) > 1:
            ambiguous.append(query)
        else:
            ambiguous.append(query)
    return chosen, ambiguous


_COUNT_WORDS = {
    "una": 1,
    "un": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
}
_NAME_STOP = frozenset({
    "mandale",
    "enviale",
    "preparame",
    "prepara",
    "armame",
    "arma",
    "estas",
    "estos",
    "propiedades",
    "propiedad",
    "para",
    "las",
    "los",
    "un",
    "una",
    "whatsapp",
    "con",
    "opciones",
    "mejores",
    "primeras",
    "primera",
    "tres",
    "dos",
    "cuatro",
    "cinco",
})


def parse_shortlist_request(prompt):
    from modules.jrh_ai_classify import fold_text

    folded = fold_text(prompt or "")
    folded = re.sub(r"[^a-z0-9\s']", " ", folded)
    folded = re.sub(r"\s+", " ", folded).strip()
    limit = 3
    match = re.search(r"\bprimeras?\s+(una|un|dos|tres|cuatro|cinco|\d)\b", folded)
    if match:
        token = match.group(1)
        limit = _COUNT_WORDS.get(token) or int(token)
    elif "mejores opciones" in folded or "estas propiedades" in folded:
        limit = 3
    body = re.sub(r"\ba\s+[a-z][a-z'\-]+(?:\s+[a-z][a-z'\-]+)?\s*$", "", folded).strip()
    queries = []
    if " y " in body and "primeras" not in folded and "mejores" not in folded:
        for part in re.split(r"\s+y\s+", body):
            tokens = [token for token in part.split() if token not in _NAME_STOP]
            if tokens:
                queries.append(" ".join(tokens[-3:]))
    return {"limit": max(1, min(SHORTLIST_LIMIT, limit)), "queries": queries}
