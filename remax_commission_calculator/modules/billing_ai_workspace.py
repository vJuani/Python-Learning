"""
Billing AI workspace view-model (Facturador IA mockup).
"""

from __future__ import annotations

from modules.i18n import translate


SUGGESTION_KEYS = (
    "billing_ai_suggestion_rent",
    "billing_ai_suggestion_sale",
    "billing_ai_suggestion_buyer",
)


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


def build_billing_ai_workspace(
  *,
    language="es",
    chat_messages=None,
    workspace=None,
    ai_message=None,
    ai_options=None,
    ai_operation=None,
):
    language = language if language in ("es", "en") else "es"
    chat_messages = chat_messages or []

    if not chat_messages:
        chat_messages = [
            {
                "role": "assistant",
                "text": _t("billing_ai_welcome", language),
            }
        ]

    suggestions = [
        {"key": key, "label": _t(key, language)}
        for key in SUGGESTION_KEYS
    ]

    extracted_fields = []
    preview = None
    operation_id = None
    side = None

    if workspace:
        preview = workspace.get("preview")
        operation_id = workspace.get("operation_id")
        side = workspace.get("side")
        extracted_fields = workspace.get("extracted_fields") or []

    if ai_message:
        chat_messages = list(chat_messages)
        chat_messages.append(
            {
                "role": "assistant",
                "text": _t(ai_message, language),
                "text_key": ai_message,
            }
        )

    return {
        "chat_messages": chat_messages,
        "suggestions": suggestions,
        "preview": preview,
        "extracted_fields": extracted_fields,
        "operation_id": operation_id,
        "side": side,
        "ai_options": ai_options or [],
        "ai_operation": ai_operation,
        "has_preview": preview is not None,
    }


def preview_to_workspace(preview, *, operation_id, side, language="es"):
    language = language if language in ("es", "en") else "es"

    fields = [
        {
            "key": "client",
            "label": _t("billing_ai_field_client", language),
            "value": preview.get("recipient_name") or "—",
        },
        {
            "key": "tax_id",
            "label": _t("billing_ai_field_tax_id", language),
            "value": preview.get("recipient_tax_id") or "—",
        },
        {
            "key": "operation",
            "label": _t("billing_ai_field_operation", language),
            "value": preview.get("operation_display_id") or "—",
        },
        {
            "key": "issuer",
            "label": _t("billing_ai_field_issuer", language),
            "value": preview.get("issuer_name") or "—",
        },
        {
            "key": "concept",
            "label": _t("billing_ai_field_concept", language),
            "value": preview.get("description") or "—",
        },
        {
            "key": "voucher",
            "label": _t("billing_invoice", language),
            "value": preview.get("voucher_label") or "—",
        },
        {
            "key": "service_type",
            "label": _t("billing_ai_field_type", language),
            "value": _t("billing_ai_service_services", language),
        },
        {
            "key": "condition",
            "label": _t("billing_ai_field_condition", language),
            "value": preview.get("payment_condition") or "—",
        },
        {
            "key": "currency",
            "label": _t("billing_ai_field_currency", language),
            "value": preview.get("currency") or "ARS",
        },
        {
            "key": "exchange_rate",
            "label": _t("billing_ai_field_exchange_rate", language),
            "value": preview.get("exchange_rate") or "—",
        },
        {
            "key": "point_of_sale",
            "label": _t("billing_ai_field_point_of_sale", language),
            "value": preview.get("point_of_sale") or "—",
        },
        {
            "key": "net_amount",
            "label": _t("billing_ai_field_net_amount", language),
            "value": preview.get("net_amount_display") or "—",
        },
        {
            "key": "vat",
            "label": _t("billing_ai_field_vat", language),
            "value": preview.get("vat_amount_display") or "—",
        },
        {
            "key": "total",
            "label": _t("billing_ai_field_total", language),
            "value": preview.get("total_amount_display") or "—",
        },
    ]

    return {
        "operation_id": operation_id,
        "side": side,
        "preview": preview,
        "extracted_fields": fields,
    }


def format_preview_amounts(preview, language="es"):
    if preview is None:
        return None

    currency = preview.get("currency") or "ARS"
    net = preview.get("subtotal")
    vat = preview.get("vat_amount")
    total = preview.get("total_amount")

    def _money(value):
        if value is None:
            return "—"
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return "—"
        symbol = "$" if currency in ("ARS", "USD") else ""
        return f"{symbol} {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    enriched = dict(preview)
    enriched["net_amount_display"] = _money(net)
    enriched["vat_amount_display"] = _money(vat)
    enriched["total_amount_display"] = _money(total)
    enriched["currency"] = currency
    enriched["draft_label"] = translate("billing_ai_draft_badge", language=language)
    from modules.arca.voucher_mapping import resolve_voucher_type

    voucher = resolve_voucher_type(
        issuer_tax_condition=preview.get("issuer_tax_condition") or "",
        recipient_tax_condition=preview.get("recipient_tax_condition") or "",
    )
    voucher_keys = {1: "billing_voucher_a", 6: "billing_voucher_b", 11: "billing_voucher_c"}
    key = voucher_keys.get(voucher)
    enriched["voucher_label"] = (
        translate(key, language=language) if key else ""
    )
    enriched["voucher_undetermined"] = key is None
    enriched["exchange_rate"] = preview.get("exchange_rate")
    return enriched
