"""
AI extraction of agent payment receipts (Phase 3A.2).

Reuses the cash AI provider abstraction: same env config
(``CASH_AI_PROVIDER`` / ``CASH_AI_MODEL`` / ``OPENAI_API_KEY``),
same transport and the same data-URL image contract. Only the
prompt and the output shape are specific to agent payments.

The model may describe who paid, but it never receives internal
ids: agent, treasury account and charge are resolved by the
backend from the extracted text.

Mock provider (``CASH_AI_PROVIDER=mock``) reads ``key=value``
pairs from the context text so tests can be explicit:

    "payer=Jose Luis Barreiro; amount=78,65; currency=USD"
"""

from __future__ import annotations

import json
import re

from modules.cash_ai_provider import (
    CashAiProviderError,
    build_multimodal_user_content,
    get_cash_ai_provider_name,
    request_structured_json,
)


LOG_PREFIX = "agent_payment_ai"

MOCK_KEYS = (
    "payer",
    "amount",
    "currency",
    "method",
    "date",
    "reference",
    "bank",
    "recipient",
    "description",
    "rate",
)


def extract_agent_payment_draft_from_provider(
    *,
    user_context_text,
    image_bytes=None,
    image_content_type=None,
    allowed_payment_methods,
    allowed_currencies,
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
            allowed_payment_methods=allowed_payment_methods,
            allowed_currencies=allowed_currencies,
            language=language,
        )

    raise CashAiProviderError(
        f"Unsupported CASH_AI_PROVIDER: {provider}",
        stage="provider_request_started",
    )


def _parse_mock_pairs(text):
    pairs = {}

    for chunk in (text or "").split(";"):
        if "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        key = key.strip().lower()
        if key in MOCK_KEYS:
            pairs[key] = value.strip()

    return pairs


def _mock_amount(raw):
    if raw is None:
        return None

    normalized = str(raw).strip().replace(".", "").replace(",", ".")

    try:
        return float(normalized)
    except ValueError:
        return None


def _mock_extract(*, user_context_text, has_image):
    text = (user_context_text or "").strip()

    if "fail" in text.lower():
        raise CashAiProviderError(
            "mock_forced_failure",
            stage="provider_request_started",
        )

    pairs = _parse_mock_pairs(text)
    lowered = text.lower()

    currency = (pairs.get("currency") or "").strip().upper() or None

    if currency is None:
        if "usd" in lowered or "dólar" in lowered or "dolar" in lowered:
            currency = "USD"
        elif "ars" in lowered or "peso" in lowered:
            currency = "ARS"

    amount = _mock_amount(pairs.get("amount"))

    if amount is None:
        match = re.search(
            r"(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:\.\d{1,2})?)",
            text,
        )
        if match:
            amount = _mock_amount(match.group(1))

    method = (pairs.get("method") or "").strip().lower() or None

    if method is None and "transferencia" in lowered:
        method = "transfer"
    elif method is None and "efectivo" in lowered:
        method = "cash"

    return {
        "amount": amount,
        "currency": currency,
        "payment_date": pairs.get("date"),
        "payment_method": method,
        "bank_name": pairs.get("bank"),
        "reference_number": pairs.get("reference"),
        "sender_name": pairs.get("payer"),
        "recipient_name": pairs.get("recipient"),
        "description": pairs.get("description")
        or ("Comprobante de pago" if has_image else None),
        "exchange_rate": _mock_amount(pairs.get("rate")),
        "confidence": "high" if pairs else "medium",
        "field_confidence": {
            key: "high"
            for key in pairs
        },
        "fields_needing_review": [],
    }


def _openai_extract(
    *,
    user_context_text,
    image_bytes,
    image_content_type,
    allowed_payment_methods,
    allowed_currencies,
    language,
):
    schema_hint = {
        "amount": "number|null",
        "currency": (
            "one of "
            + ",".join(allowed_currencies)
            + "|null"
        ),
        "payment_date": "YYYY-MM-DD|null",
        "payment_method": (
            "one of "
            + ",".join(allowed_payment_methods)
            + "|null"
        ),
        "bank_name": "string|null",
        "reference_number": "string|null",
        "sender_name": "string|null",
        "recipient_name": "string|null",
        "description": "string|null",
        "exchange_rate": "number|null",
        "confidence": "high|medium|low",
        "field_confidence": {
            "field_name": "high|medium|low",
        },
        "fields_needing_review": ["field_names"],
    }

    instructions = (
        "Extract a real estate agent payment receipt "
        "(transfer, deposit, wallet or cash) from the image "
        "and the optional user context. "
        "Return ONLY valid JSON matching this shape: "
        f"{json.dumps(schema_hint)}. "
        "Never invent values: if a field is not visible, use "
        "null and list it in fields_needing_review. "
        "Do not output agent ids, account ids or charge ids: "
        "only the names and numbers printed on the receipt. "
        "sender_name is who paid, recipient_name is who "
        "received. bank_name is the bank or digital wallet. "
        "reference_number is the operation/transaction number. "
        "Only set exchange_rate when it is explicitly printed. "
        "Argentine receipts use '.' as thousands separator and "
        "',' as decimal separator. "
        "Never assume the currency from an ambiguous '$': "
        "leave currency null and flag it for review unless the "
        "receipt clearly states USD, U$S, ARS or pesos. "
        f"Response language for description: {language}."
    )

    user_content = build_multimodal_user_content(
        user_context_text=user_context_text,
        image_bytes=image_bytes,
        image_content_type=image_content_type,
    )

    return request_structured_json(
        instructions=instructions,
        user_content=user_content,
        image_bytes_len=len(image_bytes) if image_bytes else 0,
        image_content_type=image_content_type,
        log_prefix=LOG_PREFIX,
    )
