"""
Agent payment AI orchestration (Phase 3A.2).

Pipeline: receipt upload -> AI extraction -> backend resolution
-> human review -> confirmation through the manual payment
service (``agent_account.create_movement``).

This module never writes to the ledger, to allocations or to
cash: it only fills a draft. All money movement happens inside
the existing atomic payment service.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from modules.agent_account import AgentAccountError, create_movement
from modules.agent_account_presentation import PAYMENT_METHODS
from modules.agent_payment_ai_provider import (
    extract_agent_payment_draft_from_provider,
)
from modules.agent_payment_ai_resolution import (
    APPLY_MODE_CHARGE,
    APPLY_MODE_ON_ACCOUNT,
    build_resolution,
)
from modules.cash_ai_provider import (
    CashAiProviderError,
    get_cash_ai_provider_name,
)
from modules.cash_receipts import (
    SCOPE_AGENT_PAYMENTS,
    CashReceiptError,
    absolute_receipt_path,
    prepare_image_for_ai,
    save_receipt_bytes,
    validate_receipt_upload,
)
from modules.database import get_agent_record
from modules.database.agent_account_repository import (
    CURRENCIES,
    get_agent_account_movement,
    get_pending_charge_for_payment,
)
from modules.database.agent_payment_ai_drafts_repository import (
    STATUS_CONFIRMED,
    STATUS_DISCARDED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_REVIEW,
    create_agent_payment_ai_draft,
    get_agent_payment_ai_draft,
    update_agent_payment_ai_draft,
)
from modules.database.tenant import require_organization_id
from modules.database.treasury_accounts_repository import (
    get_treasury_account,
)


logger = logging.getLogger(__name__)

PAYMENT_UNDETERMINED = "undetermined"

AI_PAYMENT_METHODS = PAYMENT_METHODS + (PAYMENT_UNDETERMINED,)

PAYMENT_METHOD_ALIASES = {
    "transfer": "transfer",
    "transferencia": "transfer",
    "transferencia bancaria": "transfer",
    "bank_transfer": "transfer",
    "deposito": "transfer",
    "depósito": "transfer",
    "deposit": "transfer",
    "cash": "cash",
    "efectivo": "cash",
    "card": "card",
    "tarjeta": "card",
    "debit": "card",
    "debito": "card",
    "débito": "card",
    "credit": "card",
    "credito": "card",
    "crédito": "card",
    "other": "other",
    "otro": "other",
}

# Digital wallets settle like a transfer for the ledger, but the
# reviewer must confirm the destination account.
PAYMENT_METHOD_NEEDS_REVIEW = {
    "wallet": "transfer",
    "billetera": "transfer",
    "mercado pago": "transfer",
    "mercadopago": "transfer",
    "mercado_pago": "transfer",
    "mp": "transfer",
    "uala": "transfer",
    "ualá": "transfer",
    "brubank": "transfer",
}

DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
)


class AgentPaymentAiError(Exception):
    def __init__(self, message_key, **kwargs):
        super().__init__(message_key)
        self.message_key = message_key
        self.kwargs = kwargs


def map_provider_error(error):
    message = str(error)
    stage = getattr(error, "stage", None) or "provider"
    details = getattr(error, "details", None) or {}

    if message == "missing_openai_api_key":
        ui_key = "cash_ai_err_not_configured"
    elif message == "openai_invalid_response":
        ui_key = "cash_ai_err_provider_parse_failed"
    else:
        ui_key = "cash_ai_err_provider_failed"

    logger.error(
        "agent_payment_ai stage=%s provider_error=%s",
        stage,
        message,
    )

    return AgentPaymentAiError(
        ui_key,
        stage=stage,
        provider_error=message,
        details=details,
    )


def parse_amount(raw):
    if raw is None:
        return None

    text = str(raw).strip()

    if not text:
        return None

    normalized = (
        text.replace("$", "")
        .replace("U$S", "")
        .replace("USD", "")
        .replace("ARS", "")
        .strip()
    )

    if "," in normalized and "." in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")

    try:
        value = Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None

    if value <= 0:
        return None

    return float(round(value, 2))


def parse_receipt_date(raw):
    text = str(raw or "").strip()

    if not text:
        return None

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue

    return None


def normalize_confidence(raw):
    text = str(raw or "").strip().lower()

    if text in ("high", "alta"):
        return "high"

    if text in ("low", "baja"):
        return "low"

    try:
        value = float(text)
    except (TypeError, ValueError):
        return "medium"

    if value >= 0.8:
        return "high"

    if value < 0.5:
        return "low"

    return "medium"


def _clean_text(raw, limit=180):
    text = str(raw or "").strip()

    if not text:
        return None

    return text[:limit]


def normalize_extraction(raw):
    raw = raw or {}
    review = {
        str(item).strip()
        for item in (raw.get("fields_needing_review") or [])
        if item
    }

    currency = (raw.get("currency") or "").strip().upper() or None

    if currency not in CURRENCIES:
        currency = None
        review.add("currency")

    amount = parse_amount(raw.get("amount"))

    if amount is None:
        review.add("amount")

    payment_date = parse_receipt_date(raw.get("payment_date"))

    if payment_date is None:
        review.add("payment_date")

    raw_method = (raw.get("payment_method") or "").strip().lower()
    payment_method = PAYMENT_METHOD_ALIASES.get(raw_method)

    if payment_method is None:
        payment_method = PAYMENT_METHOD_NEEDS_REVIEW.get(raw_method)
        if payment_method is not None:
            review.add("payment_method")

    if payment_method is None:
        payment_method = PAYMENT_UNDETERMINED
        review.add("payment_method")

    exchange_rate = parse_amount(raw.get("exchange_rate"))

    field_confidence = {}
    raw_confidence = raw.get("field_confidence") or {}

    if isinstance(raw_confidence, dict):
        field_confidence = {
            str(key): normalize_confidence(value)
            for key, value in raw_confidence.items()
        }

    return {
        "amount": amount,
        "currency": currency,
        "payment_date": payment_date,
        "payment_method": payment_method,
        "bank_name": _clean_text(raw.get("bank_name")),
        "reference_number": _clean_text(
            raw.get("reference_number"),
            limit=80,
        ),
        "sender_name": _clean_text(raw.get("sender_name")),
        "recipient_name": _clean_text(raw.get("recipient_name")),
        "description": _clean_text(raw.get("description"), limit=240),
        "notes": _clean_text(raw.get("notes"), limit=500),
        "exchange_rate": exchange_rate,
        "confidence": normalize_confidence(raw.get("confidence")),
        "field_confidence": field_confidence,
        "fields_needing_review": sorted(review),
    }


def _selection_from_resolution(resolution):
    return {
        "agent_id": resolution["agent"]["selected_id"],
        "treasury_account_id": resolution["treasury_account"][
            "selected_id"
        ],
        "charge_movement_id": resolution["charge"]["selected_id"],
    }


def _store_analysis(
    draft_id,
    organization_id,
    payload,
    resolution,
):
    selection = _selection_from_resolution(resolution)
    update_agent_payment_ai_draft(
        draft_id,
        organization_id,
        status=STATUS_REVIEW,
        draft_payload=payload,
        resolution=resolution,
        fields_needing_review=payload["fields_needing_review"],
        confidence=payload["confidence"],
        error_message_key="",
        agent_id=selection["agent_id"],
        treasury_account_id=selection["treasury_account_id"],
        charge_movement_id=selection["charge_movement_id"],
    )
    return get_agent_payment_ai_draft(draft_id, organization_id)


def start_agent_payment_analysis(
    organization_id,
    *,
    user_id,
    file_storage,
    user_context_text="",
    agent_id=None,
    language="es",
):
    organization_id = require_organization_id(
        organization_id
    )
    context = (user_context_text or "").strip()

    if agent_id is not None:
        if get_agent_record(agent_id, organization_id) is None:
            raise AgentPaymentAiError(
                "agent_payment_ai_err_agent_not_found"
            )

    try:
        upload = validate_receipt_upload(
            file_storage,
            require_magic_bytes=True,
        )
    except CashReceiptError as error:
        logger.warning(
            "agent_payment_ai stage=receipt_validated_failed key=%s",
            error.message_key,
        )
        raise AgentPaymentAiError(error.message_key) from error

    image_bytes, image_type = prepare_image_for_ai(
        upload["bytes"],
        upload["content_type"],
    )

    draft_id = create_agent_payment_ai_draft(
        organization_id,
        created_by_user_id=user_id,
        confirm_token=secrets.token_urlsafe(24),
        idempotency_key=f"apai-{uuid.uuid4().hex}",
        user_context_text=context,
        agent_id=agent_id,
        status=STATUS_PROCESSING,
        provider=get_cash_ai_provider_name(),
    )

    try:
        attachment = save_receipt_bytes(
            organization_id,
            payload=upload,
            draft_id=draft_id,
            scope=SCOPE_AGENT_PAYMENTS,
        )
    except CashReceiptError as error:
        update_agent_payment_ai_draft(
            draft_id,
            organization_id,
            status=STATUS_FAILED,
            error_message_key=error.message_key,
        )
        raise AgentPaymentAiError(error.message_key) from error

    update_agent_payment_ai_draft(
        draft_id,
        organization_id,
        attachment_path=attachment["relative_path"],
        attachment_hash=attachment["sha256"],
        attachment_content_type=attachment["content_type"],
        attachment_original_name=attachment["original_filename"],
    )
    logger.info(
        "agent_payment_ai stage=draft_created draft_id=%s "
        "context_len=%s",
        draft_id,
        len(context),
    )

    return _run_extraction(
        organization_id,
        draft_id,
        image_bytes=image_bytes,
        image_content_type=image_type,
        user_context_text=context,
        preselected_agent_id=agent_id,
        language=language,
    )


def _run_extraction(
    organization_id,
    draft_id,
    *,
    image_bytes,
    image_content_type,
    user_context_text,
    preselected_agent_id,
    language,
):
    try:
        raw = extract_agent_payment_draft_from_provider(
            user_context_text=user_context_text,
            image_bytes=image_bytes,
            image_content_type=image_content_type,
            allowed_payment_methods=list(PAYMENT_METHODS),
            allowed_currencies=list(CURRENCIES),
            language=language,
        )
    except CashAiProviderError as error:
        mapped = map_provider_error(error)
        update_agent_payment_ai_draft(
            draft_id,
            organization_id,
            status=STATUS_FAILED,
            error_message_key=mapped.message_key,
        )
        raise mapped from error

    payload = normalize_extraction(raw)
    resolution = build_resolution(
        organization_id,
        payload,
        preselected_agent_id=preselected_agent_id,
    )
    logger.info(
        "agent_payment_ai stage=draft_ready draft_id=%s "
        "currency=%s agent_source=%s charge_source=%s",
        draft_id,
        payload.get("currency"),
        resolution["agent"]["source"],
        resolution["charge"]["source"],
    )

    return _store_analysis(
        draft_id,
        organization_id,
        payload,
        resolution,
    )


def retry_agent_payment_analysis(
    organization_id,
    draft_id,
    *,
    language="es",
):
    draft = _load_open_draft(organization_id, draft_id)

    image_bytes = None
    image_type = None

    if draft.get("attachment_path"):
        path = absolute_receipt_path(
            draft["attachment_path"],
            organization_id,
        )
        image_bytes, image_type = prepare_image_for_ai(
            path.read_bytes(),
            draft.get("attachment_content_type") or "image/jpeg",
        )

    update_agent_payment_ai_draft(
        draft_id,
        organization_id,
        status=STATUS_PROCESSING,
        error_message_key="",
    )

    return _run_extraction(
        organization_id,
        draft_id,
        image_bytes=image_bytes,
        image_content_type=image_type,
        user_context_text=draft.get("user_context_text") or "",
        preselected_agent_id=draft.get("agent_id"),
        language=language,
    )


def _load_open_draft(organization_id, draft_id):
    organization_id = require_organization_id(
        organization_id
    )
    draft = get_agent_payment_ai_draft(draft_id, organization_id)

    if draft is None:
        raise AgentPaymentAiError(
            "agent_payment_ai_err_draft_not_found"
        )

    if draft["status"] == STATUS_CONFIRMED:
        raise AgentPaymentAiError(
            "agent_payment_ai_err_already_confirmed"
        )

    if draft["status"] == STATUS_DISCARDED:
        raise AgentPaymentAiError(
            "agent_payment_ai_err_discarded"
        )

    return draft


def _optional_int(raw):
    text = str(raw or "").strip()

    if not text or text == "general":
        return None

    try:
        return int(text)
    except ValueError:
        return None


def update_draft_from_form(
    organization_id,
    draft_id,
    form_values,
):
    draft = _load_open_draft(organization_id, draft_id)
    payload = dict(draft.get("draft_payload") or {})
    form_values = form_values or {}

    payload.update(
        {
            "amount": form_values.get("amount"),
            "currency": form_values.get("currency"),
            "payment_date": form_values.get("payment_date"),
            "payment_method": form_values.get("payment_method"),
            "bank_name": form_values.get("bank_name"),
            "reference_number": form_values.get(
                "reference_number"
            ),
            "sender_name": form_values.get("sender_name"),
            "description": form_values.get("description"),
            "notes": form_values.get("notes"),
            "exchange_rate": form_values.get("exchange_rate"),
            "fields_needing_review": [],
        }
    )
    normalized = normalize_extraction(payload)
    normalized["field_confidence"] = (
        draft.get("draft_payload") or {}
    ).get("field_confidence") or {}

    raw_charge = str(
        form_values.get("charge_movement_id") or ""
    ).strip()

    if raw_charge == "general":
        apply_mode = APPLY_MODE_ON_ACCOUNT
    elif raw_charge:
        apply_mode = APPLY_MODE_CHARGE
    else:
        apply_mode = (
            form_values.get("apply_mode") or APPLY_MODE_CHARGE
        )

    agent_id = _optional_int(form_values.get("agent_id"))

    if agent_id is not None:
        if get_agent_record(agent_id, organization_id) is None:
            raise AgentPaymentAiError(
                "agent_payment_ai_err_agent_not_found"
            )

    resolution = build_resolution(
        organization_id,
        normalized,
        preselected_agent_id=agent_id,
        preselected_treasury_account_id=_optional_int(
            form_values.get("treasury_account_id")
        ),
        preselected_charge_id=_optional_int(
            form_values.get("charge_movement_id")
        ),
        apply_mode=apply_mode,
    )

    return _store_analysis(
        draft_id,
        organization_id,
        normalized,
        resolution,
    )


def discard_draft(organization_id, draft_id):
    draft = _load_open_draft(organization_id, draft_id)
    update_agent_payment_ai_draft(
        draft["id"],
        organization_id,
        status=STATUS_DISCARDED,
    )
    logger.info(
        "agent_payment_ai stage=draft_discarded draft_id=%s",
        draft["id"],
    )
    return get_agent_payment_ai_draft(
        draft["id"],
        organization_id,
    )


def _validate_for_confirm(organization_id, draft):
    payload = draft.get("draft_payload") or {}
    resolution = draft.get("resolution") or {}

    agent_id = draft.get("agent_id")

    if not agent_id:
        raise AgentPaymentAiError(
            "agent_payment_ai_err_agent_required"
        )

    if get_agent_record(agent_id, organization_id) is None:
        raise AgentPaymentAiError(
            "agent_payment_ai_err_agent_not_found"
        )

    currency = payload.get("currency")

    if currency not in CURRENCIES:
        raise AgentPaymentAiError(
            "agent_payment_ai_err_currency_required"
        )

    amount = parse_amount(payload.get("amount"))

    if amount is None:
        raise AgentPaymentAiError(
            "agent_payment_ai_err_amount_required"
        )

    payment_method = payload.get("payment_method")

    if payment_method not in PAYMENT_METHODS:
        raise AgentPaymentAiError(
            "agent_payment_ai_err_payment_method_required"
        )

    treasury_account_id = draft.get("treasury_account_id")

    if treasury_account_id is not None:
        account = get_treasury_account(
            treasury_account_id,
            organization_id,
        )
        if account is None or not account["is_active"]:
            raise AgentPaymentAiError(
                "agent_payment_ai_err_treasury_required"
            )
        if account["currency"] != currency:
            raise AgentPaymentAiError(
                "agent_payment_ai_err_treasury_currency"
            )

    charge_resolution = resolution.get("charge") or {}

    if charge_resolution.get("invalid_preselection"):
        raise AgentPaymentAiError(
            "agent_payment_ai_err_invalid_charge"
        )

    charge_movement_id = draft.get("charge_movement_id")
    apply_mode = (
        charge_resolution.get("apply_mode") or APPLY_MODE_CHARGE
    )

    if apply_mode == APPLY_MODE_ON_ACCOUNT:
        charge_movement_id = None

    if charge_movement_id is not None:
        charge = get_pending_charge_for_payment(
            organization_id,
            agent_id,
            charge_movement_id,
            currency,
        )
        if charge is None:
            raise AgentPaymentAiError(
                "agent_payment_ai_err_invalid_charge"
            )

    return {
        "agent_id": agent_id,
        "currency": currency,
        "amount": amount,
        "payment_method": payment_method,
        "treasury_account_id": treasury_account_id,
        "charge_movement_id": charge_movement_id,
        "payment_date": payload.get("payment_date")
        or date.today().isoformat(),
        "description": payload.get("description"),
        "reference_number": payload.get("reference_number"),
        "notes": payload.get("notes"),
        "exchange_rate": payload.get("exchange_rate"),
    }


def _build_manual_payment_payload(values):
    """Same form contract the manual payment route submits."""
    return {
        "movement_type": "payment",
        "currency": values["currency"],
        "amount": str(values["amount"]),
        "description": values["description"] or "",
        "movement_date": values["payment_date"],
        "payment_method": values["payment_method"],
        "reference_text": values["reference_number"] or "",
        "notes": values["notes"] or "",
        "applied_to_movement_id": (
            str(values["charge_movement_id"])
            if values["charge_movement_id"]
            else "general"
        ),
        "treasury_account_id": (
            str(values["treasury_account_id"])
            if values["treasury_account_id"]
            else ""
        ),
        "exchange_rate": (
            str(values["exchange_rate"])
            if values["exchange_rate"]
            else ""
        ),
    }


def confirm_agent_payment_draft(
    organization_id,
    draft_id,
    *,
    user_id,
    confirm_token,
    form_values=None,
    language="es",
):
    organization_id = require_organization_id(
        organization_id
    )
    draft = get_agent_payment_ai_draft(draft_id, organization_id)

    if draft is None:
        raise AgentPaymentAiError(
            "agent_payment_ai_err_draft_not_found"
        )

    if draft["confirm_token"] != confirm_token:
        logger.warning(
            "agent_payment_ai.confirm_failed stage=token "
            "draft_id=%s",
            draft_id,
        )
        raise AgentPaymentAiError(
            "agent_payment_ai_err_invalid_token"
        )

    if draft["status"] == STATUS_CONFIRMED:
        if draft.get("confirmed_movement_id"):
            logger.info(
                "agent_payment_ai.confirm_idempotent "
                "draft_id=%s movement_id=%s",
                draft_id,
                draft["confirmed_movement_id"],
            )
            return get_agent_account_movement(
                draft["confirmed_movement_id"],
                organization_id,
            )
        raise AgentPaymentAiError(
            "agent_payment_ai_err_already_confirmed"
        )

    if draft["status"] == STATUS_DISCARDED:
        raise AgentPaymentAiError(
            "agent_payment_ai_err_discarded"
        )

    if form_values:
        draft = update_draft_from_form(
            organization_id,
            draft_id,
            form_values,
        )

    values = _validate_for_confirm(organization_id, draft)
    attachment = None

    if draft.get("attachment_path"):
        attachment = {
            "path": draft["attachment_path"],
            "hash": draft["attachment_hash"],
            "content_type": draft["attachment_content_type"],
            "original_name": draft["attachment_original_name"],
        }

    logger.info(
        "agent_payment_ai.manual_service_called draft_id=%s "
        "agent_id=%s currency=%s",
        draft_id,
        values["agent_id"],
        values["currency"],
    )

    try:
        movement = create_movement(
            organization_id,
            values["agent_id"],
            _build_manual_payment_payload(values),
            created_by_user_id=user_id,
            idempotency_key=draft["idempotency_key"],
            language=language,
            attachment=attachment,
            receipt_number=values["reference_number"],
        )
    except AgentAccountError as error:
        logger.warning(
            "agent_payment_ai.confirm_failed stage=payment_service "
            "draft_id=%s key=%s",
            draft_id,
            error.message_key,
        )
        raise

    cash_movement_id = None

    if movement.get("source_type") == "cash":
        cash_movement_id = movement.get("source_id")

    update_agent_payment_ai_draft(
        draft_id,
        organization_id,
        status=STATUS_CONFIRMED,
        confirmed_movement_id=movement["id"],
        confirmed_cash_movement_id=cash_movement_id,
    )
    logger.info(
        "agent_payment_ai.draft_confirmed draft_id=%s "
        "movement_id=%s cash_movement_id=%s",
        draft_id,
        movement["id"],
        cash_movement_id,
    )

    return movement


def build_review_context(organization_id, draft):
    from modules.agent_account_presentation import (
        format_pending_charge_option,
    )

    payload = draft.get("draft_payload") or {}
    resolution = draft.get("resolution") or {}
    charge = resolution.get("charge") or {}
    agent = None

    if draft.get("agent_id"):
        agent = get_agent_record(
            draft["agent_id"],
            organization_id,
        )

    charge_options = [
        format_pending_charge_option(candidate)
        for candidate in charge.get("candidates") or []
    ]

    return {
        "draft": draft,
        "payload": payload,
        "resolution": resolution,
        "agent": agent,
        "agent_candidates": (
            resolution.get("agent") or {}
        ).get("candidates")
        or [],
        "treasury_candidates": (
            resolution.get("treasury_account") or {}
        ).get("candidates")
        or [],
        "charge_candidates": charge.get("candidates") or [],
        "charge_options": charge_options,
        "apply_mode": charge.get("apply_mode")
        or APPLY_MODE_CHARGE,
        "review_fields": set(
            draft.get("fields_needing_review") or []
        ),
        "currencies": CURRENCIES,
        "payment_methods": PAYMENT_METHODS,
        "statuses": {
            "processing": STATUS_PROCESSING,
            "review": STATUS_REVIEW,
            "confirmed": STATUS_CONFIRMED,
            "failed": STATUS_FAILED,
            "discarded": STATUS_DISCARDED,
        },
    }
