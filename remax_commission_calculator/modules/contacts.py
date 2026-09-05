"""
Light commercial contacts for JRH One agents.

v1 contacts are owned by one agent. ``visibility`` is always private
today so a later team/office share does not require a rebuild.
"""

from __future__ import annotations

import json
import re

import unicodedata

from modules.database.agent_tasks_repository import (
    STATUS_COMPLETED,
    STATUS_PENDING,
    list_agent_tasks,
)
from modules.database.agents_repository import get_agent_record
from modules.database.contacts_repository import (
    SOURCES,
    STATUSES,
    VISIBILITY_PRIVATE,
    create_contact,
    get_contact,
    list_contacts,
    set_task_contact_id,
    update_contact,
)
from modules.database.tenant import require_organization_id
from modules.i18n import translate
from modules.organization_time import (
    format_local_date_iso,
    format_local_datetime,
    now_utc,
    now_utc_iso,
    organization_timezone,
    parse_utc_iso,
    to_local,
)
from modules.visit_outcome import (
    format_budget_label,
    normalize_visit_outcome,
    outcome_is_present,
)


MAX_NAME = 120
MAX_PHONE = 40
MAX_EMAIL = 120
MAX_NOTES = 2000
MAX_CHIP = 40


def _small_int(value):
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or number >= 30:
        return None
    return number

FILTER_ALL = "all"
FILTER_ACTIVE = "active"
FILTER_LEADS = "leads"
FILTER_NO_NEXT = "no_next"
CONTACT_FILTERS = (
    FILTER_ALL,
    FILTER_ACTIVE,
    FILTER_LEADS,
    FILTER_NO_NEXT,
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NAME_STOPWORDS = {"con", "de", "del", "el", "la", "las", "los", "y"}


class ContactError(Exception):
    def __init__(self, message_key, **kwargs):
        super().__init__(message_key)
        self.message_key = message_key
        self.kwargs = kwargs


def _clean(value, *, max_length):
    return (value or "").strip()[:max_length]


def _optional(value, *, max_length):
    text = _clean(value, max_length=max_length)
    return text or None


def normalize_preferences(raw):
    if not raw:
        return {}

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}

    if not isinstance(raw, dict):
        return {}

    from modules.visit_outcome import _as_list, _normalize_budget

    areas = _as_list(raw.get("areas"))
    features = _as_list(raw.get("features"))
    types = _as_list(raw.get("property_types") or raw.get("property_type"))
    budget = _normalize_budget(raw.get("budget"))
    rooms = _small_int(raw.get("rooms"))
    bedrooms = _small_int(raw.get("bedrooms"))

    prefs = {}
    if areas:
        prefs["areas"] = areas
    if budget:
        prefs["budget"] = budget
    if types:
        prefs["property_types"] = types
    if rooms is not None and rooms < 30:
        prefs["rooms"] = rooms
    if bedrooms is not None and bedrooms < 30:
        prefs["bedrooms"] = bedrooms
    if features:
        prefs["features"] = features

    return prefs


def merge_contact_preferences(existing, incoming):
    """Union lists and fill missing scalars. Never wipe stored values."""
    current = normalize_preferences(existing)
    extra = normalize_preferences(incoming)
    merged = dict(current)

    for key in ("areas", "features", "property_types"):
        values = []
        seen = set()
        for item in (current.get(key) or []) + (extra.get(key) or []):
            folded = item.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            values.append(item)
        if values:
            merged[key] = values

    extra_budget = extra.get("budget") or {}
    current_budget = dict(current.get("budget") or {})
    for key, value in extra_budget.items():
        if current_budget.get(key) in (None, ""):
            current_budget[key] = value
    if current_budget:
        merged["budget"] = current_budget

    for key in ("rooms", "bedrooms"):
        if merged.get(key) is None and extra.get(key) is not None:
            merged[key] = extra[key]

    return merged


def _fold_name(text):
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).lower()


