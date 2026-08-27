"""
Invoice AI intent parser and resolver.

The model/rule layer only interprets user intent. All factual data
(operation, amounts, clients, issuers) is resolved from the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from modules.auth import ROLE_ADMIN, ROLE_AGENT
from modules.database import (
    get_agents,
    get_operation_record,
    get_operations,
    get_properties,
)
from modules.invoicing import SIDE_BUYER, SIDE_SELLER, VALID_SIDES
from modules.search import fold_text


INTENT_CREATE_DRAFT = "create_invoice_draft"
INTENT_LIST_PENDING = "list_pending"
INTENT_UNKNOWN = "unknown"

SIDE_KEYWORDS = {
    SIDE_BUYER: (
        "comprador",
        "compradora",
        "buyer",
        "punta compradora",
        "punta comprador",
        "lado comprador",
    ),
    SIDE_SELLER: (
        "vendedor",
        "vendedora",
        "seller",
        "punta vendedora",
        "punta vendedor",
        "lado vendedor",
    ),
}

PENDING_KEYWORDS = (
    "que me falta facturar",
    "qué me falta facturar",
    "pendientes",
    "pending",
    "sin facturar",
)


@dataclass
class ParsedInvoiceIntent:
    intent: str = INTENT_UNKNOWN
    operation_reference: str = ""
    operation_id: int | None = None
    side: str | None = None
    issuer_type: str | None = None
    recipient_reference: str = ""
    confidence: float = 0.0
    raw_text: str = ""


@dataclass
class ResolvedInvoiceIntent:
    operation_id: int
    side: str
    operation_display: dict[str, Any] = field(default_factory=dict)


@dataclass
class DisambiguationResult:
    message_key: str
    options: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MissingSideResult:
    message_key: str = "billing_ai_ask_side"
    operation_id: int | None = None
    operation_label: str = ""


def _normalize_text(text):
    return " ".join(fold_text(text or "").split())


def _detect_side(text_folded):
    buyer_hits = sum(
        1 for kw in SIDE_KEYWORDS[SIDE_BUYER] if kw in text_folded
    )
    seller_hits = sum(
        1 for kw in SIDE_KEYWORDS[SIDE_SELLER] if kw in text_folded
    )
    if buyer_hits and not seller_hits:
        return SIDE_BUYER
    if seller_hits and not buyer_hits:
        return SIDE_SELLER
    return None


def _extract_operation_id(text):
    match = re.search(
        r"com[\s\-]*0*(\d+)",
        text,
        re.IGNORECASE,
    )
    if match:
        return int(match.group(1))
    return None


def _operation_label(operation):
    prop = operation.get("property") or ""
    op_id = operation.get("id") or ""
    agent = operation.get("agent") or ""
    return {
        "operation_id": operation["db_id"],
        "label": f"{prop} · {op_id}",
        "property": prop,
        "operation_code": op_id,
        "agent": agent,
    }


def parse_invoice_intent(text, *, context=None):
    raw = (text or "").strip()
    folded = _normalize_text(raw)
    context = context or {}

    if any(kw in folded for kw in PENDING_KEYWORDS):
        return ParsedInvoiceIntent(
            intent=INTENT_LIST_PENDING,
            confidence=0.9,
            raw_text=raw,
        )

    side = context.get("side") or _detect_side(folded)
    operation_id = context.get("operation_id")
    operation_reference = raw

    com_id = _extract_operation_id(raw)
    if com_id is not None:
        operation_id = com_id
        operation_reference = f"COM-{com_id:06d}"

    intent = INTENT_CREATE_DRAFT
    confidence = 0.5
    if operation_id or len(folded) >= 3:
        confidence = 0.75
    if operation_id and side:
        confidence = 0.95

    return ParsedInvoiceIntent(
        intent=intent,
        operation_reference=operation_reference,
        operation_id=operation_id,
        side=side,
        confidence=confidence,
        raw_text=raw,
    )


def _reference_contains_value(reference_folded, value):
    norm = _normalize_text(value)
    if not norm:
        return False
    if norm in reference_folded:
        return True
    for token in norm.split():
        if len(token) >= 4 and token in reference_folded:
            return True
    return False


def _matches_operation_reference(operation, reference_folded):
    if not reference_folded:
        return False

    haystacks = [
        operation.get("property"),
        operation.get("property_external_id"),
        operation.get("id"),
        f"prop-{operation.get('property_db_id', 0):06d}",
    ]
    return any(
        _reference_contains_value(reference_folded, value)
        for value in haystacks
        if value
    )


def _find_operations_by_reference(
    reference,
    organization_id,
    *,
    agent_id=None,
):
    reference_folded = _normalize_text(reference)
    com_id = _extract_operation_id(reference)
    if com_id is not None:
        operation = get_operation_record(
            com_id,
            organization_id,
        )
        if operation is None:
            return []
        if agent_id is not None and operation.get(
            "agent_db_id"
        ) != agent_id:
            return []
        return [operation]

    operations = get_operations(organization_id)
    if agent_id is not None:
        operations = [
            op
            for op in operations
            if op.get("agent_db_id") == agent_id
        ]

    matched = [
        op
        for op in operations
        if _matches_operation_reference(op, reference_folded)
    ]
    if matched:
        return matched

    properties = get_properties(organization_id)
    if agent_id is not None:
        properties = [
            p
            for p in properties
            if p.get("agent_id") == agent_id
        ]

    property_ids = set()
    for prop in properties:
        needles = [
            prop.get("address"),
            prop.get("external_id"),
            f"prop-{prop['id']:06d}",
        ]
        if any(
            _reference_contains_value(reference_folded, needle)
            for needle in needles
            if needle
        ):
            property_ids.add(prop["id"])

    if not property_ids:
        return []

    return [
        op
        for op in operations
        if op.get("property_db_id") in property_ids
    ]


def _agent_name_in_text(agent_name, text_folded):
    return _normalize_text(agent_name) in text_folded


def resolve_invoice_intent(
    parsed,
    organization_id,
    user,
    *,
    agent_scope=None,
):
    if parsed.intent == INTENT_LIST_PENDING:
        return parsed

    if parsed.intent != INTENT_CREATE_DRAFT:
        return parsed

    role = user.get("role")
    if role == ROLE_AGENT:
        agent_scope = user.get("agent_id")
    elif role != ROLE_ADMIN:
        raise PermissionError("forbidden")

    operation_id = parsed.operation_id
    com_id = _extract_operation_id(parsed.raw_text)
    if com_id is not None:
        operation_id = com_id

    operations = []
    if operation_id is not None:
        operation = get_operation_record(
            operation_id,
            organization_id,
        )
        if operation is not None:
            if (
                agent_scope is None
                or operation.get("agent_db_id") == agent_scope
            ):
                operations = [operation]
    else:
        operations = _find_operations_by_reference(
            parsed.operation_reference,
            organization_id,
            agent_id=agent_scope,
        )

        agents = get_agents(organization_id)
        text_folded = _normalize_text(parsed.raw_text)
        agent_matches = [
            a
            for a in agents
            if _agent_name_in_text(a["name"], text_folded)
        ]
        if len(agent_matches) == 1 and operations:
            operations = [
                op
                for op in operations
                if op.get("agent_db_id") == agent_matches[0]["id"]
            ]

    if not operations:
        return DisambiguationResult(
            message_key="billing_ai_operation_not_found",
            options=[],
        )

    if len(operations) > 1:
        return DisambiguationResult(
            message_key="billing_ai_disambiguation",
            options=[
                _operation_label(op)
                for op in operations[:8]
            ],
        )

    operation = operations[0]
    side = parsed.side
    if side not in VALID_SIDES:
        return MissingSideResult(
            operation_id=operation["db_id"],
            operation_label=_operation_label(operation)["label"],
        )

    return ResolvedInvoiceIntent(
        operation_id=operation["db_id"],
        side=side,
        operation_display=_operation_label(operation),
    )
