"""Marketing IA creative layer. Independent from Cash AI. Routes never call OpenAI."""

from __future__ import annotations

import logging
import os
import time

from modules.marketing_ai_client import (
    MarketingAIClientError,
    get_brief_temperature,
    get_copy_temperature,
    get_marketing_ai_model,
    request_marketing_json,
)
from modules.marketing_ai_prompts import (
    BRIEF_SCHEMA,
    MAX_TOKENS,
    PROMPT_VERSIONS,
    VARIANT_ANGLES,
    build_brief_instructions,
    build_copy_instructions,
    format_schema,
    format_schema_name,
)
from modules.marketing_ai_quality import QualityError, validate_brief, validate_format_output
from modules.i18n import translate


logger = logging.getLogger(__name__)

MOCK_PROVIDER = "mock"
MOCK_MODEL = "mock-local"
OPENAI_PROVIDER = "openai"
QUALITY_RETRIES = 1

PRIVATE_KEYS = frozenset(
    {
        "commission",
        "clients",
        "clients_data",
        "clientsdata",
        "notes",
        "private_notes",
        "privatenotes",
        "invoice",
        "fiscal",
        "internal",
        "clients",
        "property_id",
        "organization_id",
        "agent_id",
        "created_by_user_id",
        "user_id",
        "external_id",
        "documents",
        "logo_path",
        "logo_url",
        "office_logo",
        "organization_logo",
        "photo_path",
        "profile_photo_key",
        "sync_hash",
        "sync_error",
    }
)


