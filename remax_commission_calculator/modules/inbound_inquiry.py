"""Turn a public form into one CRM contact and one inquiry_received row."""

from __future__ import annotations

import json
import re
import threading
import time

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from modules.contact_normalize import normalize_email, normalize_phone, phones_match
from modules.contacts import ContactError, create_agent_contact, touch_contact_interaction
from modules.database.agents_repository import get_agent_record
from modules.database.contacts_repository import (
    get_contact,
    list_contact_identities,
    list_received_inquiries,
    record_property_interaction,
)
from modules.database.properties_repository import get_property_record
from modules.database.public_share_repository import (
    get_property_link_by_token,
    get_shortlist_by_token,
)
from modules.database.users_repository import get_user_by_agent_id
from modules.i18n import translate
from modules.organization_time import organization_timezone, to_local
from modules.public_share import resolve_property, resolve_shortlist


SOURCE_PROPERTY = "public_property"
SOURCE_SHORTLIST = "public_shortlist"
INQUIRY_TYPE = "inquiry_received"
MESSAGE_LIMIT = 500
NAME_LIMIT = 80
FORM_SALT = "jrh-public-inquiry"
FORM_MAX_AGE = 2 * 60 * 60
RATE_LIMIT = 5
RATE_WINDOW_SECONDS = 60 * 60

ATTENTION_EMAIL_OTHER = "email_other_contact"
ATTENTION_EMAIL_PHONE = "email_phone_conflict"
ATTENTION_AMBIGUOUS_PHONE = "ambiguous_phone"
ATTENTION_OTHER_AGENT = "other_agent_listing"

_RATE_LOCK = threading.Lock()
_RATE_HITS = {}


class InboundClosed(Exception):
    """The public token cannot accept an inquiry."""

    def __init__(self, status):
        self.status = status
        super().__init__(status)


def reset_inquiry_rate_limits():
    with _RATE_LOCK:
        _RATE_HITS.clear()


def allow_inquiry_rate(token, *, now=None):
    """At most five posts per public token inside one hour."""
    moment = time.time() if now is None else float(now)
    key = (token or "").strip()
    if not key:
        return False
    with _RATE_LOCK:
        recent = [
            stamp
            for stamp in _RATE_HITS.get(key, [])
            if moment - stamp < RATE_WINDOW_SECONDS
        ]
        if len(recent) >= RATE_LIMIT:
            _RATE_HITS[key] = recent
            return False
        recent.append(moment)
        _RATE_HITS[key] = recent
        return True


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=FORM_SALT)


def issue_form_token(kind, public_token):
    return _serializer().dumps({"kind": kind, "token": public_token})


