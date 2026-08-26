"""
AI provider adapters for cash receipt extraction.

Configured via env; never hardcode secrets.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request


class CashAiProviderError(Exception):
    pass


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
        f"Unsupported CASH_AI_PROVIDER: {provider}"
    )


def _mock_extract(*, user_context_text, has_image):
    text = (user_context_text or "").lower()

    if "fail" in text:
        raise CashAiProviderError("mock_forced_failure")

    amount = 18500.0 if has_image else 32000.0
    payment = None

    if "efectivo" in text and "sin efectivo" not in text:
        payment = "cash"

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
        raise CashAiProviderError("missing_openai_api_key")

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
        b64 = base64.b64encode(image_bytes).decode("ascii")
        mime = image_content_type or "image/jpeg"
        user_content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{b64}",
                },
            }
        )

    payload = {
        "model": get_cash_ai_model(),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_content},
        ],
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")
        raise CashAiProviderError(
            f"openai_http_{error.code}:{detail[:200]}"
        ) from error
    except Exception as error:
        raise CashAiProviderError(
            "openai_request_failed"
        ) from error

    try:
        content = body["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise CashAiProviderError(
            "openai_invalid_response"
        ) from error
