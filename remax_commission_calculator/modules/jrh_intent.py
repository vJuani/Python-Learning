"""Global JRH IA intent router.

Orchestrates existing Agenda, Contacts, matcher and Billing modules.
Never writes data. Callers must confirm write intents separately.
"""

from __future__ import annotations

import re
import unicodedata

from modules.agenda_ai import interpret_agenda_input
from modules.agenda_nlp import detect_task_type, parse_agenda_prompt
from modules.contacts import FILTER_NO_NEXT, list_contact_cards, match_contacts
from modules.database.operations_repository import filter_operations
from modules.database.tenant import require_organization_id
from modules.i18n import translate
from modules.organization_time import now_utc, organization_timezone
from modules.pending_actions import (
    build_agent_pending_actions,
    summarize_pending_actions,
)


INTENT_AGENDA = "agenda"
INTENT_PROPERTY_SEARCH = "property_search"
INTENT_CONTACT = "contact"
INTENT_PENDING = "pending"
INTENT_OPERATION = "operation"
INTENT_INVOICE = "invoice"
INTENT_NAVIGATION = "navigation"
INTENT_UNKNOWN = "unknown"

STATUS_READY = "ready"
STATUS_NEEDS_ATTENTION = "needs_attention"
STATUS_UNSUPPORTED = "unsupported"

WRITE_TYPES = {INTENT_AGENDA}

TYPE_LABEL_KEYS = {
    INTENT_AGENDA: "jrh_type_agenda",
    INTENT_PROPERTY_SEARCH: "jrh_type_property_search",
    INTENT_CONTACT: "jrh_type_contact",
    INTENT_PENDING: "jrh_type_pending",
    INTENT_OPERATION: "jrh_type_operation",
    INTENT_INVOICE: "jrh_type_invoice",
    INTENT_NAVIGATION: "jrh_type_navigation",
    INTENT_UNKNOWN: "jrh_type_unknown",
}

_PROPERTY_HINTS = (
    "buscame propiedades",
    "buscar propiedades",
    "buscame propiedad",
    "buscame depto",
    "mostrame propiedades",
    "propiedades para",
    "propiedades de",
    "match de propiedades",
)
_PENDING_HINTS = (
    "que tengo pendiente",
    "qué tengo pendiente",
    "pendiente hoy",
    "pendientes",
    "tengo pendiente",
    "what is pending",
    "what's pending",
)
_INVOICE_HINTS = (
    "haceme la factura",
    "hacer una factura",
    "hacer la factura",
    "factura de",
    "facturar",
    "invoice",
)
_CONTACT_HINTS = (
    "clientes sin",
    "sin proxima accion",
    "sin próxima acción",
    "mostrame clientes",
    "mostrar clientes",
    "mis contactos",
    "mis clientes",
)
_OPERATION_HINTS = (
    "mostrame la operacion",
    "mostrame la operación",
    "abrir operacion",
    "abrir operación",
    "la operacion de",
    "la operación de",
)
_NAV_HINTS = (
    "ir a agenda",
    "abrir agenda",
    "ir a contactos",
    "abrir contactos",
    "ir a facturacion",
    "ir a facturación",
    "ir a propiedades",
)
_AGENDA_HINTS = (
    "agendame",
    "agendar",
    "agenda",
    "recordame",
    "visita",
    "llamar",
    "llamada",
    "reunion",
    "reunión",
)
_NEXT_INTENT = (
    r"busc\w+\s+propiedades|"
    r"mostrame\s+propiedades|"
    r"propiedades\s+para|"
    r"decime\s+(?:qu[eé]\s+)?tengo\s+pendiente|"
    r"qu[eé]\s+tengo\s+pendiente|"
    r"pendientes|"
    r"haceme\s+(?:la\s+)?factura|"
    r"factura|"
    r"agend\w+|"
    r"visita|"
    r"llamad|"
    r"clientes|"
    r"contactos|"
    r"operaci|"
    r"ir\s+a\s+"
)
_SPLIT_RE = re.compile(
    rf"(?:,\s+|\s+y\s+|\s+despu[eé]s\s+|\s+luego\s+)"
    rf"(?=(?:decime\s+|mostrame\s+|busc\w+\s+|agend\w+\s+|haceme\s+)?"
    rf"(?:{_NEXT_INTENT}))",
    re.IGNORECASE,
)
_PERSON_RE = re.compile(
    r"\b(?:para|con|a)\s+([A-Za-zÁÉÍÓÚÑÜáéíóúñü]+"
    r"(?:\s+[A-Za-zÁÉÍÓÚÑÜáéíóúñü]+)?)",
    re.IGNORECASE,
)
_OPERATION_HINT_RE = re.compile(
    r"(?:operaci[oó]n\s+de\s+|factura\s+de(?:\s+la\s+operaci[oó]n\s+de)?\s+)"
    r"(.+)$",
    re.IGNORECASE,
)


