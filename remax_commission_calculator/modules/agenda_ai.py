"""
Agenda intelligence: prompt, voice transcript and WhatsApp screenshot.

Reuses the Caja IA provider for images. The local parser always runs
so a missing API key never blocks typed or spoken requests.
"""

from __future__ import annotations

import json
import logging
import os
import unicodedata

from modules.agenda_nlp import (
    build_task_title,
    parse_agenda_prompt,
    parse_visit_outcome,
    split_agenda_segments,
)
from modules.contacts import (
    apply_known_contact,
    match_contacts,
    public_contact_candidate,
)
from modules.visit_outcome import normalize_visit_outcome
from modules.cash_ai_provider import (
    CashAiProviderError,
    build_multimodal_user_content,
    request_structured_json,
)
from modules.cash_receipts import (
    CashReceiptError,
    prepare_image_for_ai,
    validate_receipt_upload,
)
from modules.database.properties_repository import get_properties
from modules.organization_time import now_utc, organization_timezone


logger = logging.getLogger(__name__)

PROPERTY_STOPWORDS = {
    "casa",
    "de",
    "departamento",
    "depto",
    "dpto",
    "el",
    "en",
    "la",
    "las",
    "local",
    "los",
    "monoambiente",
    "oficina",
    "para",
    "ph",
    "por",
    "propiedad",
    "terreno",
    "ver",
}


class AgendaAiError(Exception):
    def __init__(self, message_key, **kwargs):
        super().__init__(message_key)
        self.message_key = message_key
        self.kwargs = kwargs


def _fold(text):
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).lower()


def _query_tokens(needle):
    return [
        token
        for token in needle.split()
        if len(token) > 3 and token not in PROPERTY_STOPWORDS
    ]


def match_property(organization_id, agent_id, query):
    """
    Return every real property hit. Never pick a winner when
    more than one address matches.
    """
    query = (query or "").strip()

    if not query:
        return {
            "status": "empty",
            "property": None,
            "candidates": [],
        }

    needle = _fold(query)
    tokens = _query_tokens(needle)
    meaningful = " ".join(tokens) if tokens else ""
    candidates = []
    seen_ids = set()

    for record in get_properties(organization_id, agent_id=agent_id):
        address = _fold(record.get("address") or "")
        record_id = record.get("id")

        if not address or record_id in seen_ids:
            continue

        strong = bool(
            (meaningful and (meaningful in address or address in meaningful))
            or needle in address
            or address in needle
        )
        token_hit = any(token in address for token in tokens)

        if not strong and not token_hit:
            continue

        seen_ids.add(record_id)
        candidates.append(record)

    if not candidates:
        return {
            "status": "none",
            "property": None,
            "candidates": [],
        }

    if len(candidates) == 1:
        return {
            "status": "single",
            "property": candidates[0],
            "candidates": candidates,
        }

    return {
        "status": "ambiguous",
        "property": None,
        "candidates": candidates,
    }


def _public_candidates(candidates):
    return [
        {
            "id": record.get("id"),
            "address": record.get("address") or "",
        }
        for record in candidates
    ]


TIME_REQUIRED_TYPES = {"visit", "call", "meeting"}


def item_status(draft):
    if not draft.get("task_type"):
        return "invalid"
    if draft.get("property_match") == "ambiguous" and not draft.get("property_id"):
        return "needs_attention"
    contact_match = draft.get("contact_match")
    if contact_match == "ambiguous" and not draft.get("contact_id"):
        return "needs_attention"
    if contact_match == "single" and not draft.get("contact_id"):
        return "needs_attention"
    if not draft.get("date_found") or not draft.get("due_date"):
        return "needs_attention"
    if draft.get("task_type") in TIME_REQUIRED_TYPES and (
        not draft.get("time_found") or not draft.get("due_time")
    ):
        return "needs_attention"
    return "ready"


def _draft_ui_status(draft, match):
    if match["status"] == "ambiguous":
        return "properties_ambiguous"
    if not draft.get("date_found") or (
        draft.get("task_type") in TIME_REQUIRED_TYPES and not draft.get("time_found")
    ):
        return "date_unclear"
    if match["status"] == "none":
        return "property_not_found"
    return "ready"


