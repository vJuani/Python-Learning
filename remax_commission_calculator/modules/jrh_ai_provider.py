"""Intent provider abstraction. Routes never call OpenAI directly."""

from __future__ import annotations

import logging
import os
import re
import time
import unicodedata

from modules.jrh_ai_intents import (
    ALL_INTENTS,
    CREATE_TASK,
    FALLBACK,
    LOW_CONFIDENCE,
    QUERY_AGENDA,
    QUERY_AGENT_ACCOUNT,
    QUERY_INVOICES,
    QUERY_OPERATIONS,
    QUERY_PENDINGS,
    QUERY_PROPERTIES,
    QUERY_PROPERTY_NEEDS,
    START_AGENT_PAYMENT,
    START_INVOICE,
)

logger = logging.getLogger(__name__)


def _fold(text):
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).lower()


def get_jrh_ai_provider_name():
    return (
        os.environ.get("JRH_AI_PROVIDER", "mock").strip().lower() or "mock"
    )


def get_jrh_ai_model():
    return os.environ.get("JRH_AI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


class AIIntentProvider:
    def interpret(self, prompt, *, context=None, language="es"):
        raise NotImplementedError


def _person_from(text):
    match = re.search(
        r"\b(?:para|de|a)\s+([A-Za-zÁÉÍÓÚÑÜáéíóúñü]{3,}"
        r"(?:\s+[A-Za-zÁÉÍÓÚÑÜáéíóúñü]{2,})?)",
        text or "",
        re.IGNORECASE,
    )
    if not match:
        return ""
    name = match.group(1).strip()
    if _fold(name) in {
        "las", "los", "la", "el", "una", "un", "hoy", "manana", "mañana",
        "septiembre", "fee", "cuenta", "factura", "pago",
    }:
        return ""
    return name


def _price_cap(folded):
    match = re.search(r"hasta\s+(\d+(?:[.,]\d+)?)\s*(mil)?", folded)
    if not match:
        return None
    amount = float(match.group(1).replace(",", "."))
    if match.group(2):
        amount *= 1000
    return amount


def _rooms(folded):
    match = re.search(r"(\d)\s*ambientes?", folded)
    if match:
        return int(match.group(1))
    return None


def interpret_with_rules(prompt, *, context=None, language="es"):
    """Deterministic classifier used by mock and as OpenAI fallback."""
    text = (prompt or "").strip()
    folded = _fold(text)
    context = context or {}
    last = context.get("last_entity") or {}
    pronoun = bool(
        re.search(
            r"\b(lo|la|eso|esa|este|esta|el cargo|facturamelo|facturamel[oa])\b",
            folded,
        )
        or "facturamelo" in folded
        or "facturamelo" in _fold(text.replace("á", "a"))
    )

    entities = {}
    person = _person_from(text)
    if not person:
        after_debe = re.search(
            r"\bdebe\s+([A-Za-zÁÉÍÓÚÑÜáéíóúñü]{3,}"
            r"(?:\s+[A-Za-zÁÉÍÓÚÑÜáéíóúñü]{2,})?)",
            text or "",
            re.IGNORECASE,
        )
        if after_debe:
            person = after_debe.group(1).strip()
    if person:
        entities["agent_name"] = person
        entities["contact_name"] = person

    if "fee" in folded:
        entities["charge_hint"] = "fee"
    period = re.search(
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
        r"septiembre|octubre|noviembre|diciembre|\d{4})",
        folded,
    )
    if period:
        entities["period"] = period.group(1)

    if any(token in folded for token in ("usd", "dolar", "dólar")):
        entities["currency"] = "USD"
    elif "ars" in folded or "peso" in folded:
        entities["currency"] = "ARS"

    if "manana" in folded or "mañana" in folded:
        entities["when"] = "tomorrow"
    elif "hoy" in folded:
        entities["when"] = "today"

    neighborhood = None
    for zone in ("nunez", "nuñez", "palermo", "belgrano", "recoleta", "caballito"):
        if zone in folded:
            neighborhood = "Núñez" if zone in {"nunez", "nuñez"} else zone.title()
            break
    if neighborhood:
        entities["neighborhood"] = neighborhood

    rooms = _rooms(folded)
    if rooms:
        entities["rooms"] = rooms
    cap = _price_cap(folded)
    if cap:
        entities["max_price"] = cap
    if "departamento" in folded or "depto" in folded:
        entities["property_type"] = "apartment"

    address = re.search(
        r"(libertador|santa fe|cabildo|corrientes|madero)(?:\s+\d+)?",
        folded,
    )
    if address:
        entities["address"] = address.group(0)

    if pronoun and last.get("kind") and last.get("id"):
        entities["refers_to_previous"] = True
        entities["previous_kind"] = last.get("kind")
        entities["previous_id"] = last.get("id")

    intent = FALLBACK
    confidence = 0.4

    if any(
        hint in folded
        for hint in ("registrame este pago", "registrar pago", "registrar un pago")
    ):
        intent, confidence = START_AGENT_PAYMENT, 0.9
    elif any(
        hint in folded
        for hint in (
            "facturame",
            "facturá",
            "factura",
            "quiero facturar",
            "quiero hacer la factura",
            "haceme la factura",
        )
    ):
        intent, confidence = START_INVOICE, 0.88
        if "fee" in folded:
            confidence = 0.93
    elif any(
        hint in folded
        for hint in ("que debe", "qué debe", "saldo de", "cuenta de", "cuanto debe")
    ) or (folded.startswith("que debe") or folded.startswith("qué debe")):
        intent, confidence = QUERY_AGENT_ACCOUNT, 0.94
    elif "mi cuenta" in folded or "que debo" in folded or "qué debo" in folded:
        intent, confidence = QUERY_AGENT_ACCOUNT, 0.9
        entities["self"] = True
    elif any(
        hint in folded
        for hint in (
            "que tengo pendiente",
            "qué tengo pendiente",
            "pendientes",
            "tengo pendiente",
        )
    ):
        intent, confidence = QUERY_PENDINGS, 0.95
    elif any(
        hint in folded
        for hint in (
            "visitas tengo",
            "que tengo hoy",
            "qué tengo hoy",
            "que tengo manana",
            "qué tengo mañana",
            "agenda de hoy",
            "agenda de manana",
        )
    ):
        intent, confidence = QUERY_AGENDA, 0.92
    elif any(
        hint in folded
        for hint in ("que tengo para", "qué tengo para", "propiedades para")
    ):
        intent, confidence = QUERY_PROPERTY_NEEDS, 0.9
    elif any(
        hint in folded
        for hint in (
            "departamentos",
            "mostrame",
            "buscame",
            "propiedades en",
            "hasta ",
        )
    ) and (
        neighborhood
        or rooms
        or cap
        or "propiedad" in folded
        or "departamento" in folded
    ):
        intent, confidence = QUERY_PROPERTIES, 0.86
    elif any(
        hint in folded
        for hint in ("operacion de", "operación de", "buscame la operacion")
    ):
        intent, confidence = QUERY_OPERATIONS, 0.88
    elif "facturas pendientes" in folded or "facturas tengo" in folded:
        intent, confidence = QUERY_INVOICES, 0.9
    elif any(hint in folded for hint in ("agendame", "agendar", "recordame")):
        intent, confidence = CREATE_TASK, 0.86
        entities["title"] = text

    if intent == FALLBACK and pronoun and last.get("kind") == "charge":
        intent, confidence = START_INVOICE, 0.8

    return {
        "intent": intent if intent in ALL_INTENTS else FALLBACK,
        "entities": entities,
        "confidence": confidence,
        "provider": "rules",
    }


class MockAIIntentProvider(AIIntentProvider):
    def interpret(self, prompt, *, context=None, language="es"):
        parsed = interpret_with_rules(
            prompt, context=context, language=language
        )
        parsed["provider"] = "mock"
        parsed["model"] = "rules"
        return parsed


class OpenAIAIIntentProvider(AIIntentProvider):
    def interpret(self, prompt, *, context=None, language="es"):
        from modules.cash_ai_provider import request_structured_json

        allowed = ", ".join(ALL_INTENTS)
        instructions = (
            "You classify a real-estate CRM request. "
            f"Return JSON with keys intent, entities, confidence. "
            f"intent must be one of: {allowed}. "
            "entities may include agent_name, contact_name, address, "
            "neighborhood, property_type, rooms, max_price, currency, "
            "when, charge_hint, period, title. "
            "Never invent IDs. confidence is 0-1."
        )
        user_content = [{"type": "text", "text": prompt or ""}]
        try:
            parsed = request_structured_json(
                instructions=instructions,
                user_content=user_content,
                model=get_jrh_ai_model(),
                log_prefix="jrh_ai",
            )
        except Exception:
            logger.info("jrh_ai openai_fallback_to_rules")
            parsed = interpret_with_rules(
                prompt, context=context, language=language
            )
            parsed["provider"] = "openai_fallback"
            parsed["model"] = "rules"
            return parsed
        intent = parsed.get("intent") or FALLBACK
        if intent not in ALL_INTENTS:
            intent = FALLBACK
        try:
            confidence = float(parsed.get("confidence") or LOW_CONFIDENCE)
        except (TypeError, ValueError):
            confidence = LOW_CONFIDENCE
        entities = parsed.get("entities") if isinstance(parsed.get("entities"), dict) else {}
        return {
            "intent": intent,
            "entities": entities,
            "confidence": max(0.0, min(1.0, confidence)),
            "provider": "openai",
            "model": get_jrh_ai_model(),
        }


def get_intent_provider(name=None):
    chosen = (name or get_jrh_ai_provider_name()).strip().lower()
    if chosen in {"mock", "test", "rules"}:
        return MockAIIntentProvider()
    if chosen == "openai":
        return OpenAIAIIntentProvider()
    return MockAIIntentProvider()


def interpret_prompt(prompt, *, context=None, language="es", provider=None):
    started = time.perf_counter()
    adapter = provider or get_intent_provider()
    parsed = adapter.interpret(prompt, context=context, language=language)
    duration_ms = int((time.perf_counter() - started) * 1000)
    parsed["duration_ms"] = duration_ms
    logger.info(
        "jrh_ai stage=interpret intent=%s confidence=%s "
        "provider=%s model=%s duration_ms=%s",
        parsed.get("intent"),
        parsed.get("confidence"),
        parsed.get("provider"),
        parsed.get("model") or "",
        duration_ms,
    )
    return parsed