def _name_tokens(text):
    return [
        token
        for token in _fold_name(text).split()
        if len(token) >= 2 and token not in _NAME_STOPWORDS
    ]


def _phone_digits(value):
    digits = re.sub(r"\D", "", value or "")
    return digits if len(digits) >= 8 else None


def _unique_contacts(records):
    seen = set()
    unique = []
    for record in records:
        record_id = record.get("id")
        if record_id in seen:
            continue
        seen.add(record_id)
        unique.append(record)
    return unique


def public_contact_candidate(contact, *, language="es"):
    prefs = normalize_preferences(contact.get("preferences_json"))
    budget = prefs.get("budget") or {}
    return {
        "id": contact.get("id"),
        "name": contact.get("name") or "",
        "phone": contact.get("phone") or "",
        "search_line": _search_line(prefs, language),
        "budget_label": (
            format_budget_label(budget) if budget.get("max") else ""
        ),
    }


def match_contacts(
    organization_id,
    agent_id,
    query,
    *,
    phone=None,
    language="es",
):
    """
    Conservative 0/1/N matcher. Never auto-picks among several
    first-name hits. Phone and full-name matches are the only
    clear singles that may be linked without a second click.
    """
    query = (query or "").strip()
    phone = _phone_digits(phone) or _phone_digits(query)
    tokens = _name_tokens(query)

    empty = {
        "status": "empty",
        "contact": None,
        "candidates": [],
        "clear": False,
    }
    if not query and not phone:
        return empty

    records = list_contacts(
        organization_id,
        agent_id=agent_id,
        limit=200,
    )
    folded_query = " ".join(tokens)
    phone_hits = []
    exact_hits = []
    token_hits = []
    loose_hits = []

    for record in records:
        contact_tokens = _name_tokens(record.get("name"))
        folded_name = " ".join(contact_tokens)
        record_phone = _phone_digits(record.get("phone"))

        if phone and record_phone and phone == record_phone:
            phone_hits.append(record)
        if folded_query and folded_name == folded_query:
            exact_hits.append(record)
        elif tokens and contact_tokens and all(
            token in contact_tokens for token in tokens
        ):
            if len(tokens) >= 2:
                token_hits.append(record)
            else:
                loose_hits.append(record)

    def _result(hits, *, clear):
        unique = _unique_contacts(hits)
        if not unique:
            return None
        if len(unique) == 1:
            return {
                "status": "single",
                "contact": unique[0],
                "candidates": unique,
                "clear": clear,
            }
        return {
            "status": "ambiguous",
            "contact": None,
            "candidates": unique,
            "clear": False,
        }

    for hits, clear in (
        (phone_hits, True),
        (exact_hits, True),
        (token_hits, True),
        (loose_hits, False),
    ):
        matched = _result(hits, clear=clear)
        if matched:
            return matched

    return {
        "status": "none",
        "contact": None,
        "candidates": [],
        "clear": False,
    }


def name_refers_to_contact(query, contact):
    tokens = _name_tokens(query)
    contact_tokens = _name_tokens((contact or {}).get("name"))
    if not tokens or not contact_tokens:
        return False
    return all(token in contact_tokens for token in tokens)


def apply_known_contact(draft, contact, *, language="es"):
    preview = public_contact_candidate(contact, language=language)
    draft["contact_id"] = contact["id"]
    draft["contact_name"] = contact.get("name") or draft.get("contact_name")
    draft["contact_match"] = "single"
    draft["contact_candidates"] = [preview]
    draft["contact_preview"] = preview
    return draft


def bind_compose_contact(items, contact, *, language="es"):
    if not contact:
        return items

    for item in items:
        if item.get("contact_id"):
            continue
        name = (item.get("contact_name") or "").strip()
        if not name or name_refers_to_contact(name, contact):
            apply_known_contact(item, contact, language=language)

    return items