class JrhIntentError(ValueError):
    def __init__(self, message_key, **kwargs):
        super().__init__(message_key)
        self.message_key = message_key
        self.kwargs = kwargs


def _fold(text):
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).lower()


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


def split_jrh_segments(prompt):
    text = (prompt or "").strip()
    if not text:
        return []
    parts = [part.strip(" ,") for part in _SPLIT_RE.split(text) if part.strip(" ,")]
    return parts or [text]


def classify_jrh_segment(text):
    folded = _fold(text)
    if any(hint in folded for hint in _PROPERTY_HINTS):
        return INTENT_PROPERTY_SEARCH
    if any(hint in folded for hint in _PENDING_HINTS):
        return INTENT_PENDING
    if any(hint in folded for hint in _INVOICE_HINTS):
        return INTENT_INVOICE
    if any(hint in folded for hint in _CONTACT_HINTS):
        return INTENT_CONTACT
    if any(hint in folded for hint in _OPERATION_HINTS):
        return INTENT_OPERATION
    if any(hint in folded for hint in _NAV_HINTS):
        return INTENT_NAVIGATION
    if detect_task_type(text) != "other" or any(
        hint in folded for hint in _AGENDA_HINTS
    ):
        return INTENT_AGENDA
    return INTENT_UNKNOWN


def extract_person_name(text):
    match = _PERSON_RE.search(text or "")
    if not match:
        return ""
    name = (match.group(1) or "").strip()
    if _fold(name) in {"las", "los", "la", "el", "una", "un"}:
        return ""
    return name


def _intent(
    intent_type,
    status,
    *,
    language,
    summary="",
    message_key="",
    data=None,
    confirm_required=None,
):
    write = intent_type in WRITE_TYPES
    return {
        "type": intent_type,
        "status": status,
        "title_key": TYPE_LABEL_KEYS[intent_type],
        "title": _t(TYPE_LABEL_KEYS[intent_type], language),
        "summary": summary,
        "message_key": message_key,
        "message": _t(message_key, language) if message_key else "",
        "confirm_required": write if confirm_required is None else confirm_required,
        "action_kind": "write" if write else "read",
        "cta_key": "jrh_cta_review" if write else "jrh_cta_open",
        "href": "",
        "data": data or {},
    }


def _resolve_contact(organization_id, agent_id, query):
    return match_contacts(organization_id, agent_id, query)


def _agenda_intent(segment, organization_id, agent_id, language, now):
    parsed = interpret_agenda_input(
        segment,
        organization_id,
        agent_id,
        now=now,
    )
    items = parsed.get("items") or []
    item = items[0] if items else {}
    raw_status = item.get("item_status") or STATUS_NEEDS_ATTENTION
    status = (
        STATUS_READY
        if raw_status == "ready"
        else STATUS_NEEDS_ATTENTION
    )
    contact_name = item.get("contact_name") or extract_person_name(segment)
    when = " ".join(
        part
        for part in (item.get("due_date"), item.get("due_time"))
        if part
    )
    summary = " · ".join(part for part in (contact_name, when) if part) or segment
    message_key = (
        "jrh_msg_agenda_ready"
        if status == STATUS_READY
        else "jrh_msg_agenda_attention"
    )
    return _intent(
        INTENT_AGENDA,
        status,
        language=language,
        summary=summary,
        message_key=message_key,
        data={
            "source_prompt": segment,
            "task_type": item.get("task_type") or detect_task_type(segment),
            "contact_name": contact_name,
            "contact_id": item.get("contact_id"),
            "due_date": item.get("due_date"),
            "due_time": item.get("due_time"),
            "item_status": raw_status,
            "candidates": item.get("contact_candidates") or [],
        },
    )


