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
)
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


def _draft_ui_status(draft, match):
    if match["status"] == "ambiguous":
        return "properties_ambiguous"
    if not draft.get("date_found") or not draft.get("time_found"):
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
    draft["warnings"] = _draft_warnings(draft, match)
    draft["ui_status"] = _draft_ui_status(draft, match)

    return _apply_structured_title(draft)


def compose_from_prompt(
    prompt,
    organization_id,
    agent_id,
    *,
    now=None,
):
    prompt = (prompt or "").strip()

    if not prompt:
        raise AgendaAiError("agenda_ai_err_empty_prompt")

    tz = organization_timezone(organization_id)
    now_local = (now or now_utc()).astimezone(tz)
    draft = parse_agenda_prompt(
        prompt,
        today=now_local.date(),
        now_local=now_local,
    )

    if os.environ.get("OPENAI_API_KEY", "").strip():
        try:
            parsed = _openai_compose(prompt, now_local)
            draft = {**draft, **{key: value for key, value in parsed.items() if value}}
            if parsed.get("due_date"):
                draft["date_found"] = True
            if parsed.get("due_time"):
                draft["time_found"] = True
        except CashAiProviderError:
            logger.info("agenda_ai_openai_fallback prompt_len=%s", len(prompt))

    return _enrich_draft(draft, organization_id, agent_id)


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
            parsed = {**parsed, **_openai_outcome(note)}
        except CashAiProviderError:
            logger.info("agenda_ai_outcome_fallback")

    parsed["note"] = note

    return parsed


def _openai_compose(prompt, now_local, *, image_bytes=None, image_content_type=None):
    schema = {
        "title": "string",
        "task_type": "visit|call|meeting|follow_up|documentation|valuation|reminder|other",
        "due_date": "YYYY-MM-DD",
        "due_time": "HH:MM",
        "contact_name": "string|null",
        "property_query": "string|null",
        "description": "string|null",
        "duration_minutes": "number|null",
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
        "interest": "positive|neutral|negative",
        "objection": "string|null",
        "area": "string|null",
        "budget": "string|null",
        "next_action": "string|null",
    }
    parsed = request_structured_json(
        instructions=(
            "Summarize a property visit outcome. Return ONLY JSON: "
            f"{json.dumps(schema)}."
        ),
        user_content=[{"type": "text", "text": note}],
        log_prefix="agenda_ai_outcome",
    )

    return {
        key: parsed.get(key) or ""
        for key in ("interest", "objection", "area", "budget", "next_action")
    }