def _draft_warnings(draft, match):
    warnings = []

    if not draft.get("date_found"):
        warnings.append("agenda_ai_warn_date")
    if not draft.get("time_found"):
        warnings.append("agenda_ai_warn_time")
    if match["status"] == "ambiguous":
        warnings.append("agenda_ai_warn_properties_ambiguous")
    elif match["status"] == "none":
        warnings.append("agenda_ai_warn_property_not_found")
        if draft.get("task_type") == "visit":
            warnings.append("agenda_ai_warn_visit_without_property")
    if draft.get("contact_match") == "ambiguous" and not draft.get("contact_id"):
        warnings.append("agenda_ai_warn_contact_ambiguous")

    return warnings


def _apply_structured_title(draft):
    draft["title"] = build_task_title(
        draft.get("task_type"),
        draft.get("contact_name"),
        draft.get("property_query") or draft.get("property_address"),
    )
    return draft


def _enrich_draft(draft, organization_id, agent_id):
    match = match_property(
        organization_id,
        agent_id,
        draft.get("property_query") or draft.get("property_address"),
    )
    chosen = match["property"]
    query_text = draft.get("property_query") or draft.get("property_address") or ""

    draft["property_id"] = chosen["id"] if chosen else ""
    draft["property_address"] = chosen["address"] if chosen else query_text
    draft["property_match"] = match["status"]
    draft["property_candidates"] = _public_candidates(match["candidates"])
    _apply_contact_match(draft, organization_id, agent_id)
    draft["warnings"] = _draft_warnings(draft, match)
    draft["ui_status"] = _draft_ui_status(draft, match)
    _apply_structured_title(draft)
    draft["item_status"] = item_status(draft)

    return draft


def _apply_contact_match(draft, organization_id, agent_id):
    if draft.get("contact_match") == "skipped":
        draft["contact_id"] = ""
        draft["contact_candidates"] = draft.get("contact_candidates") or []
        return draft

    if draft.get("contact_id"):
        draft["contact_match"] = "single"
        if not draft.get("contact_candidates") and draft.get("contact_preview"):
            draft["contact_candidates"] = [draft["contact_preview"]]
        return draft

    match = match_contacts(
        organization_id,
        agent_id,
        draft.get("contact_name"),
    )
    draft["contact_match"] = match["status"]
    draft["contact_candidates"] = [
        public_contact_candidate(record) for record in match["candidates"]
    ]
    chosen = match.get("contact")
    if match["status"] == "single" and match.get("clear") and chosen:
        apply_known_contact(draft, chosen)
    elif chosen:
        draft["contact_id"] = ""
        draft["contact_preview"] = public_contact_candidate(chosen)
    else:
        draft["contact_id"] = ""
        draft["contact_preview"] = None
    return draft


def _merge_model_item(local, parsed):
    draft = {**local, **{key: value for key, value in parsed.items() if value}}
    if parsed.get("due_date"):
        draft["date_found"] = True
    if parsed.get("due_time"):
        draft["time_found"] = True
    return draft


def interpret_agenda_input(
    prompt,
    organization_id,
    agent_id,
    *,
    now=None,
):
    """
    Interpret typed, spoken or pasted text into one or more drafts.

    Always returns ``{"items": [...]}``. A single action is length 1.
    """
    prompt = (prompt or "").strip()

    if not prompt:
        raise AgendaAiError("agenda_ai_err_empty_prompt")

    tz = organization_timezone(organization_id)
    now_local = (now or now_utc()).astimezone(tz)
    today = now_local.date()
    segments = split_agenda_segments(prompt)
    locals_ = [
        parse_agenda_prompt(segment, today=today, now_local=now_local)
        for segment in segments
    ]

    previous_date = None
    for draft in locals_:
        if not draft.get("date_found") and previous_date:
            draft["due_date"] = previous_date
            draft["date_found"] = True
        if draft.get("date_found") and draft.get("due_date"):
            previous_date = draft["due_date"]

    model_items = []
    if os.environ.get("OPENAI_API_KEY", "").strip():
        try:
            model_items = _openai_compose_items(prompt, now_local)
        except CashAiProviderError:
            logger.info("agenda_ai_openai_fallback prompt_len=%s", len(prompt))

    if model_items and len(model_items) >= len(locals_):
        drafts = []
        for index, parsed in enumerate(model_items):
            base = locals_[index] if index < len(locals_) else locals_[-1]
            drafts.append(_merge_model_item(base, parsed))
    else:
        drafts = locals_

    items = [
        _enrich_draft(draft, organization_id, agent_id)
        for draft in drafts
    ]

    return {
        "items": items,
        "source_prompt": prompt,
    }