class MarketingAIError(Exception):
    def __init__(self, code, *, details=None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


def public_error_message(error, language="es"):
    code = getattr(error, "code", None) or type(error).__name__
    if code in {"missing_openai_api_key"}:
        key = "marketing_ia_err_not_configured"
    elif code in {"openai_invalid_response", "quality_failed"}:
        key = "marketing_ia_err_quality"
    else:
        key = "marketing_ia_err_provider"
    return translate(key, language=language)


def get_marketing_ai_provider_name():
    explicit = (os.environ.get("MARKETING_AI_PROVIDER") or "").strip().lower()
    if explicit:
        return explicit
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return OPENAI_PROVIDER
    logger.warning(
        "marketing_ai provider=mock reason=unset_and_no_openai_key "
        "set MARKETING_AI_PROVIDER=openai in production"
    )
    return MOCK_PROVIDER


def _debug_enabled():
    return (os.environ.get("MARKETING_AI_DEBUG") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _compact(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in PRIVATE_KEYS or str(key).lower() in PRIVATE_KEYS:
                continue
            packed = _compact(item)
            if packed in (None, "", [], {}):
                continue
            out[key] = packed
        return out
    if isinstance(value, list):
        return [item for item in (_compact(item) for item in value) if item not in (None, "", [], {})]
    return value


def build_model_payload(context):
    context = context or {}
    return _compact(
        {
            "language": context.get("language") or "es",
            "content_type": context.get("content_type"),
            "origin": context.get("origin"),
            "objective": context.get("objective"),
            "style": context.get("style"),
            "tone": context.get("tone"),
            "format": context.get("format"),
            "user_notes": (context.get("prompt_input") or "")[:400] or None,
            "property": context.get("property_facts") or {},
            "agent": context.get("agent_facts") or {},
            "brand": context.get("brand_facts") or {},
        }
    )


def _preview_text(content_type, parsed):
    if content_type == "post":
        return parsed.get("caption") or parsed.get("headline")
    if content_type == "story":
        frames = parsed.get("frames") or []
        first = frames[0] if frames else {}
        return first.get("headline") or first.get("body")
    if content_type == "carousel":
        return parsed.get("hook") or parsed.get("final_cta")
    if content_type == "whatsapp":
        return parsed.get("message")
    return parsed.get("body") or parsed.get("headline")


def _usage_sum(*chunks):
    input_tokens = 0
    output_tokens = 0
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        input_tokens += int(chunk.get("input_tokens") or 0)
        output_tokens += int(chunk.get("output_tokens") or 0)
    return input_tokens, output_tokens


def _result(*, content_type, parsed, provider, model_name, input_tokens, output_tokens, duration_ms, extra=None):
    payload = dict(parsed)
    payload["provider"] = provider
    payload["model_name"] = model_name
    payload["input_tokens"] = input_tokens
    payload["output_tokens"] = output_tokens
    payload["estimated_cost"] = 0
    payload["generated_copy"] = _preview_text(content_type, parsed)
    payload["prompt_version"] = PROMPT_VERSIONS[content_type]
    payload["debug"] = {
        "prompt_kind": content_type,
        "prompt_version": PROMPT_VERSIONS[content_type],
        "brief_version": PROMPT_VERSIONS["brief"],
        "model": model_name,
        "provider": provider,
        "duration_ms": duration_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if extra:
        payload["debug"].update(extra)
    return payload


class MockMarketingProvider:
    name = MOCK_PROVIDER
    model_name = MOCK_MODEL

    def generate(self, content_type, payload):
        style = (payload or {}).get("style") or "modern"
        neighborhood = ((payload or {}).get("property") or {}).get("neighborhood") or "la zona"
        if content_type == "post":
            parsed = {
                "headline": f"Departamento en {neighborhood}",
                "caption": (
                    f"Copy de prueba. {neighborhood} con los datos reales de la ficha. "
                    "Pedí una visita si querés conocerlo."
                ),
                "cta": "Pedí una visita",
                "hashtags": [f"#{str(neighborhood).replace(' ', '')}", "#Venta"],
            }
        elif content_type == "story":
            parsed = {
                "frames": [
                    {"headline": "Copy de prueba", "body": f"Hook en {neighborhood}.", "cta": ""},
                    {"headline": "Los datos", "body": "Ambientes y metros de la ficha.", "cta": ""},
                    {"headline": "Siguiente paso", "body": "Coordinamos la visita.", "cta": "Escribime"},
                ]
            }
        elif content_type == "carousel":
            parsed = {
                "hook": f"Copy de prueba en {neighborhood}",
                "slides": [
                    {"slide": 1, "title": neighborhood, "body": "Ubicación real de la ficha."},
                    {"slide": 2, "title": "Espacios", "body": "Ambientes y metros que sí están cargados."},
                    {"slide": 3, "title": "Visita", "body": "Escribime y coordinamos un horario."},
                ],
                "final_cta": "Pedí una visita",
            }
        elif content_type == "whatsapp":
            parsed = {
                "message": (
                    f"Hola, te paso este departamento en {neighborhood}. "
                    "Si te sirve, coordinamos una visita."
                ),
                "cta": "¿Te armo un horario?",
            }
        else:
            parsed = {
                "headline": f"Copy de prueba · {style}",
                "body": "Copy de prueba armado con los datos reales disponibles.",
                "cta": "Consultame",
            }
        return validate_format_output(parsed, content_type)


class OpenAIMarketingProvider:
    name = OPENAI_PROVIDER

    def generate(self, content_type, payload):
        language = (payload or {}).get("language") or "es"
        style = (payload or {}).get("style")
        tone = (payload or {}).get("tone")
        try:
            brief_raw, brief_usage = request_marketing_json(
                instructions=build_brief_instructions(language),
                user_payload=payload,
                schema=BRIEF_SCHEMA,
                schema_name=PROMPT_VERSIONS["brief"],
                temperature=get_brief_temperature(),
                max_tokens=MAX_TOKENS["brief"],
            )
            brief = validate_brief(brief_raw)
            copy_payload = {"brief": brief, "source": payload}
            last_error = None
            for attempt in range(QUALITY_RETRIES + 1):
                try:
                    copy_raw, copy_usage = request_marketing_json(
                        instructions=build_copy_instructions(
                            content_type, style=style, tone=tone, language=language
                        ),
                        user_payload=copy_payload,
                        schema=format_schema(content_type),
                        schema_name=format_schema_name(content_type),
                        temperature=get_copy_temperature(),
                        max_tokens=MAX_TOKENS[content_type],
                    )
                    parsed = validate_format_output(copy_raw, content_type)
                    parsed["_usage"] = _usage_sum(brief_usage, copy_usage)
                    if _debug_enabled():
                        parsed["_brief"] = brief
                    return parsed
                except QualityError as error:
                    last_error = error
                    logger.info(
                        "marketing_ai quality_retry type=%s attempt=%s reason=%s",
                        content_type,
                        attempt + 1,
                        error.reason,
                    )
            raise MarketingAIError(
                "quality_failed",
                details={"reason": getattr(last_error, "reason", "quality")},
            )
        except MarketingAIClientError as error:
            raise MarketingAIError(error.code, details=error.details) from error
        except QualityError as error:
            raise MarketingAIError("openai_invalid_response", details={"reason": error.reason}) from error


def resolve_provider(provider=None):
    if provider is not None:
        return provider
    name = get_marketing_ai_provider_name()
    if name == OPENAI_PROVIDER:
        return OpenAIMarketingProvider()
    return MockMarketingProvider()


def generate_content(content_type, context, provider=None):
    if content_type not in PROMPT_VERSIONS or content_type == "brief":
        raise MarketingAIError("unknown_content_type")
    payload = build_model_payload(context)
    engine = resolve_provider(provider)
    started = time.monotonic()
    parsed = engine.generate(content_type, payload)
    duration_ms = int((time.monotonic() - started) * 1000)
    usage = parsed.pop("_usage", {}) if isinstance(parsed, dict) else {}
    brief = parsed.pop("_brief", None) if isinstance(parsed, dict) else None
    extra = {}
    if brief is not None:
        extra["brief"] = brief
    extra["style"] = payload.get("style")
    extra["tone"] = payload.get("tone")
    return _result(
        content_type=content_type,
        parsed=parsed,
        provider=getattr(engine, "name", MOCK_PROVIDER),
        model_name=get_marketing_ai_model() if getattr(engine, "name", None) == OPENAI_PROVIDER else getattr(engine, "model_name", MOCK_MODEL),
        input_tokens=usage[0] if isinstance(usage, tuple) else usage.get("input_tokens") or 0,
        output_tokens=usage[1] if isinstance(usage, tuple) else usage.get("output_tokens") or 0,
        duration_ms=duration_ms,
        extra=extra,
    )


def generate_variants(content_type, context, provider=None):
    """Prepared hook for direct / aspirational / premium angles. Not used by routes yet."""
    results = []
    for angle in VARIANT_ANGLES:
        angled = dict(context or {})
        angled["tone"] = {
            "direct": "commercial",
            "aspirational": "aspirational",
            "premium": "exclusive",
        }.get(angle, angled.get("tone"))
        if angle == "premium":
            angled["style"] = "premium"
        item = generate_content(content_type, angled, provider=provider)
        item["variant_angle"] = angle
        results.append(item)
    return results


def validate_generation_output(parsed, content_type="copy"):
    try:
        return validate_format_output(parsed, content_type)
    except QualityError as error:
        raise MarketingAIError("openai_invalid_response", details={"reason": error.reason}) from error