def preferences_from_outcome(outcome):
    data = normalize_visit_outcome(outcome) if outcome else {}
    return normalize_preferences(
        {
            "areas": data.get("areas"),
            "budget": data.get("budget"),
            "features": data.get("preferences"),
        }
    )


def diff_preference_update(existing, incoming):
    current = normalize_preferences(existing)
    extra = normalize_preferences(incoming)
    additions = []
    fills = []
    conflicts = []

    for field, label_key in (
        ("areas", "contacts_merge_add_area"),
        ("features", "contacts_merge_add_feature"),
        ("property_types", "contacts_merge_add_type"),
    ):
        seen = {item.casefold() for item in (current.get(field) or [])}
        for value in extra.get(field) or []:
            if value.casefold() in seen:
                continue
            additions.append(
                {
                    "field": field,
                    "value": value,
                    "label_key": label_key,
                }
            )

    current_budget = dict(current.get("budget") or {})
    extra_budget = extra.get("budget") or {}
    for key in ("min", "max", "currency"):
        incoming_value = extra_budget.get(key)
        stored = current_budget.get(key)
        if incoming_value in (None, ""):
            continue
        if stored in (None, ""):
            fills.append(
                {
                    "key": f"budget_{key}",
                    "field": f"budget.{key}",
                    "value": incoming_value,
                }
            )
        elif stored != incoming_value:
            payload = {
                "key": f"budget_{key}",
                "field": f"budget.{key}",
                "current": stored,
                "incoming": incoming_value,
            }
            if key in ("min", "max"):
                payload["current_label"] = format_budget_label(
                    {"max": stored, "currency": current_budget.get("currency")}
                )
                payload["incoming_label"] = format_budget_label(
                    {
                        "max": incoming_value,
                        "currency": extra_budget.get("currency")
                        or current_budget.get("currency"),
                    }
                )
            else:
                payload["current_label"] = str(stored)
                payload["incoming_label"] = str(incoming_value)
            conflicts.append(payload)

    for key in ("rooms", "bedrooms"):
        incoming_value = extra.get(key)
        stored = current.get(key)
        if incoming_value is None:
            continue
        if stored is None:
            fills.append(
                {
                    "key": key,
                    "field": key,
                    "value": incoming_value,
                }
            )
        elif stored != incoming_value:
            conflicts.append(
                {
                    "key": key,
                    "field": key,
                    "current": stored,
                    "incoming": incoming_value,
                    "current_label": str(stored),
                    "incoming_label": str(incoming_value),
                }
            )

    return {
        "has_changes": bool(additions or fills or conflicts),
        "additions": additions,
        "fills": fills,
        "conflicts": conflicts,
        "incoming": extra,
        "current": current,
    }


def apply_preference_update(existing, incoming, *, accepted_conflicts=None):
    accepted = {item for item in (accepted_conflicts or []) if item}
    current = normalize_preferences(existing)
    extra = normalize_preferences(incoming)
    merged = merge_contact_preferences(current, extra)
    current_budget = dict(current.get("budget") or {})
    extra_budget = extra.get("budget") or {}
    budget = dict(merged.get("budget") or {})

    for key in ("min", "max", "currency"):
        incoming_value = extra_budget.get(key)
        stored = current_budget.get(key)
        if incoming_value in (None, "") or stored in (None, ""):
            continue
        if stored != incoming_value and f"budget_{key}" in accepted:
            budget[key] = incoming_value
        elif stored != incoming_value:
            budget[key] = stored

    if budget:
        merged["budget"] = budget

    for key in ("rooms", "bedrooms"):
        incoming_value = extra.get(key)
        stored = current.get(key)
        if incoming_value is None or stored is None:
            continue
        if stored != incoming_value and key in accepted:
            merged[key] = incoming_value
        elif stored != incoming_value:
            merged[key] = stored

    return merged


