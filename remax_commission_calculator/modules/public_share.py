"""Public property and shortlist pages.

Tokens are unguessable and scoped to one organization. The public
payload is a whitelist: internal ids, notes, commissions, and the
contact behind a shortlist never leave this module.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from modules.agent_branding import format_whatsapp_display, get_agent_branding, whatsapp_url
from modules.config import get_private_upload_root
from modules.database.properties_repository import get_property_record
from modules.database.public_share_repository import (
    KIND_PROPERTY,
    KIND_SHORTLIST,
    STATUS_ACTIVE,
    STATUS_REVOKED,
    count_opens,
    get_active_property_link,
    get_property_link_by_token,
    get_shortlist_by_token,
    insert_property_link,
    insert_shortlist,
    record_open,
    revoke_property_links,
    revoke_shortlist,
    update_shortlist_properties,
)
from modules.i18n import translate
from modules.marketing_branding import build_legal_footer_line
from modules.property_inventory import (
    format_listing_money,
    is_commercially_available,
    property_feature_labels,
)
from modules.property_types import normalize_listing_purpose, normalize_property_type


TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,80}$")
DEFAULT_SHORTLIST_DAYS = 30
SHORTLIST_DAY_CHOICES = (7, 30, 90)
class PublicShareError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def new_token():
    return secrets.token_urlsafe(32)


def valid_token(value):
    return bool(TOKEN_RE.fullmatch(str(value or "").strip()))


def absolute_url(base_url, kind, token):
    root = str(base_url or "").strip().rstrip("/")
    return f"{root}/{kind}/{token}"


def _utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse_expiry(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _client_price(amount, currency, language):
    label = format_listing_money(amount, currency, language=language) or ""
    if label.endswith(",00") or label.endswith(".00"):
        return label[:-3]
    return label


def _under_upload(path):
    if not path:
        return None
    root = get_private_upload_root().resolve()
    candidate = Path(path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def can_publish(record):
    return bool(record) and (record.get("status") or "") == "approved"


def ensure_property_link(organization_id, property_id, *, agent_id=None):
    record = get_property_record(property_id, organization_id)
    if record is None:
        raise PublicShareError("not_found")
    if agent_id is not None and record.get("agent_id") != agent_id:
        raise PublicShareError("forbidden")
    if not can_publish(record):
        raise PublicShareError("unpublished")
    existing = get_active_property_link(organization_id, property_id)
    if existing:
        return existing
    return insert_property_link(
        organization_id,
        property_id,
        new_token(),
        agent_id=agent_id,
    )


def revoke_property_link(organization_id, property_id):
    revoke_property_links(organization_id, property_id)


def ensure_public_shortlist(
    organization_id,
    property_ids,
    *,
    contact_id=None,
    agent_id=None,
    days=DEFAULT_SHORTLIST_DAYS,
    token=None,
):
    try:
        span = int(days)
    except (TypeError, ValueError):
        span = DEFAULT_SHORTLIST_DAYS
    if span not in SHORTLIST_DAY_CHOICES:
        span = DEFAULT_SHORTLIST_DAYS
    ids = []
    for raw in property_ids or []:
        try:
            property_id = int(raw)
        except (TypeError, ValueError):
            continue
        if property_id not in ids:
            ids.append(property_id)
    if not ids:
        raise PublicShareError("empty")
    expires = (_utcnow() + timedelta(days=span)).isoformat()
    if token and valid_token(token):
        current = get_shortlist_by_token(token)
        if (
            current
            and current.get("status") == STATUS_ACTIVE
            and int(current.get("organization_id") or 0) == int(organization_id)
            and current.get("contact_id") == contact_id
        ):
            return update_shortlist_properties(
                organization_id,
                token,
                ids,
                expires_at=expires,
            )
    return insert_shortlist(
        organization_id,
        new_token(),
        ids,
        expires,
        contact_id=contact_id,
        agent_id=agent_id,
    )


def share_target(record, base_url, *, agent_id=None):
    """Portal URL when it is already public. Otherwise a JRH /p/<token>."""
    from modules.property_shortlist import public_share_url

    portal = public_share_url((record or {}).get("external_url"))
    if portal:
        return portal, "portal"
    if not can_publish(record):
        return "", ""
    link = ensure_property_link(
        record["organization_id"],
        record["id"],
        agent_id=agent_id,
    )
    return absolute_url(base_url, "p", link["token"]), "jrh"


def prepare_client_links(
    organization_id,
    items,
    *,
    agent_id,
    base_url,
    mode="individual",
    contact_id=None,
    days=DEFAULT_SHORTLIST_DAYS,
    collection_token=None,
):
    """Fill each item's public_url. Collection mode also returns /s/<token>."""
    for item in items or []:
        record = get_property_record(item.get("property_id"), organization_id)
        url, kind = share_target(record, base_url, agent_id=agent_id)
        item["public_url"] = url
        item["share_kind"] = kind
    collection_url = ""
    token = collection_token or ""
    if mode == "collection":
        row = ensure_public_shortlist(
            organization_id,
            [item.get("property_id") for item in items or []],
            contact_id=contact_id,
            agent_id=agent_id,
            days=days,
            token=token or None,
        )
        token = row["token"]
        collection_url = absolute_url(base_url, "s", token)
    return collection_url, token