def _property_search_intent(segment, organization_id, agent_id, language):
    name = extract_person_name(segment)
    if not name:
        return _intent(
            INTENT_PROPERTY_SEARCH,
            STATUS_NEEDS_ATTENTION,
            language=language,
            summary=segment,
            message_key="jrh_msg_which_contact",
            data={"source_prompt": segment, "candidates": []},
        )
    matched = _resolve_contact(organization_id, agent_id, name)
    if matched["status"] == "single":
        contact = matched["contact"]
        return _intent(
            INTENT_PROPERTY_SEARCH,
            STATUS_READY,
            language=language,
            summary=contact.get("name") or name,
            message_key="jrh_msg_property_search_ready",
            data={
                "source_prompt": segment,
                "contact_id": contact["id"],
                "contact_name": contact.get("name"),
                "candidates": [],
            },
        )
    if matched["status"] == "ambiguous" or (
        matched["status"] == "single" and not matched.get("clear")
    ):
        return _intent(
            INTENT_PROPERTY_SEARCH,
            STATUS_NEEDS_ATTENTION,
            language=language,
            summary=name,
            message_key="jrh_msg_contacts_ambiguous",
            data={
                "source_prompt": segment,
                "contact_name": name,
                "candidates": [
                    {
                        "id": item["id"],
                        "name": item.get("name"),
                    }
                    for item in (matched.get("candidates") or [])[:8]
                ],
            },
        )
    return _intent(
        INTENT_PROPERTY_SEARCH,
        STATUS_NEEDS_ATTENTION,
        language=language,
        summary=name,
        message_key="jrh_msg_contact_missing",
        data={"source_prompt": segment, "contact_name": name, "candidates": []},
    )


def _contact_intent(segment, organization_id, agent_id, language):
    folded = _fold(segment)
    if "sin" in folded and ("proxima" in folded or "accion" in folded or "next" in folded):
        cards = list_contact_cards(
            organization_id,
            agent_id=agent_id,
            contact_filter=FILTER_NO_NEXT,
            language=language,
        )
        row = _intent(
            INTENT_CONTACT,
            STATUS_READY,
            language=language,
            summary=_t("jrh_msg_contacts_no_next", language, count=len(cards)),
            message_key="jrh_msg_contacts_no_next",
            data={
                "source_prompt": segment,
                "filter": FILTER_NO_NEXT,
                "count": len(cards),
                "names": [card.get("name") for card in cards[:5]],
            },
        )
        row["message"] = row["summary"]
        return row
    name = extract_person_name(segment)
    if not name:
        return _intent(
            INTENT_CONTACT,
            STATUS_READY,
            language=language,
            summary=_t("jrh_cta_open_contacts", language),
            message_key="jrh_msg_open_contacts",
            data={"source_prompt": segment, "filter": "all"},
        )
    matched = _resolve_contact(organization_id, agent_id, name)
    if matched["status"] == "single":
        contact = matched["contact"]
        return _intent(
            INTENT_CONTACT,
            STATUS_READY,
            language=language,
            summary=contact.get("name") or name,
            message_key="jrh_msg_contact_ready",
            data={
                "source_prompt": segment,
                "contact_id": contact["id"],
                "contact_name": contact.get("name"),
            },
        )
    if matched.get("candidates"):
        return _intent(
            INTENT_CONTACT,
            STATUS_NEEDS_ATTENTION,
            language=language,
            summary=name,
            message_key="jrh_msg_contacts_ambiguous",
            data={
                "source_prompt": segment,
                "candidates": [
                    {"id": item["id"], "name": item.get("name")}
                    for item in matched["candidates"][:8]
                ],
            },
        )
    return _intent(
        INTENT_CONTACT,
        STATUS_NEEDS_ATTENTION,
        language=language,
        summary=name,
        message_key="jrh_msg_contact_missing",
        data={"source_prompt": segment, "contact_name": name},
    )


def _pending_intent(organization_id, agent_id, user_id, language, segment):
    actions = build_agent_pending_actions(
        organization_id,
        agent_id,
        user_id=user_id,
    )
    summary = summarize_pending_actions(actions, language=language)
    row = _intent(
        INTENT_PENDING,
        STATUS_READY,
        language=language,
        summary=_t("jrh_msg_pending_count", language, count=summary["total"]),
        message_key="jrh_msg_pending_count",
        data={
            "source_prompt": segment,
            "total": summary["total"],
            "groups": summary.get("groups") or [],
        },
    )
    row["message"] = row["summary"]
    return row


def _invoice_intent(segment, organization_id, agent_id, language):
    hint = ""
    match = _OPERATION_HINT_RE.search(segment or "")
    if match:
        hint = (match.group(1) or "").strip(" .")
    operations = []
    if hint:
        operations = filter_operations(
            organization_id,
            property_address=hint,
            agent_id=agent_id,
        )[:5]
    if len(operations) == 1:
        row = operations[0]
        return _intent(
            INTENT_INVOICE,
            STATUS_READY,
            language=language,
            summary=row.get("property") or hint,
            message_key="jrh_msg_invoice_redirect",
            data={
                "source_prompt": segment,
                "operation_id": row.get("db_id"),
                "hint": hint,
            },
            confirm_required=False,
        )
    if operations:
        return _intent(
            INTENT_INVOICE,
            STATUS_NEEDS_ATTENTION,
            language=language,
            summary=hint,
            message_key="jrh_msg_operation_ambiguous",
            data={
                "source_prompt": segment,
                "hint": hint,
                "operation_ids": [row.get("db_id") for row in operations],
            },
            confirm_required=False,
        )
    return _intent(
        INTENT_INVOICE,
        STATUS_READY if not hint else STATUS_NEEDS_ATTENTION,
        language=language,
        summary=hint or _t("jrh_type_invoice", language),
        message_key=(
            "jrh_msg_invoice_redirect"
            if not hint
            else "jrh_msg_operation_missing"
        ),
        data={"source_prompt": segment, "hint": hint},
        confirm_required=False,
    )


