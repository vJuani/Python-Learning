"""
AI provider adapters for cash receipt extraction.

Configured via env; never hardcode secrets.
Images are sent as data-URLs (base64), never as local filesystem paths.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request


logger = logging.getLogger(__name__)


class CashAiProviderError(Exception):
    def __init__(self, message, *, stage=None, details=None):
        super().__init__(message)
        self.stage = stage or "provider"
        self.details = details or {}


def get_cash_ai_provider_name():
    return (
        os.environ.get("CASH_AI_PROVIDER", "openai")
        .strip()
        .lower()
        or "openai"
    )


def get_cash_ai_model():
    return (
        os.environ.get("CASH_AI_MODEL", "gpt-4o-mini")
        .strip()
        or "gpt-4o-mini"
    )


def get_cash_ai_config_status():
    """Safe diagnostics for ops (no secrets)."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    provider_raw = os.environ.get("CASH_AI_PROVIDER")
    model_raw = os.environ.get("CASH_AI_MODEL")

    return {
        "provider_configured": bool(
            (provider_raw or "").strip()
        ),
        "provider": get_cash_ai_provider_name(),
        "model_configured": bool((model_raw or "").strip()),
        "model": get_cash_ai_model(),
        "openai_api_key_present": bool(key),
        "openai_api_key_length": len(key) if key else 0,
    }


def build_multimodal_user_content(
    *,
    user_context_text,
    image_bytes=None,
    image_content_type=None,
):
    """
    Build OpenAI chat user content parts.
    Image must be embedded as data URL — never a local path/URL.
    """
    user_content = [
        {
            "type": "text",
            "text": (
                "User context:\n"
                + ((user_context_text or "").strip() or "(none)")
            ),
        }
    ]

    if image_bytes:
        if isinstance(image_bytes, str):
            # Guard against accidentally passing a filesystem path.
            if (
                image_bytes.startswith("/")
                or image_bytes.startswith("\\")
                or ":\\" in image_bytes
                or image_bytes.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".webp")
                )
            ):
                raise CashAiProviderError(
                    "image_path_not_allowed",
                    stage="provider_request_started",
                    details={
                        "hint": (
                            "Refusing to send a filesystem "
                            "path to OpenAI"
                        ),
                    },
                )

        b64 = base64.b64encode(image_bytes).decode("ascii")
        mime = image_content_type or "image/jpeg"

        if not mime.startswith("image/"):
            raise CashAiProviderError(
                "invalid_image_mime",
                stage="provider_request_started",
                details={"mime": mime},
            )

        user_content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{b64}",
                },
            }
        )

    return user_content


def extract_cash_draft_from_provider(
    *,
    user_context_text,
    image_bytes=None,
    image_content_type=None,
    allowed_categories,
    allowed_payment_methods,
    language="es",
):
    provider = get_cash_ai_provider_name()

    if provider in ("mock", "test"):
        return _mock_extract(
            user_context_text=user_context_text,
            has_image=image_bytes is not None,
        )

    if provider == "openai":
        return _openai_extract(
            user_context_text=user_context_text,
            image_bytes=image_bytes,
            image_content_type=image_content_type,
            allowed_categories=allowed_categories,
            allowed_payment_methods=allowed_payment_methods,
            language=language,
        )

    raise CashAiProviderError(
        f"Unsupported CASH_AI_PROVIDER: {provider}",
        stage="provider_request_started",
    )


def _mock_extract(*, user_context_text, has_image):
    text = (user_context_text or "").lower()

    if "fail" in text:
        raise CashAiProviderError(
            "mock_forced_failure",
            stage="provider_request_started",
        )

    amount = 18500.0 if has_image else 32000.0
    payment = None

    if "efectivo" in text and "sin efectivo" not in text:
        payment = "cash"
    elif "transferencia" in text or "transfer" in text:
        payment = "transfer"

    category = "cleaning" if "limpieza" in text else (
        "office_supplies" if has_image or "librer" in text else None
    )
    currency = "ARS"
    review = []

    if payment is None:
        review.append("payment_method")
        payment = "undetermined"

    if category is None:
        review.append("category")
        category = "other_expense"

    if "usd" in text or "dólar" in text or "dolar" in text:
        currency = "USD"
    elif "$" in (user_context_text or "") and "ars" not in text:
        review.append("currency")

    return {
        "movement_type": "expense",
        "currency": currency,
        "amount": amount,
        "movement_date": None if "sin fecha" in text else "2026-08-26",
        "category": category,
        "description": (
            "Compra de artículos de librería"
            if has_image and "limpieza" not in text
            else "Compra de productos de limpieza"
        ),
        "merchant": "Librería XYZ" if has_image else None,
        "payment_method": payment,
        "receipt_number": "A-001" if has_image else None,
        "notes": (user_context_text or "").strip() or None,
        "confidence": "medium" if review else "high",
        "fields_needing_review": review,
    }