def _location_line(record):
    parts = []
    for key in ("neighborhood", "locality", "jurisdiction"):
        value = str((record or {}).get(key) or "").strip()
        if value and value.casefold() not in {part.casefold() for part in parts}:
            parts.append(value)
    return " · ".join(parts)


def _facts(record, language):
    parts = []
    if record.get("rooms") is not None:
        parts.append(translate("property_rooms_n", language, n=record["rooms"]))
    if record.get("bedrooms") is not None:
        parts.append(translate("property_bedrooms_n", language, n=record["bedrooms"]))
    if record.get("bathrooms") is not None:
        parts.append(translate("property_bathrooms_n", language, n=record["bathrooms"]))
    surface = record.get("total_m2")
    if surface in (None, ""):
        surface = record.get("covered_m2")
    if surface not in (None, ""):
        try:
            amount = float(surface)
            if amount == int(amount):
                surface = int(amount)
        except (TypeError, ValueError):
            pass
        parts.append(f"{surface} m²")
    spaces = record.get("parking_spaces")
    if spaces is not None and int(spaces) >= 1:
        parts.append(translate("property_feature_parking", language))
    return parts


def public_gallery(organization_id, property_id):
    from modules.property_sync.media import is_displayable_media

    from modules.database.property_media_repository import list_property_media

    photos = []
    seen = set()
    for item in list_property_media(organization_id, property_id):
        media_id = item.get("id")
        if media_id in seen or not is_displayable_media(item):
            continue
        seen.add(media_id)
        remote = ""
        if item.get("storage_strategy") == "remote_reference" or (
            not item.get("storage_key") and item.get("original_url")
        ):
            remote = (item.get("original_url") or item.get("remote_url") or "").strip()
        photos.append(
            {
                "index": len(photos),
                "remote_url": remote,
                "local": not bool(remote),
                "content_type": item.get("content_type") or "image/jpeg",
                "_media": item,
            }
        )
    return photos


def photo_file(organization_id, property_id, index):
    from modules.property_sync.media import resolve_media_filesystem_path

    photos = public_gallery(organization_id, property_id)
    try:
        slot = photos[int(index)]
    except (TypeError, ValueError, IndexError):
        return None, None
    if slot.get("remote_url"):
        return None, None
    path = _under_upload(resolve_media_filesystem_path(slot["_media"]))
    if path is None:
        return None, None
    return path, slot.get("content_type") or "image/jpeg"


def _office(organization_id, language):
    from modules.organization_marketing_logo import get_organization_marketing_branding

    branding = get_organization_marketing_branding(organization_id, language=language) or {}
    logo_url = str(branding.get("logo_url") or "")
    if not logo_url.lower().startswith("https://"):
        logo_url = ""
    logo_file = _under_upload(branding.get("logo_path"))
    return {
        "name": branding.get("brand_name") or "",
        "logo_url": logo_url,
        "has_logo_file": bool(logo_file),
        "legal_footer": branding.get("legal_footer_line")
        or build_legal_footer_line({}, language=language),
    }


def office_logo_file(organization_id):
    from modules.organization_marketing_logo import get_organization_marketing_branding

    branding = get_organization_marketing_branding(organization_id) or {}
    return _under_upload(branding.get("logo_path"))


