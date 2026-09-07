"""Short conversational context. Last entity only. No long memory."""

from __future__ import annotations

SESSION_KEY = "jrh_ai_context"


def empty_context():
    return {
        "last_intent": "",
        "last_entity": {},
        "last_prompt": "",
    }


def load_context(session):
    raw = (session or {}).get(SESSION_KEY) or {}
    if not isinstance(raw, dict):
        return empty_context()
    entity = raw.get("last_entity") if isinstance(raw.get("last_entity"), dict) else {}
    return {
        "last_intent": raw.get("last_intent") or "",
        "last_entity": {
            "kind": entity.get("kind") or "",
            "id": entity.get("id"),
            "label": entity.get("label") or "",
        },
        "last_prompt": raw.get("last_prompt") or "",
    }


def store_context(session, *, intent="", entity=None, prompt=""):
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
    session[SESSION_KEY] = payload


def clear_context(session):
    if session is not None:
        session.pop(SESSION_KEY, None)