def save_contact_preference_update(
    organization_id,
    contact_id,
    incoming,
    *,
    accepted_conflicts=None,
    agent_id=None,
):
    contact = load_contact(
        organization_id,
        contact_id,
        agent_id=agent_id,
    )
    merged = apply_preference_update(
        contact.get("preferences_json"),
        incoming,
        accepted_conflicts=accepted_conflicts,
    )
    return update_contact(
        contact["id"],
        organization_id,
        preferences_json=json.dumps(merged, ensure_ascii=False) if merged else "",
    )


def touch_contact_interaction(organization_id, contact_id):
    if not contact_id:
        return None
    organization_id = require_organization_id(organization_id)
    return update_contact(
        contact_id,
        organization_id,
        last_interacted_at=now_utc_iso(),
    )


def preferences_from_form(form):
    areas = [item.strip() for item in form.getlist("area") if item.strip()]
    extra_areas = [
        part.strip()
        for part in (form.get("areas") or "").replace(",", "\n").splitlines()
        if part.strip()
    ]
    features = [item.strip() for item in form.getlist("feature") if item.strip()]
    extra_features = [
        part.strip()
        for part in (form.get("features") or "").replace(",", "\n").splitlines()
        if part.strip()
    ]
    types = [
        item.strip()
        for item in form.getlist("property_type")
        if item.strip()
    ]
    if form.get("property_types"):
        types.extend(
            part.strip()
            for part in form.get("property_types").replace(",", "\n").splitlines()
            if part.strip()
        )

    return normalize_preferences(
        {
            "areas": areas + extra_areas,
            "features": features + extra_features,
            "property_types": types,
            "rooms": form.get("rooms"),
            "bedrooms": form.get("bedrooms"),
            "budget": {
                "min": form.get("budget_min"),
                "max": form.get("budget_max"),
                "currency": form.get("budget_currency"),
            },
        }
    )


def whatsapp_digits(phone):
    digits = re.sub(r"\D", "", phone or "")
    return digits or None


def _validate_payload(payload):
    name = _clean(payload.get("name"), max_length=MAX_NAME)
    if not name:
        raise ContactError("contacts_err_name_required")

    email = _optional(payload.get("email"), max_length=MAX_EMAIL)
    if email and not _EMAIL_RE.match(email):
        raise ContactError("contacts_err_invalid_email")

    status = (payload.get("status") or "lead").strip()
    if status not in STATUSES:
        raise ContactError("contacts_err_invalid_status")

    source = (payload.get("source") or "manual").strip()
    if source not in SOURCES:
        raise ContactError("contacts_err_invalid_source")

    return {
        "name": name,
        "phone": _optional(payload.get("phone"), max_length=MAX_PHONE),
        "email": email,
        "status": status,
        "source": source,
        "notes": _optional(payload.get("notes"), max_length=MAX_NOTES),
        "preferences": normalize_preferences(payload.get("preferences")),
    }


def _owner_agent(organization_id, agent_id):
    if agent_id is None:
        raise ContactError("contacts_err_agent_required")

    agent = get_agent_record(agent_id, organization_id)
    if agent is None:
        raise ContactError("contacts_err_agent_not_found")

    return agent


def create_agent_contact(
    organization_id,
    agent_id,
    payload,
):
    organization_id = require_organization_id(organization_id)
    _owner_agent(organization_id, agent_id)
    validated = _validate_payload(payload)
    prefs = validated["preferences"]

    return create_contact(
        organization_id,
        agent_id,
        name=validated["name"],
        phone=validated["phone"],
        email=validated["email"],
        status=validated["status"],
        source=validated["source"],
        visibility=VISIBILITY_PRIVATE,
        notes=validated["notes"],
        preferences_json=json.dumps(prefs, ensure_ascii=False) if prefs else None,
    )


def load_contact(
    organization_id,
    contact_id,
    *,
    agent_id=None,
):
    organization_id = require_organization_id(organization_id)
    contact = get_contact(contact_id, organization_id)

    if contact is None:
        raise ContactError("contacts_err_not_found")

    if agent_id is not None and contact["agent_id"] != agent_id:
        raise ContactError("contacts_err_forbidden")

    return contact


