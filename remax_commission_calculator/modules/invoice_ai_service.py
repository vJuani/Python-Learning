"""
Invoice AI intent parser and resolver.

The model/rule layer only interprets user intent. All factual data
(operation, amounts, clients, issuers) is resolved from the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from decimal import Decimal, InvalidOperation

from modules.auth import ROLE_ADMIN, ROLE_AGENT
from modules.database import (
    get_agents,
    get_operation_record,
    get_operations,
    get_parties_for_operation,
    get_properties,
)
from modules.invoicing import (
    SIDE_BUYER,
    SIDE_SELLER,
    VALID_SIDES,
    get_charge_invoice_context,
    list_billable_agent_charges,
)
from modules.jrh_ai_classify import (
    ORIGIN_CHARGE,
    ORIGIN_OPERATION,
    ORIGIN_UNKNOWN,
    classify_intent,
    extract_entities,
    extract_invoice_side,
    fold_text,
    normalize_user_text,
)
from modules.jrh_ai_intents import START_INVOICE
from modules.jrh_ai_context import sanitize_pending_invoice
from modules.search import fuzzy_token_match, search_agents_flexible


INTENT_CREATE_DRAFT = "create_invoice_draft"
INTENT_LIST_PENDING = "list_pending"
INTENT_UNKNOWN = "unknown"
INVOICE_CREATE_RE = re.compile(
    r"\b(facturame|facturale|haceme la factura|quiero facturar)\b"
)

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
    origin_type: str = ORIGIN_UNKNOWN
    entities: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResolvedInvoiceIntent:
    operation_id: int
    side: str
    operation_display: dict[str, Any] = field(default_factory=dict)


@dataclass
class DisambiguationResult:
    message_key: str
    options: list[dict[str, Any]] = field(default_factory=list)
    expected_entity: str = ""
    message_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class MissingSideResult:
    message_key: str = "billing_ai_ask_side"
    operation_id: int | None = None
    operation_label: str = ""


@dataclass
class ResolvedChargeIntent:
    charge_id: int
    agent_id: int
    charge: dict[str, Any] = field(default_factory=dict)
    agent: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExistingInvoiceResult:
    message_key: str = "billing_ai_invoice_exists"
    invoice_id: int | None = None
    origin_type: str = ORIGIN_UNKNOWN
    label: str = ""


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
    raw = normalize_user_text(text)
    folded = _normalize_text(raw)
    context = context or {}
    pending = context.get("pending_invoice") or {}
    classified = classify_intent(raw, context=context)
    entities = dict(classified.get("entities") or extract_entities(raw, context=context))

    list_pending = any(kw in folded for kw in PENDING_KEYWORDS)
    if list_pending and not INVOICE_CREATE_RE.search(folded) and not pending.get("expected_entity"):
        return ParsedInvoiceIntent(
            intent=INTENT_LIST_PENDING,
            confidence=0.9,
            raw_text=raw,
            origin_type=ORIGIN_UNKNOWN,
            entities=entities,
        )

    expected = (
        pending.get("expected_entity")
        or entities.get("expected_entity")
        or ""
    )
    side = (
        pending.get("side")
        or context.get("side")
        or entities.get("side")
        or _detect_side(folded)
        or extract_invoice_side(raw)
    )
    operation_id = pending.get("operation_id") or context.get("operation_id")
    if entities.get("previous_kind") == "operation" and entities.get("previous_id"):
        operation_id = operation_id or entities.get("previous_id")
    operation_reference = (
        entities.get("property_text")
        or entities.get("address")
        or entities.get("operation_reference")
        or ""
    )
    if not operation_reference and expected not in {"agent", "charge", "side"}:
        operation_reference = raw

    com_id = _extract_operation_id(raw)
    if com_id is not None:
        operation_id = com_id
        operation_reference = f"COM-{com_id:06d}"
        entities["origin_type"] = ORIGIN_OPERATION

    origin = entities.get("origin_type") or pending.get("origin_type") or ORIGIN_UNKNOWN
    if expected == "agent":
        origin = pending.get("origin_type") or ORIGIN_CHARGE
        operation_reference = ""
    if expected == "charge":
        origin = pending.get("origin_type") or ORIGIN_CHARGE
    if origin == ORIGIN_UNKNOWN and operation_id:
        origin = ORIGIN_OPERATION
    if origin == ORIGIN_UNKNOWN and (
        entities.get("charge_hint")
        or entities.get("billing_period")
        or entities.get("self")
        or entities.get("generic_agents")
    ):
        origin = ORIGIN_CHARGE
    if pending.get("origin_type") and origin == ORIGIN_UNKNOWN:
        origin = pending["origin_type"]
    if expected:
        entities["expected_entity"] = expected
    if pending.get("charge_category") and not entities.get("charge_category"):
        entities["charge_category"] = pending["charge_category"]
        entities["charge_hint"] = entities.get("charge_hint") or pending["charge_category"]

    intent = INTENT_CREATE_DRAFT
    confidence = float(classified.get("confidence") or 0.5)
    if classified.get("intent") == START_INVOICE:
        confidence = max(confidence, 0.86)
    if operation_id or len(folded) >= 3 or expected:
        confidence = max(confidence, 0.75)
    if operation_id and side:
        confidence = max(confidence, 0.95)

    return ParsedInvoiceIntent(
        intent=intent,
        operation_reference=operation_reference,
        operation_id=operation_id,
        side=side,
        confidence=confidence,
        raw_text=raw,
        origin_type=origin,
        entities=entities,
    )


def _reference_contains_value(reference_folded, value):
    norm = _normalize_text(value)
    if not norm or not reference_folded:
        return False
    if norm in reference_folded or reference_folded in norm:
        return True
    ref_tokens = [
        token
        for token in reference_folded.split()
        if len(token) >= 4 or token.isdigit()
    ]
    val_tokens = set(norm.split())
    numbers = [token for token in ref_tokens if token.isdigit()]
    if numbers:
        return all(number in val_tokens for number in numbers) and any(
            token in val_tokens for token in ref_tokens if not token.isdigit()
        )
    for token in ref_tokens:
        if token in val_tokens:
            return True
        if len(token) >= 5 and fuzzy_token_match(norm, token):
            return True
    return False


def _parse_decimal(value):
    text = str(value or "").strip().replace(" ", "")
    if re.fullmatch(r"\d+,\d+", text):
        text = text.replace(",", ".")
    return Decimal(text)


def _amount_close(left, right):
    try:
        a = _parse_decimal(left)
        b = _parse_decimal(right)
    except (InvalidOperation, TypeError, ValueError):
        return False
    return abs(a - b) <= Decimal("0.05")


def _score_charge(charge, entities):
    score = 10
    hint = fold_text(
        entities.get("charge_hint")
        or entities.get("charge_category")
        or ""
    )
    period = fold_text(entities.get("billing_period") or entities.get("period") or "")
    blob = fold_text(
        " ".join(
            str(charge.get(key) or "")
            for key in (
                "name",
                "description",
                "charge_category",
                "period_label",
                "billing_period",
            )
        )
    )
    if hint and hint in blob:
        score += 40
    if period:
        month = period.split("-")[-1] if "-" in period else period
        if period in blob or month in blob:
            score += 25
        month_names = (
            "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
        )
        try:
            month_index = int(month)
            if 1 <= month_index <= 12 and month_names[month_index - 1] in blob:
                score += 25
        except ValueError:
            if period in blob:
                score += 15
    amount = entities.get("amount")
    charge_amount = (
        charge.get("amount")
        or charge.get("pending_amount")
        or charge.get("gross_amount")
        or charge.get("remaining_amount")
    )
    if amount not in (None, "") and charge_amount not in (None, ""):
        if _amount_close(amount, charge_amount):
            score += 35
        else:
            score -= 20
    currency = (entities.get("currency") or "").upper()
    if currency and (charge.get("currency") or "").upper() == currency:
        score += 20
    elif currency and charge.get("currency"):
        score -= 15
    return score


def _charge_option(charge, agent=None):
    amount = (
        charge.get("amount")
        or charge.get("pending_amount")
        or charge.get("gross_amount")
        or ""
    )
    currency = charge.get("currency") or ""
    name = charge.get("name") or charge.get("description") or ""
    period = charge.get("period_label") or charge.get("billing_period") or ""
    return {
        "id": charge.get("id"),
        "charge_id": charge.get("id"),
        "name": " · ".join(part for part in (name, period) if part),
        "label": " · ".join(
            part
            for part in (name, period, f"{currency} {amount}".strip())
            if part
        ),
        "kind": "charge",
        "amount": amount,
        "currency": currency,
        "agent_name": (agent or {}).get("name") or "",
        "href_name": "billing_prepare_charge",
        "href_args": {"charge_id": charge.get("id")},
    }


def _available_sides(organization_id, operation_id):
    try:
        parties = get_parties_for_operation(organization_id, operation_id) or []
    except Exception:
        parties = []
    sides = []
    for party in parties:
        role = party.get("party_role") or party.get("side")
        if role not in VALID_SIDES:
            continue
        if party.get("billing_enabled") in (0, False):
            continue
        amount = party.get("invoice_amount")
        if amount in (None, "", 0, "0"):
            continue
        if role not in sides:
            sides.append(role)
    return sides


def _match_agents(organization_id, query, *, agent_scope=None):
    if agent_scope and not query:
        from modules.database.agents_repository import get_agent_record

        own = get_agent_record(agent_scope, organization_id)
        return [own] if own else []
    matches = search_agents_flexible(query or "", organization_id, limit=8)
    if agent_scope is not None:
        matches = [
            item for item in matches if int(item.get("id") or 0) == int(agent_scope)
        ]
    return matches


def _charge_matches_category(charge, category):
    if not category:
        return True
    hint = fold_text(category)
    blob = fold_text(
        " ".join(
            str(charge.get(key) or "")
            for key in ("name", "description", "charge_category", "period_label")
        )
    )
    return hint in blob


def _agents_with_pending_charges(
    organization_id,
    *,
    category="",
    agent_scope=None,
):
    agents = get_agents(organization_id)
    if agent_scope is not None:
        agents = [
            item for item in agents if int(item.get("id") or 0) == int(agent_scope)
        ]
    options = []
    for agent in agents:
        charges = list_billable_agent_charges(
            organization_id,
            agent_id=agent["id"],
        )
        matching = [
            item for item in charges if _charge_matches_category(item, category)
        ]
        if not matching:
            continue
        first = matching[0]
        amount = (
            first.get("gross_amount")
            or first.get("amount")
            or first.get("pending_amount")
            or ""
        )
        currency = first.get("currency") or ""
        options.append(
            {
                "id": agent.get("id"),
                "name": agent.get("name"),
                "label": " · ".join(
                    part
                    for part in (
                        agent.get("name") or "",
                        f"{currency} {amount}".strip(),
                    )
                    if part
                ),
                "kind": "agent",
                "amount": amount,
                "currency": currency,
            }
        )
        if len(options) >= 8:
            break
    return options


def build_pending_invoice_context(parsed, result):
    """Persist the slot we are waiting for. Keep until resolve, cancel, or new intent."""
    entities = dict((parsed.entities if parsed else {}) or {})
    origin = (parsed.origin_type if parsed else "") or entities.get("origin_type") or ""
    category = entities.get("charge_category") or entities.get("charge_hint") or ""
    expected = ""
    operation_id = parsed.operation_id if parsed else None
    side = parsed.side if parsed else None
    if isinstance(result, MissingSideResult):
        expected = "side"
        origin = ORIGIN_OPERATION
        operation_id = result.operation_id
    elif isinstance(result, DisambiguationResult):
        expected = result.expected_entity
        if not expected:
            if result.message_key in {
                "billing_ai_need_agent",
                "billing_ai_agent_not_found",
                "billing_ai_agents_with_fee",
                "jrh_ai_agent_missing",
                "jrh_ai_agent_ambiguous",
            }:
                expected = "agent"
            elif result.message_key in {
                "jrh_ai_invoice_candidates",
                "jrh_ai_charge_ambiguous",
                "billing_ai_charge_missing",
            }:
                expected = "charge"
            elif result.message_key in {
                "billing_ai_disambiguation",
                "billing_ai_operation_not_found",
            }:
                expected = "operation"
    if not expected:
        return {}
    if expected == "agent":
        origin = origin or ORIGIN_CHARGE
    return sanitize_pending_invoice(
        {
            "intent": START_INVOICE,
            "origin_type": origin,
            "expected_entity": expected,
            "charge_category": category,
            "operation_id": operation_id,
            "side": side,
            "entities": entities,
        }
    )


def _resolve_charges(parsed, organization_id, *, agent_scope=None, user=None):
    from modules.database.agents_repository import get_agent_record
    from modules.jrh_ai_resolver import resolve_pending_charges

    entities = parsed.entities or {}
    query = entities.get("agent_name") or ""
    generic = bool(entities.get("generic_agents"))
    use_self = bool(entities.get("self")) or (
        user and user.get("role") == ROLE_AGENT and not query and not generic
    )
    scoped = agent_scope
    if generic or (not query and not use_self):
        category = entities.get("charge_category") or entities.get("charge_hint") or ""
        options = _agents_with_pending_charges(
            organization_id,
            category=category,
            agent_scope=scoped,
        )
        if len(options) != 1:
            message_key = (
                "billing_ai_agents_with_fee"
                if options and fold_text(category) == "fee"
                else "billing_ai_need_agent"
            )
            return DisambiguationResult(
                message_key=message_key,
                options=options,
                expected_entity="agent",
                message_data={"count": len(options), "name": query},
            )
        query = options[0].get("name") or ""
        entities["agent_name"] = query
        parsed.entities = entities
    if use_self and scoped:
        agents = _match_agents(organization_id, "", agent_scope=scoped)
    elif query:
        agents = _match_agents(organization_id, query, agent_scope=scoped)
    else:
        return DisambiguationResult(
            message_key="billing_ai_need_agent",
            options=[],
            expected_entity="agent",
        )
    if not agents:
        display = " ".join((query or "").split()).title() or query
        return DisambiguationResult(
            message_key="billing_ai_agent_not_found",
            options=[],
            expected_entity="agent",
            message_data={"name": display},
        )
    if len(agents) > 1:
        return DisambiguationResult(
            message_key="jrh_ai_agent_ambiguous",
            options=[
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "label": item.get("name"),
                    "kind": "agent",
                }
                for item in agents[:8]
            ],
            expected_entity="agent",
        )
    agent = agents[0]
    agent_row = {
        "id": agent.get("id"),
        "name": agent.get("name") or "",
        "kind": "agent",
    }
    charges = resolve_pending_charges(
        organization_id,
        agent["id"],
        currency=entities.get("currency"),
        hint=entities.get("charge_hint") or "",
    )
    if not charges:
        billable = list_billable_agent_charges(
            organization_id,
            agent_id=agent["id"],
        )
        charges = [
            {
                "id": item.get("id"),
                "name": item.get("description") or item.get("period_label") or "",
                "kind": "charge",
                "amount": item.get("gross_amount") or item.get("amount"),
                "currency": item.get("currency"),
                "period_label": item.get("period_label") or "",
                "charge_category": item.get("charge_category") or "",
                "billing_period": item.get("billing_period") or "",
            }
            for item in billable
        ]
    if entities.get("billing_period") or entities.get("amount") or entities.get("currency"):
        ranked = sorted(
            (( _score_charge(item, entities), item) for item in charges),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if ranked:
            top_score = ranked[0][0]
            charges = [
                item
                for score, item in ranked
                if score >= max(top_score - 15, 20)
            ]
            if not charges:
                charges = [item for _score, item in ranked]
            if top_score >= 40 and (len(ranked) == 1 or ranked[1][0] < top_score - 15):
                charges = [ranked[0][1]]
    if not charges:
        return DisambiguationResult(
            message_key="billing_ai_charge_missing",
            options=[],
            expected_entity="charge",
        )
    if len(charges) > 1:
        return DisambiguationResult(
            message_key="jrh_ai_invoice_candidates",
            options=[_charge_option(item, agent_row) for item in charges[:8]],
            expected_entity="charge",
        )
    charge = charges[0]
    try:
        context = get_charge_invoice_context(
            organization_id,
            charge["id"],
            user=user,
        )
    except Exception:
        context = {}
    active = (context or {}).get("active_invoice")
    if active:
        return ExistingInvoiceResult(
            invoice_id=active.get("id"),
            origin_type=ORIGIN_CHARGE,
            label=charge.get("name") or "",
        )
    full_agent = get_agent_record(agent["id"], organization_id) or agent_row
    return ResolvedChargeIntent(
        charge_id=charge["id"],
        agent_id=agent["id"],
        charge=charge,
        agent={"id": full_agent.get("id"), "name": full_agent.get("name") or ""},
    )


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

    entities = parsed.entities or {}
    if entities.get("previous_kind") == "charge" and entities.get("previous_id"):
        from modules.database.agents_repository import get_agent_record

        charge_id = entities["previous_id"]
        try:
            context = get_charge_invoice_context(
                organization_id,
                charge_id,
                user=user,
            )
        except Exception:
            context = None
        if context:
            active = context.get("active_invoice")
            if active:
                return ExistingInvoiceResult(
                    invoice_id=active.get("id"),
                    origin_type=ORIGIN_CHARGE,
                )
            movement = context.get("movement") or {}
            agent = get_agent_record(movement.get("agent_id"), organization_id) or {}
            return ResolvedChargeIntent(
                charge_id=charge_id,
                agent_id=movement.get("agent_id"),
                charge={
                    "id": charge_id,
                    "name": movement.get("description") or "",
                    "amount": movement.get("gross_amount") or movement.get("amount"),
                    "currency": movement.get("currency"),
                    "period_label": movement.get("period_label") or "",
                },
                agent={"id": agent.get("id"), "name": agent.get("name") or ""},
            )

    origin = parsed.origin_type or entities.get("origin_type") or ORIGIN_UNKNOWN
    expected = entities.get("expected_entity") or ""
    if origin == ORIGIN_UNKNOWN and (
        entities.get("charge_hint")
        or entities.get("self")
        or entities.get("billing_period")
        or entities.get("amount")
        or entities.get("generic_agents")
        or expected in {"agent", "charge"}
    ):
        origin = ORIGIN_CHARGE
    if origin == ORIGIN_CHARGE or expected == "agent":
        return _resolve_charges(
            parsed,
            organization_id,
            agent_scope=agent_scope,
            user=user,
        )

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
        reference = (
            entities.get("property_text")
            or entities.get("address")
            or parsed.operation_reference
        )
        operations = _find_operations_by_reference(
            reference,
            organization_id,
            agent_id=agent_scope,
        )

        query = entities.get("agent_name") or ""
        if query:
            agent_matches = _match_agents(
                organization_id,
                query,
                agent_scope=agent_scope,
            )
        else:
            agents = get_agents(organization_id)
            text_folded = _normalize_text(parsed.raw_text)
            agent_matches = [
                item
                for item in agents
                if _agent_name_in_text(item["name"], text_folded)
            ]
        if len(agent_matches) == 1 and operations:
            operations = [
                op
                for op in operations
                if op.get("agent_db_id") == agent_matches[0]["id"]
            ]

    if not operations:
        if expected == "agent" or origin == ORIGIN_CHARGE:
            return _resolve_charges(
                parsed,
                organization_id,
                agent_scope=agent_scope,
                user=user,
            )
        if origin != ORIGIN_OPERATION and (
            entities.get("agent_name") or entities.get("self")
        ):
            return _resolve_charges(
                parsed,
                organization_id,
                agent_scope=agent_scope,
                user=user,
            )
        return DisambiguationResult(
            message_key="billing_ai_operation_not_found",
            options=[],
            expected_entity="operation",
        )

    if len(operations) > 1:
        return DisambiguationResult(
            message_key="billing_ai_disambiguation",
            options=[
                _operation_label(op)
                for op in operations[:8]
            ],
            expected_entity="operation",
        )

    operation = operations[0]
    sides = _available_sides(organization_id, operation["db_id"])
    side = parsed.side
    if side not in VALID_SIDES:
        if len(sides) == 1:
            side = sides[0]
        else:
            return MissingSideResult(
                operation_id=operation["db_id"],
                operation_label=_operation_label(operation)["label"],
            )
    if sides and side not in sides and len(sides) == 1:
        side = sides[0]

    return ResolvedInvoiceIntent(
        operation_id=operation["db_id"],
        side=side,
        operation_display=_operation_label(operation),
    )