def _sanitize_openai_error_body(raw_text):
    text = (raw_text or "")[:500]
    # Avoid echoing accidental key fragments.
    return text.replace("sk-", "sk-***")


def _openai_extract(
    *,
    user_context_text,
    image_bytes,
    image_content_type,
    allowed_categories,
    allowed_payment_methods,
    language,
):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if not api_key:
        raise CashAiProviderError(
            "missing_openai_api_key",
            stage="provider_request_started",
            details={"openai_api_key_present": False},
        )

    schema_hint = {
        "movement_type": "income|expense|null",
        "currency": "ARS|USD|null",
        "amount": "number|null",
        "movement_date": "YYYY-MM-DD|null",
        "category": (
            "one of "
            + ",".join(allowed_categories)
            + "|null"
        ),
        "description": "string|null",
        "merchant": "string|null",
        "payment_method": (
            "one of "
            + ",".join(allowed_payment_methods)
            + "|null"
        ),
        "receipt_number": "string|null",
        "notes": "string|null",
        "confidence": "high|medium|low",
        "fields_needing_review": ["field_names"],
    }

    instructions = (
        "Extract an office cash movement draft from the "
        "optional receipt image and user context. "
        "Return ONLY valid JSON matching this shape: "
        f"{json.dumps(schema_hint)}. "
        "Never invent values. If unknown, use null and "
        "include the field in fields_needing_review. "
        "Prefer Argentine Spanish office context. "
        "Do not assume payment_method=cash. "
        "Do not assume USD for $. "
        "User context has semantic priority for category "
        "and payment method when stated. "
        f"Response language for description/notes: {language}."
    )

    user_content = build_multimodal_user_content(
        user_context_text=user_context_text,
        image_bytes=image_bytes,
        image_content_type=image_content_type,
    )

    has_image_part = any(
        part.get("type") == "image_url"
        for part in user_content
    )
    image_url_prefix = None

    if has_image_part:
        url = user_content[1]["image_url"]["url"]
        image_url_prefix = url[:48]

    model = get_cash_ai_model()
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_content},
        ],
    }
    request_bytes = json.dumps(payload).encode("utf-8")

    logger.info(
        "cash_ai stage=provider_request_started "
        "provider=openai model=%s has_image=%s "
        "image_bytes=%s image_mime=%s "
        "payload_bytes=%s data_url_prefix=%s",
        model,
        has_image_part,
        len(image_bytes) if image_bytes else 0,
        image_content_type,
        len(request_bytes),
        image_url_prefix,
    )

    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=request_bytes,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw_body = response.read().decode("utf-8")
            request_id = response.headers.get(
                "x-request-id"
            ) or response.headers.get("X-Request-Id")
    except urllib.error.HTTPError as error:
        detail = _sanitize_openai_error_body(
            error.read().decode("utf-8", errors="ignore")
        )
        request_id = error.headers.get("x-request-id") if error.headers else None
        logger.error(
            "cash_ai stage=provider_request_failed "
            "http_status=%s request_id=%s detail=%s",
            error.code,
            request_id,
            detail,
        )
        raise CashAiProviderError(
            f"openai_http_{error.code}",
            stage="provider_request_failed",
            details={
                "http_status": error.code,
                "request_id": request_id,
                "detail": detail,
            },
        ) from error
    except Exception as error:
        logger.error(
            "cash_ai stage=provider_request_failed "
            "error_type=%s error=%s",
            type(error).__name__,
            str(error)[:200],
        )
        raise CashAiProviderError(
            "openai_request_failed",
            stage="provider_request_failed",
            details={
                "error_type": type(error).__name__,
                "error": str(error)[:200],
            },
        ) from error

    logger.info(
        "cash_ai stage=provider_response_received "
        "request_id=%s body_bytes=%s",
        request_id,
        len(raw_body),
    )

    try:
        body = json.loads(raw_body)
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        logger.error(
            "cash_ai stage=provider_response_parsed_failed "
            "error_type=%s request_id=%s body_prefix=%s",
            type(error).__name__,
            request_id,
            raw_body[:200],
        )
        raise CashAiProviderError(
            "openai_invalid_response",
            stage="provider_response_parsed",
            details={
                "error_type": type(error).__name__,
                "request_id": request_id,
            },
        ) from error

    logger.info(
        "cash_ai stage=provider_response_parsed "
        "request_id=%s keys=%s",
        request_id,
        sorted(parsed.keys()) if isinstance(parsed, dict) else None,
    )

    return parsed