def refresh_item(draft):
    draft["date_found"] = bool(draft.get("due_date"))
    draft["time_found"] = bool(draft.get("due_time"))
    if draft.get("property_id"):
        draft["property_match"] = "single"
    if draft.get("contact_id"):
        draft["contact_match"] = "single"
    elif draft.get("contact_match") == "skipped":
        draft["contact_id"] = ""
    draft["item_status"] = item_status(draft)
    if draft["item_status"] == "ready":
        draft["ui_status"] = "ready"
    elif draft.get("property_match") == "ambiguous":
        draft["ui_status"] = "properties_ambiguous"
    elif not draft.get("date_found") or (
        draft.get("task_type") in TIME_REQUIRED_TYPES and not draft.get("time_found")
    ):
        draft["ui_status"] = "date_unclear"
    return draft


def compose_from_prompt(
    prompt,
    organization_id,
    agent_id,
    *,
    now=None,
):
    bundle = interpret_agenda_input(
        prompt,
        organization_id,
        agent_id,
        now=now,
    )
    return bundle["items"][0]


compose_from_whatsapp_text = compose_from_prompt


def compose_from_image(
    file_storage,
    organization_id,
    agent_id,
    *,
    extra_prompt="",
    now=None,
):
    try:
        validated = validate_receipt_upload(file_storage)
    except CashReceiptError as error:
        raise AgendaAiError(error.message_key) from error

    prepared, mime = prepare_image_for_ai(
        validated["bytes"],
        validated["content_type"],
    )

    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise AgendaAiError("agenda_ai_err_image_needs_provider")

    tz = organization_timezone(organization_id)
    now_local = (now or now_utc()).astimezone(tz)

    try:
        parsed = _openai_compose(
            extra_prompt or "Leé este chat de WhatsApp y extraé el evento.",
            now_local,
            image_bytes=prepared,
            image_content_type=mime,
        )
    except CashAiProviderError as error:
        raise AgendaAiError("agenda_ai_err_provider_failed") from error

    prompt = parsed.get("source_prompt") or extra_prompt or ""
    local = parse_agenda_prompt(
        prompt,
        today=now_local.date(),
        now_local=now_local,
    )
    draft = {**local, **{key: value for key, value in parsed.items() if value}}
    if parsed.get("due_date"):
        draft["date_found"] = True
    if parsed.get("due_time"):
        draft["time_found"] = True

    return _enrich_draft(draft, organization_id, agent_id)


def summarize_visit_outcome(text):
    note = (text or "").strip()

    if not note:
        raise AgendaAiError("agenda_ai_err_empty_outcome")

    parsed = parse_visit_outcome(note)

    if os.environ.get("OPENAI_API_KEY", "").strip():
        try:
            ai_fields = {
                key: value
                for key, value in _openai_outcome(note).items()
                if value not in (None, "", [])
            }
            parsed = {**parsed, **ai_fields}
        except CashAiProviderError:
            logger.info("agenda_ai_outcome_fallback")

    parsed["note"] = note

    return normalize_visit_outcome(parsed)


