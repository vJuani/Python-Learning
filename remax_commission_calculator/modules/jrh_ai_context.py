"""Short conversational context. Last entity + invoice slots. No long memory."""

from __future__ import annotations

SESSION_KEY = "jrh_ai_context"

EXPECTED_ENTITIES = frozenset(
    {
        "agent",
        "operation",
        "property",
        "charge",
        "side",
        "issuer",
        "billing_period",
        "currency",
    }
)

_PENDING_ENTITY_KEYS = (
    "agent_name",
    "charge_hint",
    "charge_category",
    "billing_period",
    "period",
    "currency",
    "amount",
    "side",
    "origin_type",
    "self",
    "generic_agents",
    "property_text",
    "address",
    "operation_reference",
    "expected_entity",
)


def empty_context():
    return {
        "last_intent": "",
        "last_entity": {},
        "last_prompt": "",
        "pending_invoice": {},
    }


def _sanitize_last_entity(entity):
    if not isinstance(entity, dict):
        return {}
    return {
        "kind": entity.get("kind") or "",
        "id": entity.get("id"),
        "label": entity.get("label") or "",
    }


def sanitize_pending_invoice(pending):
    if not isinstance(pending, dict) or not pending:
        return {}
    expected = str(pending.get("expected_entity") or "").strip()
    if expected and expected not in EXPECTED_ENTITIES:
        expected = ""
    entities = pending.get("entities") if isinstance(pending.get("entities"), dict) else {}
    clean_entities = {}
    for key in _PENDING_ENTITY_KEYS:
        value = entities.get(key)
        if value in (None, ""):
            continue
        if key == "amount":
            try:
                clean_entities[key] = float(value)
            except (TypeError, ValueError):
                continue
        else:
            clean_entities[key] = value
    payload = {
        "intent": pending.get("intent") or "",
        "origin_type": pending.get("origin_type") or "",
        "expected_entity": expected,
        "charge_category": pending.get("charge_category") or "",
        "entities": clean_entities,
    }
    if pending.get("operation_id"):
        payload["operation_id"] = pending.get("operation_id")
    if pending.get("side"):
        payload["side"] = pending.get("side")
    if not payload["expected_entity"] and not payload["origin_type"] and not clean_entities:
        return {}
    return payload


def load_context(session):
    raw = (session or {}).get(SESSION_KEY) or {}
    if not isinstance(raw, dict):
        return empty_context()
    entity = raw.get("last_entity") if isinstance(raw.get("last_entity"), dict) else {}
    return {
        "last_intent": raw.get("last_intent") or "",
        "last_entity": _sanitize_last_entity(entity),
        "last_prompt": raw.get("last_prompt") or "",
        "pending_invoice": sanitize_pending_invoice(raw.get("pending_invoice")),
    }


def store_context(session, *, intent="", entity=None, prompt="", pending_invoice=None):
    if session is None:
        return
    payload = empty_context()
    payload["last_intent"] = intent or ""
    payload["last_prompt"] = (prompt or "")[:200]
    if entity and entity.get("id"):
        payload["last_entity"] = {
            "kind": entity.get("kind") or "",
            "id": entity.get("id"),
            "label": (entity.get("label") or entity.get("name") or "")[:80],
        }
    payload["pending_invoice"] = sanitize_pending_invoice(pending_invoice)
    session[SESSION_KEY] = payload


def clear_context(session):
    if session is not None:
        session.pop(SESSION_KEY, None)
