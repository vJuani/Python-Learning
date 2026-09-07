"""Intent provider abstraction. Routes never call OpenAI directly."""

from __future__ import annotations

import logging
import os
import time

from modules.jrh_ai_classify import apply_intent_guards, classify_intent
from modules.jrh_ai_intents import ALL_INTENTS, FALLBACK, LOW_CONFIDENCE

logger = logging.getLogger(__name__)


def get_jrh_ai_provider_name():
    explicit = (os.environ.get("JRH_AI_PROVIDER") or "").strip().lower()
    if explicit:
        return explicit
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai"
    logger.warning(
        "jrh_ai provider=mock reason=unset_and_no_openai_key "
        "set JRH_AI_PROVIDER=openai in Railway for production"
    )
    return "mock"


def get_jrh_ai_model():
    return os.environ.get("JRH_AI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


class AIIntentProvider:
    def interpret(self, prompt, *, context=None, language="es"):
        raise NotImplementedError


def interpret_with_rules(prompt, *, context=None, language="es"):
    """Deterministic classifier used by mock and as OpenAI fallback."""
    parsed = classify_intent(prompt, context=context)
    parsed["provider"] = "rules"
    parsed["language"] = language
    return parsed


class MockAIIntentProvider(AIIntentProvider):
    def interpret(self, prompt, *, context=None, language="es"):
        parsed = interpret_with_rules(
            prompt, context=context, language=language
        )
        parsed["provider"] = "mock"
        parsed["model"] = "rules"
        return parsed


_OPENAI_INSTRUCTIONS = """
You classify a request for JRH One, a real-estate CRM.

Return JSON only:
{{
  "intent": one of [{allowed}],
  "entities": {{}},
  "confidence": 0.0-1.0
}}

INTENT SEMANTICS — do not mix these three:

QUERY_PROPERTIES = inventory / listing / search of properties.
Examples:
- mostrame que propiedades disponibles tengo en capital
- qué propiedades tengo en CABA
- mostrame lo disponible
- buscame deptos en Núñez
- hay casas en zona norte?
- departamentos hasta 250 mil
- qué alquileres tenemos en capital

QUERY_AGENDA = read existing calendar. Never create.
Examples:
- qué visitas tengo mañana
- qué tengo agendado hoy
- mostrame mi agenda
- tengo algo el jueves?

CREATE_TASK = explicit create reminder/event.
Requires a create verb or a clear schedule action.
Examples:
- agendame una visita mañana a las 18
- recordame llamar a Juan
- anotame reunión el jueves
- programá una visita

Do NOT classify as QUERY_AGENDA or CREATE_TASK just because the phrase
contains "mostrame", "tengo", or does not match another pattern.

If the user asks to see available properties, inventory, listings,
deptos, casas, alquileres, CABA/capital, or "lo disponible":
intent=QUERY_PROPERTIES.

If unsure: intent=FALLBACK with low confidence. Never guess Agenda.

entities may include:
location, neighborhood, jurisdiction, availability, operation_type,
property_type, rooms, bedrooms, bathrooms, min_price, max_price,
currency, min_area, parking, balcony, terrace, garden, when,
agent_name, contact_name, address, charge_hint, period, title.

availability=available only if they said disponible/publicado.
location "capital" / "CABA" / "capital federal" stay as location text.
Never invent IDs, agents, properties, or prices.
""".strip()


class OpenAIAIIntentProvider(AIIntentProvider):
    def interpret(self, prompt, *, context=None, language="es"):
        from modules.cash_ai_provider import request_structured_json

        instructions = _OPENAI_INSTRUCTIONS.format(
            allowed=", ".join(ALL_INTENTS)
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
            return apply_intent_guards(parsed, prompt)
        intent = parsed.get("intent") or FALLBACK
        if intent not in ALL_INTENTS:
            intent = FALLBACK
        try:
            confidence = float(parsed.get("confidence") or LOW_CONFIDENCE)
        except (TypeError, ValueError):
            confidence = LOW_CONFIDENCE
        entities = parsed.get("entities") if isinstance(parsed.get("entities"), dict) else {}
        guarded = apply_intent_guards(
            {
                "intent": intent,
                "entities": entities,
                "confidence": max(0.0, min(1.0, confidence)),
                "provider": "openai",
                "model": get_jrh_ai_model(),
            },
            prompt,
        )
        return guarded


def get_intent_provider(name=None):
    chosen = (name or get_jrh_ai_provider_name()).strip().lower()
    if chosen in {"mock", "test", "rules"}:
        return MockAIIntentProvider()
    if chosen == "openai":
        return OpenAIAIIntentProvider()
    logger.warning("jrh_ai unknown_provider=%s falling_back=mock", chosen)
    return MockAIIntentProvider()


def interpret_prompt(prompt, *, context=None, language="es", provider=None):
    started = time.perf_counter()
    adapter = provider or get_intent_provider()
    parsed = adapter.interpret(prompt, context=context, language=language)
    parsed = apply_intent_guards(parsed, prompt)
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