def update_agent_contact(
    organization_id,
    contact_id,
    payload,
    *,
    agent_id=None,
):
    contact = load_contact(
        organization_id,
        contact_id,
        agent_id=agent_id,
    )
    validated = _validate_payload(payload)
    prefs = validated["preferences"]
    if payload.get("merge_preferences"):
        prefs = merge_contact_preferences(
            contact.get("preferences_json"),
            prefs,
        )

    return update_contact(
        contact_id,
        organization_id,
        name=validated["name"],
        phone=validated["phone"] or "",
        email=validated["email"] or "",
        status=validated["status"],
        source=validated["source"],
        notes=validated["notes"] or "",
        preferences_json=json.dumps(prefs, ensure_ascii=False) if prefs else "",
    )


def link_task_to_contact(
    organization_id,
    task_id,
    contact_id,
    *,
    agent_id=None,
):
    """Explicit link only. Never matches by contact_name."""
    contact = load_contact(
        organization_id,
        contact_id,
        agent_id=agent_id,
    )
    updated = set_task_contact_id(task_id, organization_id, contact["id"])
    if not updated:
        raise ContactError("contacts_err_task_not_found")

    return contact


def list_linked_tasks(organization_id, contact_id, *, limit=50):
    return list_agent_tasks(
        organization_id,
        contact_id=contact_id,
        statuses=None,
        order="desc",
        limit=limit,
    )


def next_pending_task(organization_id, contact_id):
    pending = list_agent_tasks(
        organization_id,
        contact_id=contact_id,
        statuses=(STATUS_PENDING,),
        order="asc",
        limit=1,
    )
    return pending[0] if pending else None


def build_contact_summary(contact, *, language="es", linked_tasks=None):
    """Deterministic copy from stored fields only."""
    prefs = normalize_preferences(contact.get("preferences_json"))
    name = (contact.get("name") or "").split(" ")[0] or contact.get("name") or ""
    parts = []

    rooms = prefs.get("rooms")
    areas = prefs.get("areas") or []
    if rooms and areas:
        parts.append(
            translate(
                "contacts_summary_rooms_areas",
                language,
                name=name,
                rooms=rooms,
                areas=_join_areas(areas, language),
            )
        )
    elif rooms:
        parts.append(
            translate(
                "contacts_summary_rooms",
                language,
                name=name,
                rooms=rooms,
            )
        )
    elif areas:
        parts.append(
            translate(
                "contacts_summary_areas",
                language,
                name=name,
                areas=_join_areas(areas, language),
            )
        )

    budget = prefs.get("budget") or {}
    if budget.get("min") and budget.get("max"):
        parts.append(
            translate(
                "contacts_summary_budget_range",
                language,
                minimum=format_budget_label(
                    {"max": budget["min"], "currency": budget.get("currency")}
                ),
                maximum=format_budget_label(budget),
            )
        )
    elif budget.get("max"):
        parts.append(
            translate(
                "contacts_summary_budget_max",
                language,
                amount=format_budget_label(budget),
            )
        )

    features = prefs.get("features") or []
    if features:
        parts.append(
            translate(
                "contacts_summary_features",
                language,
                features=_join_areas(features, language),
            )
        )

    visits = [
        task
        for task in (linked_tasks or [])
        if task.get("task_type") == "visit"
    ]
    if visits:
        parts.append(
            translate(
                "contacts_summary_visits",
                language,
                count=len(visits),
            )
        )

    if not parts:
        return ""

    text = " ".join(parts)
    if not text.endswith("."):
        text += "."
    return text


def _join_areas(values, language):
    if len(values) == 1:
        return values[0]
    if language == "en":
        return f"{', '.join(values[:-1])} and {values[-1]}"
    return f"{', '.join(values[:-1])} y {values[-1]}"