def _agent_public(record, language):
    branding = get_agent_branding(
        record.get("agent_id"),
        record.get("organization_id"),
        language=language,
        agent_login_only=True,
    )
    if not branding:
        return None
    number = branding.get("whatsapp") or ""
    if not number:
        from modules.agent_contact_channels import (
            resolve_agent_contact_channels,
            suggested_whatsapp_number,
        )

        channels = resolve_agent_contact_channels(
            record.get("agent_id"),
            record.get("organization_id"),
        )
        number = (
            channels.get("whatsapp_number")
            or channels.get("suggested_whatsapp")
            or branding.get("phone")
            or ""
        )
    href = whatsapp_url(number)
    return {
        "name": branding.get("name") or "",
        "first_name": branding.get("greeting_name") or branding.get("name") or "",
        "title": branding.get("title") or "",
        "whatsapp_href": href,
        "whatsapp_label": format_whatsapp_display(number),
        "has_photo": bool(branding.get("has_photo")),
    }


def agent_photo_file(record):
    from modules.agent_photo import resolve_agent_photo_path
    from modules.database.agents_repository import get_agent_record

    agent = get_agent_record(record.get("agent_id"), record.get("organization_id"))
    if agent is None:
        return None
    return _under_upload(resolve_agent_photo_path(agent))


def _listing_view(record, language, *, token):
    available = is_commercially_available(record)
    purpose_key = normalize_listing_purpose(record.get("listing_purpose"))
    type_key = normalize_property_type(record.get("property_type"))
    title = (record.get("title") or "").strip() or (record.get("address") or "")
    address = record.get("address") or ""
    price = ""
    if available:
        price = _client_price(
            record.get("listing_price"),
            record.get("listing_currency"),
            language,
        )
    description = " ".join(str(record.get("description") or "").split())
    features = []
    if available:
        features = property_feature_labels(record.get("features"), language=language)
    return {
        "token": token,
        "title": title,
        "address": address,
        "location": _location_line(record),
        "price": price,
        "purpose": (
            translate(f"listing_purpose_{purpose_key}", language)
            if available and purpose_key
            else ""
        ),
        "property_type": (
            translate(f"property_type_{type_key}", language)
            if available and type_key
            else ""
        ),
        "facts": _facts(record, language) if available else [],
        "features": features,
        "description": description if available else "",
        "available": available,
        "status_label": "" if available else translate("public_unavailable", language),
    }


def _og(listing, photos, base_url, token):
    bits = [part for part in (listing.get("price"), listing.get("purpose"), listing.get("address")) if part]
    image = ""
    if photos:
        if photos[0].get("remote_url"):
            image = photos[0]["remote_url"]
        else:
            image = absolute_url(base_url, "p", f"{token}/photo/0")
    description = listing.get("description") or " · ".join(bits)
    return {
        "title": listing.get("title") or listing.get("address") or "",
        "description": description[:180],
        "image": image,
        "url": absolute_url(base_url, "p", token),
    }


def _whatsapp_links(agent, listing, language):
    if not agent or not agent.get("whatsapp_href"):
        return "", ""
    address = listing.get("address") or listing.get("title") or ""
    consult = translate(
        "public_wa_consult",
        language,
        name=agent.get("first_name") or agent.get("name") or "",
        address=address,
    )
    visit = translate(
        "public_wa_visit",
        language,
        name=agent.get("first_name") or agent.get("name") or "",
        address=address,
    )
    base = agent["whatsapp_href"]
    return f"{base}?text={quote(consult)}", f"{base}?text={quote(visit)}"


def resolve_property(token, *, language="es", base_url=""):
    if not valid_token(token):
        return "missing", None
    link = get_property_link_by_token(token)
    if link is None:
        return "missing", None
    if link.get("status") != STATUS_ACTIVE:
        return "revoked", None
    record = get_property_record(link["property_id"], link["organization_id"])
    if not can_publish(record):
        return "missing", None
    photos = public_gallery(link["organization_id"], link["property_id"])
    for photo in photos:
        photo.pop("_media", None)
        if photo.get("local"):
            photo["src"] = absolute_url(base_url, "p", f"{token}/photo/{photo['index']}")
        else:
            photo["src"] = photo.get("remote_url") or ""
    listing = _listing_view(record, language, token=token)
    agent = _agent_public(record, language)
    consult, visit = _whatsapp_links(agent, listing, language)
    if not listing["available"]:
        visit = ""
    office = _office(link["organization_id"], language)
    if office.get("has_logo_file") and not office.get("logo_url"):
        office["logo_url"] = absolute_url(base_url, "p", f"{token}/logo")
    if agent and agent.get("has_photo"):
        agent["photo_url"] = absolute_url(base_url, "p", f"{token}/agent")
    payload = {
        "listing": listing,
        "photos": photos,
        "agent": agent,
        "office": office,
        "consult_url": consult,
        "visit_url": visit if listing["available"] else "",
        "og": _og(listing, photos, base_url, token),
        "_link_id": link["id"],
        "_organization_id": link["organization_id"],
    }
    return "ok", payload


