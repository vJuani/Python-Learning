from modules.database.agents_repository import get_agent_record
from modules.database.operation_documents_repository import (
    DOC_TYPE_AGENT_CLIENT,
    DOC_TYPE_MARTILLERO_CLIENT,
    DOC_TYPE_UIF_FORM,
    list_operation_documents,
)
from modules.database.operations_repository import (
    get_operation_record,
    update_operation_status,
)
from modules.database.properties_repository import get_property_record
from modules.operations import validate_operation_inputs
from modules.property_readiness import (
    property_readiness_requirement,
    validate_property_readiness,
)
from modules.property_types import INVOICE_FULL_COMMISSION_VALUES
from modules.workflow import STATUS_PENDING


class OperationNotReadyError(Exception):
    def __init__(self, readiness):
        self.readiness = readiness
        super().__init__("Operation is not ready for approval.")


def _document_types_present(documents):
    return {
        document.get("doc_type")
        for document in documents
        if document.get("doc_type")
    }


def _operation_form_values_from_record(operation):
    currency = operation.get("currency") or "USD"
    original_amount = operation.get(
        "original_amount",
        operation.get("sale_price"),
    )

    return {
        "agent_id": str(operation.get("agent_db_id") or ""),
        "property_id": str(operation.get("property_db_id") or ""),
        "currency": currency,
        "original_amount": str(original_amount or ""),
        "exchange_rate": (
            ""
            if currency == "USD"
            else str(operation.get("exchange_rate") or "")
        ),
        "commission_rate": str(
            operation.get("commission_rate") or ""
        ),
        "was_invoiced": operation.get("was_invoiced") or "no",
        "vat_amount": str(operation.get("vat_amount") or "0"),
        "operation_date": operation.get("date") or "",
    }


def _merge_operation_record(operation, pending_values=None):
    if not pending_values:
        return operation

    merged = dict(operation)
    field_map = {
        "date": "date",
        "was_invoiced": "was_invoiced",
        "vat_amount": "vat_amount",
        "sale_price": "sale_price",
        "commission_rate": "commission_rate",
        "currency": "currency",
        "original_amount": "original_amount",
        "exchange_rate": "exchange_rate",
        "invoice_full_commission": "invoice_full_commission",
        "agent_db_id": "agent_db_id",
        "property_db_id": "property_db_id",
    }

    for source_key, target_key in field_map.items():
        if source_key in pending_values:
            merged[target_key] = pending_values[source_key]

    return merged


def _is_invoiced(operation):
    return (operation.get("was_invoiced") or "no") == "yes"


def _requires_uif_form(operation):
    return _is_invoiced(operation)


def _requires_martillero_client_invoice(operation):
    return _is_invoiced(operation)


def _requires_agent_client_invoice(operation):
    if not _is_invoiced(operation):
        return False

    value = operation.get("invoice_full_commission") or "no"
    return value not in INVOICE_FULL_COMMISSION_VALUES or value == "no"


def validate_operation_readiness(
    operation_id,
    organization_id,
    user=None,
    *,
    pending_values=None,
):
    operation = get_operation_record(
        operation_id,
        organization_id,
    )

    if operation is None:
        return {
            "is_ready": False,
            "requirements": [],
            "missing_keys": ["operation"],
        }

    operation = _merge_operation_record(
        operation,
        pending_values,
    )

    requirements = []

    form_values = _operation_form_values_from_record(operation)
    field_errors, _parsed = validate_operation_inputs(
        form_values["agent_id"],
        form_values["property_id"],
        organization_id,
        form_values["original_amount"],
        form_values["commission_rate"],
        form_values["was_invoiced"],
        form_values["vat_amount"],
        form_values["operation_date"],
        currency=form_values["currency"],
        exchange_rate=form_values["exchange_rate"],
    )
    operation_data_complete = len(field_errors) == 0
    requirements.append(
        {
            "key": "operation_data",
            "label_key": "readiness_operation_data",
            "complete": operation_data_complete,
            "required": True,
        }
    )

    property_data = get_property_record(
        operation["property_db_id"],
        organization_id,
    )
    property_ready, _property_missing = validate_property_readiness(
        property_data,
    )
    requirements.append(
        property_readiness_requirement(property_data)
        if property_data is not None
        else {
            "key": "property",
            "label_key": "readiness_property",
            "complete": False,
            "required": True,
        }
    )

    agent = get_agent_record(
        operation["agent_db_id"],
        organization_id,
    )
    agent_complete = agent is not None
    requirements.append(
        {
            "key": "agent",
            "label_key": "readiness_agent",
            "complete": agent_complete,
            "required": True,
        }
    )

    documents = list_operation_documents(
        organization_id,
        operation_id,
    )
    present_types = _document_types_present(documents)

    uif_required = _requires_uif_form(operation)
    uif_complete = (
        not uif_required
        or DOC_TYPE_UIF_FORM in present_types
    )
    requirements.append(
        {
            "key": "uif_form",
            "label_key": "readiness_uif_form",
            "complete": uif_complete,
            "required": uif_required,
        }
    )

    martillero_required = _requires_martillero_client_invoice(
        operation,
    )
    martillero_complete = (
        not martillero_required
        or DOC_TYPE_MARTILLERO_CLIENT in present_types
    )
    requirements.append(
        {
            "key": "martillero_client",
            "label_key": "readiness_martillero_client",
            "complete": martillero_complete,
            "required": martillero_required,
        }
    )

    agent_invoice_required = _requires_agent_client_invoice(
        operation,
    )
    agent_client_complete = (
        not agent_invoice_required
        or DOC_TYPE_AGENT_CLIENT in present_types
    )
    requirements.append(
        {
            "key": "agent_client",
            "label_key": "readiness_agent_client",
            "complete": agent_client_complete,
            "required": agent_invoice_required,
        }
    )

    missing_keys = [
        item["key"]
        for item in requirements
        if item["required"] and not item["complete"]
    ]

    return {
        "is_ready": len(missing_keys) == 0,
        "requirements": requirements,
        "missing_keys": missing_keys,
    }


def assert_operation_ready_for_pending(
    operation_id,
    organization_id,
    user=None,
    *,
    pending_values=None,
):
    readiness = validate_operation_readiness(
        operation_id,
        organization_id,
        user=user,
        pending_values=pending_values,
    )

    if not readiness["is_ready"]:
        raise OperationNotReadyError(readiness)

    return readiness


def submit_operation_for_approval(
    operation_id,
    organization_id,
    user=None,
    *,
    pending_values=None,
):
    readiness = assert_operation_ready_for_pending(
        operation_id,
        organization_id,
        user=user,
        pending_values=pending_values,
    )

    update_operation_status(
        operation_id,
        organization_id,
        STATUS_PENDING,
        rejection_reason=None,
    )

    return readiness
