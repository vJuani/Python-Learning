"""Contact import providers. Selection happens on the client; the server
never reads a device address book and never persists a preview."""

from __future__ import annotations

import secrets
from abc import ABC, abstractmethod

from modules.contact_normalize import normalize_email, normalize_phone
from modules.contacts import (
    ContactError,
    create_agent_contact,
    load_contact,
    match_contacts,
    split_display_name,
    update_agent_contact,
)
from modules.database.contacts_repository import (
    find_contacts_by_normalized,
    get_import_batch,
    record_import_batch,
)
from modules.database.tenant import require_organization_id


MAX_IMPORT_SELECTION = 50
IMPORT_ACTIONS = (
    "import",
    "use_existing",
    "update_existing",
    "create_separate",
    "skip",
)


class ContactImportProvider(ABC):
    provider_id = "base"
    source_type = "other"

    @abstractmethod
    def parse_selected(self, payload):
        """Turn user-selected payload into raw contact dicts. No persistence."""

    def capability(self):
        return {
            "id": self.provider_id,
            "source_type": self.source_type,
            "available": False,
            "requires_user_selection": True,
        }


class BrowserContactPickerProvider(ContactImportProvider):
    """Adapter for the Contact Picker API. Detection is client-side."""

    provider_id = "browser_picker"
    source_type = "phone_import"

    def parse_selected(self, payload):
        items = payload if isinstance(payload, list) else payload.get("contacts") or []
        parsed = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _first(item.get("name")) or item.get("display_name") or ""
            tel = _first(item.get("tel")) or item.get("phone") or ""
            email = _first(item.get("email")) or ""
            parsed.append(
                {
                    "name": str(name).strip(),
                    "phone": str(tel).strip(),
                    "email": str(email).strip(),
                }
            )
        return parsed

    def capability(self):
        return {
            "id": self.provider_id,
            "source_type": self.source_type,
            "available": None,
            "requires_user_selection": True,
            "detect_on_client": True,
        }


class VCardImportProvider(ContactImportProvider):
    provider_id = "vcard"
    source_type = "vcard"

    def parse_selected(self, payload):
        text = payload if isinstance(payload, str) else payload.get("text") or ""
        return parse_vcard_text(text)

    def capability(self):
        return {
            "id": self.provider_id,
            "source_type": self.source_type,
            "available": True,
            "requires_user_selection": True,
        }


class FutureNativeContactProvider(ContactImportProvider):
    provider_id = "native"
    source_type = "phone_import"

    def parse_selected(self, payload):
        raise ContactError("contacts_import_native_unavailable")

    def capability(self):
        return {
            "id": self.provider_id,
            "source_type": self.source_type,
            "available": False,
            "requires_user_selection": True,
        }


PROVIDERS = {
    BrowserContactPickerProvider.provider_id: BrowserContactPickerProvider(),
    VCardImportProvider.provider_id: VCardImportProvider(),
    FutureNativeContactProvider.provider_id: FutureNativeContactProvider(),
}


def get_provider(provider_id):
    return PROVIDERS.get(provider_id)


def list_provider_capabilities():
    return [provider.capability() for provider in PROVIDERS.values()]


def new_import_token():
    return secrets.token_hex(16)


def preview_import(
    organization_id,
    agent_id,
    selected,
    *,
    source_type="phone_import",
    language="es",
):
    """Classify selected contacts. Does not write to the database."""
    organization_id = require_organization_id(organization_id)
    if agent_id is None:
        raise ContactError("contacts_err_agent_required")
    rows = []
    for raw in selected or []:
        item = _normalize_incoming(raw)
        if not item["name"] and not item["phone"] and not item["email"]:
            continue
        if not item["name"]:
            item["name"] = item["phone"] or item["email"]
        classification = classify_import_row(
            organization_id,
            agent_id,
            item,
            language=language,
        )
        rows.append({**item, **classification, "selected": True})
    counts = {
        "selected": len(rows),
        "new": sum(1 for row in rows if row["status"] == "new"),
        "possible_duplicate": sum(
            1 for row in rows if row["status"] == "possible_duplicate"
        ),
        "exists": sum(1 for row in rows if row["status"] == "exists"),
        "too_large": len(rows) > MAX_IMPORT_SELECTION,
    }
    return {
        "import_token": new_import_token(),
        "source_type": source_type,
        "persisted": False,
        "counts": counts,
        "items": rows,
    }