def _operation_intent(segment, organization_id, agent_id, language):
    hint = ""
    match = _OPERATION_HINT_RE.search(segment or "")
    if match:
        hint = (match.group(1) or "").strip(" .")
    operations = []
    if hint:
        operations = filter_operations(
            organization_id,
            property_address=hint,
            agent_id=agent_id,
        )[:5]
    if len(operations) == 1:
        return _intent(
            INTENT_OPERATION,
            STATUS_READY,
            language=language,
            summary=hint,
            message_key="jrh_msg_operation_ready",
            data={
                "source_prompt": segment,
                "operation_id": operations[0].get("db_id"),
                "hint": hint,
            },
        )
    if operations:
        return _intent(
            INTENT_OPERATION,
            STATUS_NEEDS_ATTENTION,
            language=language,
            summary=hint,
            message_key="jrh_msg_operation_ambiguous",
            data={"source_prompt": segment, "hint": hint},
        )
    return _intent(
        INTENT_OPERATION,
        STATUS_NEEDS_ATTENTION if hint else STATUS_READY,
        language=language,
        summary=hint or _t("jrh_type_operation", language),
        message_key=(
            "jrh_msg_operation_missing" if hint else "jrh_msg_open_operations"
        ),
        data={"source_prompt": segment, "hint": hint},
    )


def _navigation_intent(segment, language):
    folded = _fold(segment)
    target = "agenda"
    if "contacto" in folded or "cliente" in folded:
        target = "contacts"
    elif "factura" in folded or "billing" in folded:
        target = "billing"
    elif "propiedad" in folded:
        target = "properties"
    elif "operaci" in folded:
        target = "operations"
    return _intent(
        INTENT_NAVIGATION,
        STATUS_READY,
        language=language,
        summary=_t(f"jrh_nav_{target}", language),
        message_key=f"jrh_nav_{target}",
        data={"source_prompt": segment, "target": target},
    )


def interpret_jrh_request(
    prompt,
    *,
    organization_id,
    agent_id,
    user_id=None,
    language="es",
    now=None,
):
    """Return product intents. Never creates or updates records."""
    organization_id = require_organization_id(organization_id)
    text = (prompt or "").strip()
    now = now or now_utc()
    if not text:
        raise JrhIntentError("jrh_err_empty")

    intents = []
    for segment in split_jrh_segments(text):
        kind = classify_jrh_segment(segment)
        if kind == INTENT_AGENDA:
            intents.append(
                _agenda_intent(segment, organization_id, agent_id, language, now)
            )
        elif kind == INTENT_PROPERTY_SEARCH:
            intents.append(
                _property_search_intent(
                    segment, organization_id, agent_id, language
                )
            )
        elif kind == INTENT_CONTACT:
            intents.append(
                _contact_intent(segment, organization_id, agent_id, language)
            )
        elif kind == INTENT_PENDING:
            intents.append(
                _pending_intent(
                    organization_id, agent_id, user_id, language, segment
                )
            )
        elif kind == INTENT_INVOICE:
            intents.append(
                _invoice_intent(segment, organization_id, agent_id, language)
            )
        elif kind == INTENT_OPERATION:
            intents.append(
                _operation_intent(segment, organization_id, agent_id, language)
            )
        elif kind == INTENT_NAVIGATION:
            intents.append(_navigation_intent(segment, language))
        else:
            intents.append(
                _intent(
                    INTENT_UNKNOWN,
                    STATUS_UNSUPPORTED,
                    language=language,
                    summary=segment,
                    message_key="jrh_msg_unsupported",
                    data={"source_prompt": segment},
                    confirm_required=False,
                )
            )

    return {
        "prompt": text,
        "understood_count": len(intents),
        "intents": intents,
        "wrote": False,
    }


# Keep parse available for tests that inspect local agenda drafts.
parse_local_agenda = parse_agenda_prompt
_ = organization_timezone