def decorate_contact(
    contact,
    *,
    organization_id,
    language="es",
    now=None,
):
    tz = organization_timezone(organization_id)
    now = now or now_utc()
    prefs = normalize_preferences(contact.get("preferences_json"))
    linked = list_linked_tasks(organization_id, contact["id"])
    next_task = next_pending_task(organization_id, contact["id"])
    digits = whatsapp_digits(contact.get("phone"))
    budget = prefs.get("budget") or {}

    history = _history_events(
        contact,
        linked,
        tz=tz,
        language=language,
    )
    viewed = _viewed_properties(linked)
    last_label, last_task = _last_interaction_label(linked, tz, language)
    recommendation = _contact_recommendation(
        contact,
        linked,
        next_task,
        language=language,
    )

    return {
        **contact,
        "preferences": prefs,
        "status_label": translate(f"contacts_status_{contact['status']}", language),
        "source_label": translate(f"contacts_source_{contact['source']}", language),
        "search_line": _search_line(prefs, language),
        "budget_label": format_budget_label(budget) if budget.get("max") else "",
        "budget_range_label": _budget_range_label(budget),
        "whatsapp_url": f"https://wa.me/{digits}" if digits else None,
        "tel_url": f"tel:{digits}" if digits else None,
        "has_phone": bool(digits),
        "next_task": next_task,
        "has_next_action": next_task is not None,
        "next_action_label": _next_action_label(next_task, tz, language),
        "summary": build_contact_summary(
            contact,
            language=language,
            linked_tasks=linked,
        ),
        "history": history,
        "visit_count": sum(
            1 for task in linked if task.get("task_type") == "visit"
        ),
        "viewed_properties": viewed,
        "viewed_count": len(viewed),
        "last_interaction_label": last_label,
        "last_interaction_task": last_task,
        "recommendation": recommendation,
        "linked_task_count": len(linked),
    }


def _search_line(prefs, language):
    bits = []
    if prefs.get("rooms"):
        bits.append(
            translate(
                "contacts_rooms_label",
                language,
                count=prefs["rooms"],
            )
        )
    if prefs.get("areas"):
        bits.append(" · ".join(prefs["areas"][:2]))
    return " · ".join(bits)


def _budget_range_label(budget):
    if not budget:
        return ""
    currency = budget.get("currency") or "USD"
    if budget.get("min") and budget.get("max"):
        low = format_budget_label(
            {"max": budget["min"], "currency": currency}
        )
        high = format_budget_label(budget)
        return f"{low} – {high.replace(currency + ' ', '')}"
    if budget.get("max"):
        return format_budget_label(budget)
    return ""


def _next_action_label(task, tz, language):
    if not task:
        return ""

    when = format_local_datetime(task.get("due_at"), tz)
    title = task.get("title") or translate(
        f"agent_task_type_{task.get('task_type')}",
        language,
    )
    return f"{when} · {title}" if when else title


def _history_events(contact, tasks, *, tz, language):
    events = [
        {
            "at": contact.get("created_at"),
            "kind": "created",
            "title": translate("contacts_history_created", language),
            "detail": "",
            "outcome": None,
        }
    ]

    for task in tasks:
        outcome = (
            normalize_visit_outcome(task.get("outcome_json"))
            if outcome_is_present(task.get("outcome_json"))
            else None
        )
        events.append(
            {
                "at": task.get("completed_at")
                or task.get("due_at")
                or task.get("created_at"),
                "kind": task.get("task_type") or "other",
                "title": translate(
                    f"agent_task_type_{task.get('task_type') or 'other'}",
                    language,
                ),
                "detail": task.get("property_address") or task.get("title") or "",
                "status": task.get("status"),
                "task_id": task.get("id"),
                "outcome": outcome,
            }
        )

    events.sort(key=lambda item: item.get("at") or "", reverse=True)

    grouped = []
    last_day = None
    for event in events:
        parsed = parse_utc_iso(event.get("at")) if event.get("at") else None
        local = to_local(event["at"], tz) if event.get("at") else None
        day = format_local_date_iso(event.get("at"), tz) if event.get("at") else ""
        event["time_label"] = (
            local.strftime("%H:%M") if local is not None else ""
        )
        event["day_key"] = day
        if day != last_day:
            grouped.append(
                {
                    "day_key": day,
                    "day_label": _day_label(local, tz, language) if local else "",
                    "events": [event],
                }
            )
            last_day = day
        else:
            grouped[-1]["events"].append(event)

    return grouped