def classify_import_row(organization_id, agent_id, item, *, language="es"):
    phone_hits = find_contacts_by_normalized(
        organization_id,
        agent_id=agent_id,
        phone_normalized=item.get("phone_normalized"),
    ) if item.get("phone_normalized") else []
    email_hits = find_contacts_by_normalized(
        organization_id,
        agent_id=agent_id,
        email_normalized=item.get("email_normalized"),
    ) if item.get("email_normalized") else []
    strong = _unique(phone_hits + email_hits)
    if strong:
        match = strong[0]
        return {
            "status": "exists",
            "match": _public_match(match),
            "suggested_action": "use_existing",
            "reason": "phone" if phone_hits else "email",
        }

    name = (item.get("name") or "").strip()
    if not name:
        return {
            "status": "new",
            "match": None,
            "suggested_action": "import",
            "reason": "",
        }
    fuzzy = match_contacts(organization_id, agent_id, name, language=language)
    candidates = []
    if fuzzy.get("contact"):
        candidates.append(fuzzy["contact"])
    candidates.extend(fuzzy.get("candidates") or [])
    candidates = [
        contact
        for contact in _unique(candidates)
        if not _same_strong_identity(item, contact)
    ]
    if candidates and not item.get("phone_normalized") and not item.get("email_normalized"):
        return {
            "status": "possible_duplicate",
            "match": _public_match(candidates[0]),
            "suggested_action": "review",
            "reason": "name",
        }
    if candidates and fuzzy.get("status") in ("single", "ambiguous"):
        return {
            "status": "possible_duplicate",
            "match": _public_match(candidates[0]),
            "suggested_action": "review",
            "reason": "name",
        }
    return {
        "status": "new",
        "match": None,
        "suggested_action": "import",
        "reason": "",
    }


def confirm_import(
    organization_id,
    agent_id,
    preview,
    *,
    decisions=None,
    import_token=None,
):
    """Persist only confirmed rows. Idempotent on import_token."""
    organization_id = require_organization_id(organization_id)
    if agent_id is None:
        raise ContactError("contacts_err_agent_required")
    token = import_token or (preview or {}).get("import_token")
    if not token:
        raise ContactError("contacts_import_token_required")
    existing = get_import_batch(organization_id, agent_id, token)
    if existing:
        return {
            "idempotent": True,
            "created": [],
            "updated": [],
            "used": [],
            "created_count": existing["created_count"],
            "updated_count": existing["updated_count"],
            "import_token": token,
        }

    source_type = (preview or {}).get("source_type") or "phone_import"
    items = (preview or {}).get("items") or []
    decision_map = decisions or {}
    created = []
    updated = []
    used = []

    for index, item in enumerate(items):
        action = decision_map.get(str(index), decision_map.get(index))
        if not action:
            action = item.get("action") or item.get("suggested_action")
        if action in ("review", None):
            action = "import" if item.get("status") == "new" else "skip"
        if action == "skip" or item.get("selected") is False:
            continue
        if action == "use_existing":
            match_id = _match_id(item, decision_map, index)
            if match_id:
                used.append(load_contact(organization_id, match_id, agent_id=agent_id))
            continue
        if action == "update_existing":
            match_id = _match_id(item, decision_map, index)
            if not match_id:
                continue
            contact = update_existing_from_import(
                organization_id,
                match_id,
                item,
                agent_id=agent_id,
            )
            updated.append(contact)
            continue
        if action in ("import", "create_separate"):
            contact = create_agent_contact(
                organization_id,
                agent_id,
                {
                    "name": item.get("name"),
                    "phone": item.get("phone"),
                    "email": item.get("email"),
                    "company": item.get("company"),
                    "contact_type": item.get("contact_type"),
                    "notes": item.get("notes"),
                    "status": "lead",
                    "source": "other",
                    "source_type": source_type,
                },
            )
            created.append(contact)

    record_import_batch(
        organization_id,
        agent_id,
        import_token=token,
        source_type=source_type,
        created_count=len(created),
        updated_count=len(updated),
    )
    return {
        "idempotent": False,
        "created": created,
        "updated": updated,
        "used": used,
        "created_count": len(created),
        "updated_count": len(updated),
        "import_token": token,
    }


