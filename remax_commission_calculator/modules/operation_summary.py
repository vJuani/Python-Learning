"""
Operation summary view-model.

Single source of truth for HTML, PDF, and Excel exports.
Does not recalculate commissions or touch schema.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from modules.branding import (
    DEFAULT_BRAND_LOGO,
    get_brand_name,
    resolve_brand_logo_path,
)
from modules.config import BASE_DIR
from modules.database.operation_documents_repository import (
    DOC_TYPE_LABEL_KEYS,
    list_operation_documents,
)
from modules.database.organization_settings_repository import (
    get_organization_settings,
)
from modules.database.users_repository import get_user_by_id
from modules.i18n import translate

ZERO = Decimal("0")


def _t(key, language):
    return translate(key, language)


def _as_decimal(value):
    if value is None:
        return None

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _as_float(value):
    amount = _as_decimal(value)

    if amount is None:
        return None

    return float(amount)


def _is_positive(value):
    amount = _as_decimal(value)
    return amount is not None and amount > ZERO


def _user_display_name(user):
    if user is None:
        return None

    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    full = f"{first} {last}".strip()

    if full:
        return full

    username = (user.get("username") or "").strip()

    if username:
        return username

    email = (user.get("email") or "").strip()

    return email or None


def _resolve_logo_path(logo_path):
    if not logo_path:
        return None

    candidate = (BASE_DIR / "static" / logo_path).resolve()

    try:
        candidate.relative_to((BASE_DIR / "static").resolve())
    except ValueError:
        return None

    if candidate.is_file():
        return candidate

    return None


def _brand_logo_path(organization_logo_path):
    org_logo = _resolve_logo_path(organization_logo_path)

    if org_logo is not None:
        return org_logo

    return resolve_brand_logo_path()


def _doc_type_label(doc_type, language):
    label_key = DOC_TYPE_LABEL_KEYS.get(doc_type)

    if label_key:
        return _t(label_key, language)

    return doc_type


def build_download_basename(operation_id_label, language):
    safe_id = str(operation_id_label).replace(" ", "-")
    lang = language if language in ("es", "en") else "es"
    return f"{safe_id}_operation-summary_{lang}"


def _line(key, label, value, kind, currency="USD", emphasize=False):
    return {
        "key": key,
        "label": label,
        "value": value,
        "kind": kind,
        "currency": currency,
        "emphasize": emphasize,
    }


def build_commission_lines(operation, language):
    """
    Visible commission presentation lines from persisted amounts only.
    """
    labels = {
        "rate": _t("field_commission_rate", language),
        "commission": _t("op_summary_commission", language),
        "total": _t("total_commission", language),
        "abao": _t("abao", language),
        "after_abao": _t("commission_after_abao", language),
        "martillero": _t("martillero", language),
        "agent_payment": _t("agent_payment", language),
        "office_net": _t("office_net_payment", language),
        "office_total": _t("office_total", language),
    }

    rate = _as_float(operation.get("commission_rate"))
    total = _as_float(operation.get("total_commission"))
    abao = operation.get("abao")
    after_abao = _as_float(operation.get("commission_after_abao"))
    martillero = operation.get("martillero")
    agent_payment = _as_float(operation.get("agent_payment"))
    office_payment = _as_float(operation.get("office_payment"))
    office_total = _as_float(operation.get("office_total"))

    lines = [
        _line("commission_rate", labels["rate"], rate, "percent"),
    ]

    if _is_positive(abao):
        lines.append(
            _line(
                "total_commission",
                labels["total"],
                total,
                "money",
                emphasize=True,
            )
        )
        lines.append(
            _line(
                "abao",
                labels["abao"],
                _as_float(abao),
                "money",
            )
        )
        lines.append(
            _line(
                "commission_after_abao",
                labels["after_abao"],
                after_abao,
                "money",
            )
        )
    else:
        lines.append(
            _line(
                "commission",
                labels["commission"],
                total,
                "money",
                emphasize=True,
            )
        )

    if _is_positive(martillero):
        lines.append(
            _line(
                "martillero",
                labels["martillero"],
                _as_float(martillero),
                "money",
            )
        )

    lines.append(
        _line(
            "agent_payment",
            labels["agent_payment"],
            agent_payment,
            "money",
        )
    )
    lines.append(
        _line(
            "office_payment",
            labels["office_net"],
            office_payment,
            "money",
        )
    )
    lines.append(
        _line(
            "office_total",
            labels["office_total"],
            office_total,
            "money",
            emphasize=True,
        )
    )

    return lines


def build_billing_lines(operation, language):
    currency = operation.get("currency") or "USD"
    original_amount = _as_float(operation.get("original_amount"))
    exchange_rate = _as_float(operation.get("exchange_rate"))
    sale_price = _as_float(operation.get("sale_price"))
    vat_amount = operation.get("vat_amount")
    invoiced_label = _t(
        operation.get("was_invoiced") or "no",
        language,
    )

    lines = [
        _line(
            "currency",
            _t("currency", language),
            currency,
            "text",
        ),
    ]

    if currency != "USD":
        lines.append(
            _line(
                "original_amount",
                _t("original_amount", language),
                original_amount,
                "money",
                currency=currency,
            )
        )
        lines.append(
            _line(
                "exchange_rate",
                _t("exchange_rate", language),
                exchange_rate,
                "rate",
            )
        )

    lines.append(
        _line(
            "sale_price",
            _t("usd_equivalent", language),
            sale_price,
            "money",
            emphasize=True,
        )
    )
    lines.append(
        _line(
            "was_invoiced",
            _t("invoiced", language),
            invoiced_label,
            "text",
        )
    )

    if _is_positive(vat_amount):
        lines.append(
            _line(
                "vat_amount",
                _t("vat", language),
                _as_float(vat_amount),
                "money",
                emphasize=True,
            )
        )

    return lines


def build_operation_context_lines(operation, language):
    return [
        _line(
            "operation_id",
            _t("operation_id", language),
            operation.get("id"),
            "text",
        ),
        _line(
            "date",
            _t("date", language),
            operation.get("date"),
            "text",
        ),
        _line(
            "status",
            _t("status", language),
            _t(
                f"status_{operation.get('status') or 'approved'}",
                language,
            ),
            "text",
        ),
        _line(
            "agent",
            _t("agent", language),
            operation.get("agent"),
            "text",
        ),
        _line(
            "agent_type",
            _t("type", language),
            operation.get("agent_type"),
            "text",
        ),
        _line(
            "property_id",
            _t("property_id", language),
            operation.get("property_id"),
            "text",
        ),
        _line(
            "property",
            _t("property", language),
            operation.get("property"),
            "text",
        ),
        _line(
            "jurisdiction",
            _t("jurisdiction", language),
            operation.get("jurisdiction"),
            "text",
        ),
    ]


def build_operation_summary(
    operation,
    *,
    language,
    can_see_documents,
    documents=None,
    organization_settings=None,
    created_by_user=None,
    reviewed_by_user=None,
    uploader_names=None,
):
    language = language if language in ("es", "en") else "es"
    currency = operation.get("currency") or "USD"
    status = operation.get("status") or "approved"
    status_label = _t(f"status_{status}", language)
    invoiced_label = _t(
        operation.get("was_invoiced") or "no",
        language,
    )

    settings = organization_settings
    organization_name = None
    organization_logo_rel = None

    if settings is not None:
        organization_name = settings.get("display_name")
        organization_logo_rel = settings.get("logo_path")

    logo_path = _brand_logo_path(organization_logo_rel)
    used_org_logo = (
        organization_logo_rel is not None
        and logo_path is not None
        and _resolve_logo_path(organization_logo_rel) == logo_path
    )

    created_by_name = _user_display_name(created_by_user)
    reviewed_by_name = _user_display_name(reviewed_by_user)
    uploader_names = uploader_names or {}

    document_rows = []

    if can_see_documents:
        source_docs = documents

        if source_docs is None:
            source_docs = list_operation_documents(
                operation["organization_id"],
                operation["db_id"],
            )

        for document in source_docs:
            uploader_id = document.get("uploaded_by_user_id")
            document_rows.append(
                {
                    "id": document["id"],
                    "doc_type": document["doc_type"],
                    "doc_type_label": _doc_type_label(
                        document["doc_type"],
                        language,
                    ),
                    "original_filename": document[
                        "original_filename"
                    ],
                    "uploaded_at": document.get("updated_at")
                    or document.get("created_at"),
                    "uploaded_by_name": uploader_names.get(
                        uploader_id
                    ),
                    "size_bytes": document.get("size_bytes"),
                    "content_type": document.get("content_type"),
                }
            )

    numbers = {
        "original_amount": _as_float(
            operation.get("original_amount")
        ),
        "exchange_rate": _as_float(
            operation.get("exchange_rate")
        ),
        "sale_price": _as_float(operation.get("sale_price")),
        "commission_rate": _as_float(
            operation.get("commission_rate")
        ),
        "total_commission": _as_float(
            operation.get("total_commission")
        ),
        "abao": _as_float(operation.get("abao")),
        "commission_after_abao": _as_float(
            operation.get("commission_after_abao")
        ),
        "martillero": _as_float(operation.get("martillero")),
        "agent_payment": _as_float(
            operation.get("agent_payment")
        ),
        "office_payment": _as_float(
            operation.get("office_payment")
        ),
        "vat_amount": _as_float(operation.get("vat_amount")),
        "office_total": _as_float(
            operation.get("office_total")
        ),
    }

    commission_lines = build_commission_lines(
        operation,
        language,
    )
    billing_lines = build_billing_lines(operation, language)

    return {
        "language": language,
        "can_see_documents": bool(can_see_documents),
        "download_basename": build_download_basename(
            operation["id"],
            language,
        ),
        "brand": {
            "app_name": get_brand_name(),
            "slogan": _t("app_slogan", language),
            "organization_name": organization_name,
            "logo_path": str(logo_path) if logo_path else None,
            "used_organization_logo": used_org_logo,
        },
        "operation": {
            "id": operation["id"],
            "date": operation.get("date"),
            "status": status,
            "status_label": status_label,
            "currency": currency,
            "was_invoiced": operation.get("was_invoiced"),
            "was_invoiced_label": invoiced_label,
            "rejection_reason": operation.get(
                "rejection_reason"
            ),
            "reviewed_at": operation.get("reviewed_at"),
            "agent_name": operation.get("agent"),
            "agent_type": operation.get("agent_type"),
            "property_id": operation.get("property_id"),
            "property_address": operation.get("property"),
            "jurisdiction": operation.get("jurisdiction"),
        },
        "people": {
            "created_by_name": created_by_name,
            "reviewed_by_name": reviewed_by_name,
        },
        "numbers": numbers,
        "commission_lines": commission_lines,
        "billing_lines": billing_lines,
        "documents": document_rows,
        "labels": {
            "page_title": _t("op_summary_title", language),
            "page_subtitle": _t(
                "op_summary_subtitle",
                language,
            ),
            "section_operation": _t(
                "op_summary_section_operation",
                language,
            ),
            "section_property": _t(
                "op_summary_section_property",
                language,
            ),
            "section_agent": _t(
                "op_summary_section_agent",
                language,
            ),
            "section_commission": _t(
                "op_summary_section_commission",
                language,
            ),
            "section_billing": _t(
                "op_summary_section_billing",
                language,
            ),
            "section_status": _t(
                "op_summary_section_status",
                language,
            ),
            "section_documents": _t(
                "op_summary_section_documents",
                language,
            ),
            "sheet_summary": _t(
                "op_summary_sheet_summary",
                language,
            ),
            "sheet_commission": _t(
                "op_summary_sheet_commission",
                language,
            ),
            "sheet_approval": _t(
                "op_summary_sheet_approval",
                language,
            ),
            "sheet_documents": _t(
                "op_summary_sheet_documents",
                language,
            ),
            "field_operation_id": _t("operation_id", language),
            "field_date": _t("date", language),
            "field_status": _t("status", language),
            "field_currency": _t("currency", language),
            "field_original_amount": _t(
                "original_amount",
                language,
            ),
            "field_exchange_rate": _t(
                "exchange_rate",
                language,
            ),
            "field_usd_equivalent": _t(
                "usd_equivalent",
                language,
            ),
            "field_agent": _t("agent", language),
            "field_agent_type": _t("type", language),
            "field_property": _t("property", language),
            "field_property_id": _t("property_id", language),
            "field_jurisdiction": _t(
                "jurisdiction",
                language,
            ),
            "field_invoiced": _t("invoiced", language),
            "field_created_by": _t(
                "op_summary_created_by",
                language,
            ),
            "field_reviewed_by": _t(
                "op_summary_reviewed_by",
                language,
            ),
            "field_reviewed_at": _t(
                "op_summary_reviewed_at",
                language,
            ),
            "field_rejection_reason": _t(
                "rejection_reason",
                language,
            ),
            "field_doc_type": _t(
                "op_summary_doc_type",
                language,
            ),
            "field_filename": _t(
                "op_summary_filename",
                language,
            ),
            "field_uploaded_at": _t(
                "op_summary_uploaded_at",
                language,
            ),
            "field_uploaded_by": _t(
                "op_summary_uploaded_by",
                language,
            ),
            "empty_value": _t("op_summary_empty", language),
            "no_documents": _t(
                "op_summary_no_documents",
                language,
            ),
            "documents_hidden": _t(
                "op_summary_documents_hidden",
                language,
            ),
            "download_pdf": _t(
                "op_summary_download_pdf",
                language,
            ),
            "download_xlsx": _t(
                "op_summary_download_xlsx",
                language,
            ),
            "billing_note": _t(
                "op_summary_billing_note",
                language,
            ),
        },
    }


def load_operation_summary(
    operation,
    *,
    language,
    can_see_documents,
):
    settings = get_organization_settings(
        operation["organization_id"]
    )

    created_by_user = None
    reviewed_by_user = None

    created_by_id = operation.get("created_by_user_id")
    reviewed_by_id = operation.get("reviewed_by_user_id")

    if created_by_id is not None:
        created_by_user = get_user_by_id(created_by_id)

    if reviewed_by_id is not None:
        reviewed_by_user = get_user_by_id(reviewed_by_id)

    documents = None
    uploader_names = {}

    if can_see_documents:
        documents = list_operation_documents(
            operation["organization_id"],
            operation["db_id"],
        )

        for document in documents:
            user_id = document.get("uploaded_by_user_id")

            if user_id is None or user_id in uploader_names:
                continue

            uploader_names[user_id] = _user_display_name(
                get_user_by_id(user_id)
            )

    return build_operation_summary(
        operation,
        language=language,
        can_see_documents=can_see_documents,
        documents=documents,
        organization_settings=settings,
        created_by_user=created_by_user,
        reviewed_by_user=reviewed_by_user,
        uploader_names=uploader_names,
    )


__all__ = [
    "DEFAULT_BRAND_LOGO",
    "build_billing_lines",
    "build_commission_lines",
    "build_download_basename",
    "build_operation_summary",
    "load_operation_summary",
]