def _day_label(local, tz, language):
    today = now_utc().astimezone(tz).date()
    day = local.date()
    delta = (today - day).days

    if delta == 0:
        return translate("contacts_day_today", language)
    if delta == 1:
        return translate("contacts_day_yesterday", language)
    return local.strftime("%d/%m")


def _viewed_properties(tasks):
    seen = []
    ids = set()
    ordered = sorted(
        tasks or [],
        key=lambda item: item.get("due_at") or item.get("created_at") or "",
        reverse=True,
    )
    for task in ordered:
        if task.get("task_type") != "visit" or not task.get("property_id"):
            continue
        property_id = task["property_id"]
        if property_id in ids:
            continue
        ids.add(property_id)
        seen.append(
            {
                "id": property_id,
                "address": task.get("property_address") or "",
            }
        )
    return seen


def _last_interaction_label(tasks, tz, language):
    completed = [
        task
        for task in (tasks or [])
        if task.get("status") == STATUS_COMPLETED
    ]
    if not completed:
        return "", None

    completed.sort(
        key=lambda item: item.get("completed_at")
        or item.get("due_at")
        or "",
        reverse=True,
    )
    task = completed[0]
    stamp = task.get("completed_at") or task.get("due_at")
    local = to_local(stamp, tz) if stamp else None
    when = _day_label(local, tz, language) if local else ""
    kind = translate(
        f"agent_task_type_{task.get('task_type') or 'other'}",
        language,
    )
    if when and kind:
        return f"{when} · {kind}", task
    return kind, task


def _contact_recommendation(contact, tasks, next_task, *, language="es"):
    overdue = None
    missing_outcome = None
    has_visit_with_outcome = False
    now = now_utc()

    for task in tasks or []:
        if task.get("status") == STATUS_PENDING and task.get("due_at"):
            parsed = parse_utc_iso(task["due_at"])
            if parsed is not None and parsed < now:
                if overdue is None or task["due_at"] < overdue["due_at"]:
                    overdue = task
        if (
            task.get("task_type") == "visit"
            and task.get("status") == STATUS_COMPLETED
        ):
            if outcome_is_present(task.get("outcome_json")):
                has_visit_with_outcome = True
            elif missing_outcome is None:
                missing_outcome = task

    if overdue:
        return {
            "key": "overdue",
            "cta": "agenda",
            "task": overdue,
        }
    if missing_outcome:
        return {
            "key": "missing_outcome",
            "cta": "followup",
            "task": missing_outcome,
        }
    if contact.get("status") in ("lead", "active") and next_task is None:
        return {
            "key": "visit_no_next" if has_visit_with_outcome else "no_next",
            "cta": "schedule",
            "task": None,
        }
    return None


def list_contact_cards(
    organization_id,
    *,
    agent_id=None,
    contact_filter=None,
    search=None,
    language="es",
):
    organization_id = require_organization_id(organization_id)
    contact_filter = (
        contact_filter if contact_filter in CONTACT_FILTERS else FILTER_ALL
    )
    status = None
    if contact_filter == FILTER_ACTIVE:
        status = "active"
    elif contact_filter == FILTER_LEADS:
        status = "lead"

    cards = [
        decorate_contact(
            contact,
            organization_id=organization_id,
            language=language,
        )
        for contact in list_contacts(
            organization_id,
            agent_id=agent_id,
            status=status,
            search=search,
        )
    ]

    if contact_filter == FILTER_NO_NEXT:
        cards = [card for card in cards if not card["has_next_action"]]

    return cards
