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

from modules.agenda_nlp import parse_agenda_prompt, parse_visit_outcome
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


def match_property(organization_id, agent_id, query):
    query = (query or "").strip()

    if not query:
        return None

    needle = _fold(query)
    best = None
    best_score = 0

    for record in get_properties(organization_id, agent_id=agent_id):
        address = _fold(record.get("address") or "")

        if not address:
            continue

        if needle in address or address in needle:
            return record

        overlap = sum(
            1
            for token in needle.split()
            if len(token) > 3 and token in address
        )

        if overlap > best_score:
            best = record
            best_score = overlap

    return best if best_score else None


def _enrich_draft(draft, organization_id, agent_id):
    matched = match_property(
        organization_id,
        agent_id,
        draft.get("property_query") or draft.get("property_address"),
    )
    draft["property_id"] = matched["id"] if matched else ""
    draft["property_address"] = (
        matched["address"] if matched else draft.get("property_query") or ""
    )

    return draft


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
            draft = {**draft, **_openai_compose(prompt, now_local)}
        except CashAiProviderError:
            logger.info("agenda_ai_openai_fallback prompt_len=%s", len(prompt))

    return _enrich_draft(draft, organization_id, agent_id)


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
    draft = {**local, **{k: v for k, v in parsed.items() if v}}

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