def form_token_ok(raw, kind, public_token):
    if not (raw or "").strip():
        return False
    try:
        payload = _serializer().loads(raw.strip(), max_age=FORM_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False
    return payload.get("kind") == kind and payload.get("token") == public_token


def clean_public_text(value, limit):
    text = re.sub(r"<[^>]*>", "", value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = " ".join(text.split())
    return text[:limit]


def validate_public_inquiry(name, phone, email, message):
    """Field errors only. Never mentions whether a person already exists."""
    errors = []
    clean_name = clean_public_text(name, NAME_LIMIT)
    clean_phone = clean_public_text(phone, 40)
    clean_email = clean_public_text(email, 120)
    clean_message = clean_public_text(message, MESSAGE_LIMIT)
    if len(clean_name) < 2:
        errors.append("public_inquiry_err_name")
    if not normalize_phone(clean_phone, default_country="AR"):
        errors.append("public_inquiry_err_phone")
    if clean_email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", clean_email):
        errors.append("public_inquiry_err_email")
    if len(clean_message) < 2:
        errors.append("public_inquiry_err_message")
    return {
        "name": clean_name,
        "phone": clean_phone,
        "email": clean_email,
        "message": clean_message,
        "errors": errors,
    }


def _phone_hits(raw_phone, identities):
    incoming = normalize_phone(raw_phone, default_country="AR") or ""
    hits = []
    for contact in identities:
        stored_raw = contact.get("phone") or ""
        stored_key = contact.get("phone_normalized") or ""
        if phones_match(raw_phone, stored_raw):
            hits.append(contact)
            continue
        if incoming and stored_key and (
            incoming == stored_key or incoming[-8:] == stored_key[-8:]
        ):
            hits.append(contact)
    return hits


def _choose_contact(identities, *, phone, email, listing_agent_id):
    """Org-wide match. Phone wins. A contradiction never merges two fichas."""
    email_key = normalize_email(email) if email else None
    phone_hits = _phone_hits(phone, identities) if phone else []
    email_hits = [
        contact
        for contact in identities
        if email_key and contact.get("email_normalized") == email_key
    ]
    attention = ""
    related_id = None
    save_email = bool(email_key)

    if len(phone_hits) == 1:
        chosen = phone_hits[0]
        others = [item for item in email_hits if item["id"] != chosen["id"]]
        if others:
            attention = ATTENTION_EMAIL_OTHER
            related_id = others[0]["id"]
            save_email = False
        return chosen, attention, related_id, save_email

    if len(phone_hits) > 1:
        owned = [
            item
            for item in phone_hits
            if listing_agent_id and item.get("agent_id") == listing_agent_id
        ]
        pool = owned or phone_hits
        chosen = sorted(pool, key=lambda item: item["id"])[0]
        return chosen, ATTENTION_AMBIGUOUS_PHONE, None, False

    if len(email_hits) == 1:
        chosen = email_hits[0]
        stored_phone = chosen.get("phone") or ""
        if not stored_phone or phones_match(phone, stored_phone):
            return chosen, "", None, False
        return None, ATTENTION_EMAIL_PHONE, chosen["id"], False

    if len(email_hits) > 1:
        return None, ATTENTION_EMAIL_PHONE, email_hits[0]["id"], False

    return None, "", None, save_email


def _notify(organization_id, owner_agent_id, contact, interaction_id, *, address, visitor_name, language):
    user = get_user_by_agent_id(owner_agent_id, organization_id)
    if user is None:
        return None
    first = (visitor_name or "").split(" ")[0]
    if address:
        title = translate("inbound_push_title", language, address=address)
    else:
        title = translate("inbound_push_title_selection", language)
    body = (
        translate("inbound_push_body", language, name=first)
        if first
        else translate("inbound_push_body_plain", language)
    )
    from modules.notifications.events import emit_event

    return emit_event(
        "crm.inbound_lead",
        {
            "organization_id": organization_id,
            "user_id": user["id"],
            "agent_id": owner_agent_id,
            "type": "inbound_lead_received",
            "title": title,
            "body": body,
            "url": f"/contacts/{int(contact['id'])}#inquiry-{int(interaction_id)}",
            "event_key": f"inbound_lead:{int(interaction_id)}",
            "entity_type": "contact",
            "entity_id": contact["id"],
            "priority": "important",
            "metadata": {
                "contact_id": contact["id"],
                "interaction_id": interaction_id,
            },
        },
    )


def _commit_inquiry(
    *,
    organization_id,
    listing_agent_id,
    property_id,
    shortlist_id,
    source,
    address,
    name,
    phone,
    email,
    message,
    language,
):
    if not listing_agent_id or get_agent_record(listing_agent_id, organization_id) is None:
        raise InboundClosed("missing")
    identities = list_contact_identities(organization_id)
    chosen, attention, related_id, save_email = _choose_contact(
        identities,
        phone=phone,
        email=email,
        listing_agent_id=listing_agent_id,
    )
    created = False
    if chosen is None:
        payload = {
            "name": name,
            "phone": phone,
            "source": source,
            "source_type": source,
            "status": "lead",
        }
        if save_email and email:
            payload["email"] = email
        try:
            contact = create_agent_contact(organization_id, listing_agent_id, payload)
        except ContactError as error:
            raise InboundClosed("missing") from error
        phone_key = normalize_phone(phone, default_country="AR")
        if phone_key and contact.get("phone_normalized") != phone_key:
            from modules.database.contacts_repository import update_contact

            contact = update_contact(
                contact["id"],
                organization_id,
                phone_normalized=phone_key,
            )
        created = True
    else:
        contact = get_contact(chosen["id"], organization_id)

    if (
        listing_agent_id
        and contact.get("agent_id") != listing_agent_id
        and not attention
    ):
        attention = ATTENTION_OTHER_AGENT

    touch_contact_interaction(organization_id, contact["id"])
    interaction_id = record_property_interaction(
        organization_id,
        contact["agent_id"],
        contact_id=contact["id"],
        property_id=property_id,
        interaction_type=INQUIRY_TYPE,
        shortlist_id=shortlist_id,
        source=source,
        message=message,
        listing_agent_id=listing_agent_id,
        attention=attention or None,
        related_contact_id=related_id,
        visitor_email=email or None,
    )
    _notify(
        organization_id,
        contact["agent_id"],
        contact,
        interaction_id,
        address=address,
        visitor_name=name,
        language=language,
    )
    return {
        "contact_id": contact["id"],
        "interaction_id": interaction_id,
        "created": created,
        "agent_id": contact["agent_id"],
        "attention": attention,
    }


def submit_property_inquiry(token, *, name, phone, email, message, language="es"):
    status, payload = resolve_property(token, language=language)
    if status == "revoked":
        raise InboundClosed("revoked")
    if status != "ok" or not (payload.get("listing") or {}).get("available"):
        raise InboundClosed("missing" if status != "ok" else "unavailable")
    link = get_property_link_by_token(token)
    record = get_property_record(link["property_id"], link["organization_id"])
    return _commit_inquiry(
        organization_id=link["organization_id"],
        listing_agent_id=record.get("agent_id") if record else None,
        property_id=link["property_id"],
        shortlist_id=None,
        source=SOURCE_PROPERTY,
        address=(payload.get("listing") or {}).get("address") or "",
        name=name,
        phone=phone,
        email=email,
        message=message,
        language=language,
    )


def submit_shortlist_inquiry(token, *, name, phone, email, message, language="es"):
    status, payload = resolve_shortlist(token, language=language)
    if status in ("revoked", "expired"):
        raise InboundClosed(status)
    if status != "ok":
        raise InboundClosed("missing")
    cards = payload.get("cards") or []
    if not any(card.get("available") for card in cards):
        raise InboundClosed("unavailable")
    row = get_shortlist_by_token(token)
    return _commit_inquiry(
        organization_id=row["organization_id"],
        listing_agent_id=row.get("created_by_agent_id"),
        property_id=None,
        shortlist_id=row["id"],
        source=SOURCE_SHORTLIST,
        address="",
        name=name,
        phone=phone,
        email=email,
        message=message,
        language=language,
    )


def _shortlist_addresses(organization_id, property_ids_json):
    try:
        raw_ids = json.loads(property_ids_json or "[]")
    except (TypeError, ValueError):
        raw_ids = []
    addresses = []
    for raw in raw_ids:
        record = get_property_record(raw, organization_id)
        if record and record.get("address"):
            addresses.append(record["address"])
    return addresses


def inquiries_for_agent(organization_id, agent_id, *, mode="new", address="", now=None):
    from modules.jrh_ai_classify import fold_text
    from modules.organization_time import now_utc

    rows = list_received_inquiries(organization_id, agent_id=agent_id)
    if mode == "address" and address:
        needle = fold_text(address)
        matched = []
        seen = set()
        for row in rows:
            candidates = [row.get("address") or ""]
            candidates.extend(
                _shortlist_addresses(organization_id, row.get("property_ids_json"))
            )
            if not any(needle and needle in fold_text(item) for item in candidates):
                continue
            if row["contact_id"] in seen:
                continue
            seen.add(row["contact_id"])
            matched.append(row)
        return matched

    if mode == "today":
        moment = now or now_utc()
        local_day = to_local(moment, organization_timezone(organization_id))
        day = local_day.date().isoformat() if local_day else ""
        kept = []
        for row in rows:
            created = to_local(row.get("created_at"), organization_timezone(organization_id))
            if created is not None and created.date().isoformat() == day:
                kept.append(row)
        return kept

    return [row for row in rows if (row.get("commercial_stage") or "") == "new"]