def update_existing_from_import(organization_id, contact_id, incoming, *, agent_id=None):
    contact = load_contact(organization_id, contact_id, agent_id=agent_id)
    merged = merge_import_fields(contact, incoming)
    payload = {
        "name": merged["name"],
        "phone": merged["phone"],
        "email": merged["email"],
        "status": contact.get("status") or "lead",
        "source": contact.get("source") or "manual",
        "notes": merged["notes"],
        "company": merged["company"],
        "contact_type": merged["contact_type"],
        "first_name": merged.get("first_name"),
        "last_name": merged.get("last_name"),
        "merge_preferences": True,
        "preferences": {},
    }
    return update_agent_contact(
        organization_id,
        contact_id,
        payload,
        agent_id=agent_id,
    )


def merge_import_fields(existing, incoming):
    """Fill blanks from import. Empty incoming values never wipe stored data."""
    def _pick(field, *aliases):
        for key in (field, *aliases):
            value = (incoming.get(key) or "").strip()
            if value:
                return value
        return (existing.get(field) or "").strip()

    name = _pick("name")
    first_name, last_name = split_display_name(name)
    return {
        "name": name or existing.get("name"),
        "phone": _pick("phone"),
        "email": _pick("email"),
        "notes": _pick("notes"),
        "company": _pick("company"),
        "contact_type": _pick("contact_type"),
        "first_name": first_name or existing.get("first_name") or "",
        "last_name": last_name or existing.get("last_name") or "",
    }


def import_field_diff(existing, incoming):
    merged = merge_import_fields(existing, incoming)
    changes = []
    for field in ("phone", "email", "company", "notes", "name"):
        before = (existing.get(field) or "").strip()
        after = (merged.get(field) or "").strip()
        if after and after != before:
            changes.append({"field": field, "current": before, "incoming": after})
    return changes


def parse_vcard_text(text):
    cards = []
    current = {}
    for raw_line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper == "BEGIN:VCARD":
            current = {}
            continue
        if upper == "END:VCARD":
            if current:
                cards.append(current)
            current = {}
            continue
        key, _, value = line.partition(":")
        key = key.split(";")[0].upper()
        value = value.strip()
        if key in {"FN", "N"} and value and not current.get("name"):
            if key == "N":
                parts = [part.strip() for part in value.split(";") if part.strip()]
                current["name"] = " ".join(reversed(parts[:2])) if len(parts) >= 2 else value
            else:
                current["name"] = value
        elif key == "TEL" and value and not current.get("phone"):
            current["phone"] = value
        elif key == "EMAIL" and value and not current.get("email"):
            current["email"] = value
        elif key == "ORG" and value and not current.get("company"):
            current["company"] = value
    return cards


def _normalize_incoming(raw):
    name = (raw.get("name") or raw.get("display_name") or "").strip()
    phone = (raw.get("phone") or raw.get("tel") or "").strip()
    email = (raw.get("email") or "").strip()
    return {
        "name": name,
        "phone": phone,
        "email": email,
        "company": (raw.get("company") or "").strip(),
        "notes": (raw.get("notes") or "").strip(),
        "contact_type": (raw.get("contact_type") or "").strip(),
        "phone_normalized": normalize_phone(phone),
        "email_normalized": normalize_email(email),
    }


def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else ""
    return value or ""


def _unique(records):
    seen = set()
    unique = []
    for record in records:
        key = record.get("id")
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def _public_match(contact):
    if not contact:
        return None
    return {
        "id": contact.get("id"),
        "name": contact.get("name") or "",
        "phone": contact.get("phone") or "",
        "email": contact.get("email") or "",
    }


def _same_strong_identity(item, contact):
    if item.get("phone_normalized") and contact.get("phone_normalized"):
        if item["phone_normalized"] == contact["phone_normalized"]:
            return True
    if item.get("email_normalized") and contact.get("email_normalized"):
        if item["email_normalized"] == contact["email_normalized"]:
            return True
    return False


def _match_id(item, decision_map, index):
    raw = (
        decision_map.get(f"{index}_match_id")
        or item.get("match_id")
        or (item.get("match") or {}).get("id")
    )
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