def _openai_item_schema():
    return {
        "task_type": "visit|call|meeting|follow_up|documentation|valuation|reminder|other",
        "due_date": "YYYY-MM-DD|null",
        "due_time": "HH:MM|null",
        "contact_name": "string|null",
        "property_query": "string|null",
        "description": "string|null",
        "duration_minutes": "number|null",
    }


def _normalized_model_item(parsed, prompt):
    return {
        "task_type": parsed.get("task_type") or "other",
        "due_date": parsed.get("due_date"),
        "due_time": parsed.get("due_time"),
        "contact_name": parsed.get("contact_name") or "",
        "property_query": parsed.get("property_query") or "",
        "description": parsed.get("description") or prompt,
        "duration_minutes": parsed.get("duration_minutes"),
        "source_prompt": parsed.get("source_prompt") or prompt,
        "title": parsed.get("title") or "",
    }


def _openai_compose_items(prompt, now_local):
    schema = {"items": [_openai_item_schema()]}
    instructions = (
        "Extract every distinct real-estate agenda action from the user text. "
        "A visit with two people is ONE item. A visit and a later call are TWO. "
        "Never invent a date, time or property id. "
        "If a later action says 'después' without a time, omit due_time. "
        "Return ONLY JSON matching this shape: "
        f"{json.dumps(schema)}. "
        f"Today is {now_local.date().isoformat()} "
        f"and the local time is {now_local.strftime('%H:%M')}. "
        "Prefer Argentine Spanish."
    )
    parsed = request_structured_json(
        instructions=instructions,
        user_content=[{"type": "text", "text": prompt}],
        log_prefix="agenda_ai_items",
    )
    raw_items = parsed.get("items") if isinstance(parsed, dict) else None

    if not raw_items:
        return []

    return [
        _normalized_model_item(item, prompt)
        for item in raw_items
        if isinstance(item, dict)
    ]


def _openai_compose(prompt, now_local, *, image_bytes=None, image_content_type=None):
    schema = {
        "title": "string",
        **_openai_item_schema(),
        "source_prompt": "string",
    }
    instructions = (
        "Extract one real-estate agenda event from the user text "
        "and optional WhatsApp screenshot. "
        "Return ONLY JSON matching this shape: "
        f"{json.dumps(schema)}. "
        f"Today is {now_local.date().isoformat()} "
        f"and the local time is {now_local.strftime('%H:%M')}. "
        "Never invent a property id. Prefer Argentine Spanish."
    )
    user_content = build_multimodal_user_content(
        user_context_text=prompt,
        image_bytes=image_bytes,
        image_content_type=image_content_type,
    )
    parsed = request_structured_json(
        instructions=instructions,
        user_content=user_content,
        image_bytes_len=len(image_bytes) if image_bytes else 0,
        image_content_type=image_content_type,
        log_prefix="agenda_ai",
    )

    return {
        "title": parsed.get("title") or "",
        "task_type": parsed.get("task_type") or "other",
        "due_date": parsed.get("due_date"),
        "due_time": parsed.get("due_time"),
        "contact_name": parsed.get("contact_name") or "",
        "property_query": parsed.get("property_query") or "",
        "description": parsed.get("description") or prompt,
        "duration_minutes": parsed.get("duration_minutes"),
        "source_prompt": parsed.get("source_prompt") or prompt,
    }


def _openai_outcome(note):
    schema = {
        "interest": "positive|neutral|negative|null",
        "objections": ["string"],
        "areas": ["string"],
        "budget": {
            "min": "number|null",
            "max": "number|null",
            "currency": "USD|ARS|null",
        },
        "preferences": ["string"],
        "next_action": "string|null",
        "suggested_task": {
            "type": "follow_up|call|visit|meeting|reminder|null",
            "prompt": "string|null",
        },
    }
    parsed = request_structured_json(
        instructions=(
            "Summarize a property visit outcome from the agent's words. "
            "Return ONLY JSON matching this schema and omit anything the "
            f"agent did not say: {json.dumps(schema)}."
        ),
        user_content=[{"type": "text", "text": note}],
        log_prefix="agenda_ai_outcome",
    )

    return normalize_visit_outcome(parsed)