def resolve_shortlist(token, *, language="es", base_url=""):
    if not valid_token(token):
        return "missing", None
    row = get_shortlist_by_token(token)
    if row is None:
        return "missing", None
    if row.get("status") == STATUS_REVOKED:
        return "revoked", None
    if row.get("status") != STATUS_ACTIVE:
        return "missing", None
    expiry = _parse_expiry(row.get("expires_at"))
    if expiry is not None and expiry <= _utcnow():
        return "expired", None
    cards = []
    for property_id in row.get("property_ids") or []:
        record = get_property_record(property_id, row["organization_id"])
        if not can_publish(record):
            cards.append(
                {
                    "available": False,
                    "title": "",
                    "address": "",
                    "price": "",
                    "facts": [],
                    "photo": "",
                    "url": "",
                    "consult_url": "",
                    "status_label": translate("public_unavailable", language),
                }
            )
            continue
        link = ensure_property_link(row["organization_id"], property_id)
        listing = _listing_view(record, language, token=link["token"])
        photos = public_gallery(row["organization_id"], property_id)
        photo = ""
        if photos:
            if photos[0].get("remote_url"):
                photo = photos[0]["remote_url"]
            elif photos[0].get("local"):
                photo = absolute_url(base_url, "p", f"{link['token']}/photo/0")
        agent = _agent_public(record, language)
        consult, _visit = _whatsapp_links(agent, listing, language)
        cards.append(
            {
                "available": listing["available"],
                "title": listing["title"],
                "address": listing["address"] if listing["available"] else listing["address"],
                "location": listing["location"],
                "price": listing["price"],
                "facts": listing["facts"],
                "photo": photo,
                "url": absolute_url(base_url, "p", link["token"]),
                "consult_url": consult if listing["available"] else "",
                "status_label": listing["status_label"],
            }
        )
    office = _office(row["organization_id"], language)
    if office.get("has_logo_file") and not office.get("logo_url"):
        office["logo_url"] = ""
    first_photo = next((card["photo"] for card in cards if card.get("photo")), "")
    payload = {
        "title": translate("public_shortlist_title", language),
        "cards": cards,
        "office": office,
        "og": {
            "title": translate("public_shortlist_title", language),
            "description": " · ".join(
                part
                for part in (
                    (cards[0].get("address") if cards else ""),
                    (cards[0].get("price") if cards else ""),
                )
                if part
            )[:180],
            "image": first_photo,
            "url": absolute_url(base_url, "s", token),
        },
        "_link_id": row["id"],
        "_organization_id": row["organization_id"],
    }
    return "ok", payload


def mark_opened(kind, payload):
    if not payload:
        return
    record_open(payload["_organization_id"], kind, payload["_link_id"])


def public_context(payload):
    """Drop the internal bookkeeping before the template sees the page."""
    clean = dict(payload or {})
    clean.pop("_link_id", None)
    clean.pop("_organization_id", None)
    office = dict(clean.get("office") or {})
    office.pop("has_logo_file", None)
    clean["office"] = office
    return clean


def opens_for(organization_id, kind, link_id):
    return count_opens(organization_id, kind, link_id)


def deactivate_shortlist(organization_id, token, *, contact_id=None):
    if not valid_token(token):
        return False
    row = get_shortlist_by_token(token)
    if row is None or int(row.get("organization_id") or 0) != int(organization_id):
        return False
    if contact_id is not None and row.get("contact_id") not in (None, contact_id):
        return False
    return revoke_shortlist(organization_id, token)


def collection_requested(prompt):
    from modules.jrh_ai_classify import fold_text

    folded = fold_text(prompt or "")
    words = ("armame", "generame", "preparame")
    if "link" in folded and any(word in folded for word in words):
        return True
    return "seleccion" in folded and any(word in folded for word in words)


__all__ = [
    "KIND_PROPERTY",
    "KIND_SHORTLIST",
    "DEFAULT_SHORTLIST_DAYS",
    "SHORTLIST_DAY_CHOICES",
]
