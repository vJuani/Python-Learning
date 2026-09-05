"""
Structured visit outcome stored in ``agent_tasks.outcome_json``.

New keys sit next to the legacy aliases (``objection``, ``area``,
string ``budget``) so older rows still render.
"""

from __future__ import annotations

import json
import re
import unicodedata


INTERESTS = ("positive", "neutral", "negative")
SEARCH_ACTIONS = ("buscar alternativas", "buscar propiedades")
CURRENCIES = ("USD", "ARS")


def _fold(text):
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).lower()


def _as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _lines(value):
    return [
        part.strip()
        for part in str(value or "").replace(",", "\n").splitlines()
        if part.strip()
    ]


def _parse_amount(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return number if number > 0 else None

    text = str(value).strip()
    if not text:
        return None

    folded = _fold(text)
    mil = re.search(r"(\d[\d\.]*)\s*mil", folded)
    if mil:
        head = mil.group(1).replace(".", "").replace(",", "")
        try:
            return int(float(head)) * 1000
        except ValueError:
            return None

    digits = re.sub(r"[^\d]", "", text)
    if len(digits) < 3:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _normalize_budget(value):
    if value in (None, ""):
        return None

    if isinstance(value, dict):
        minimum = _parse_amount(value.get("min"))
        maximum = _parse_amount(value.get("max") or value.get("amount"))
        currency = str(value.get("currency") or "").strip().upper()
    else:
        minimum = None
        maximum = _parse_amount(value)
        currency = "USD" if _fold(str(value)).find("ars") < 0 else ""
        if "ars" in _fold(str(value)) or "peso" in _fold(str(value)):
            currency = "ARS"
        elif maximum is not None:
            currency = "USD"

    if currency not in CURRENCIES:
        currency = "USD" if maximum is not None or minimum is not None else ""

    if minimum is None and maximum is None:
        return None

    budget = {}
    if minimum is not None:
        budget["min"] = minimum
    if maximum is not None:
        budget["max"] = maximum
    if currency:
        budget["currency"] = currency

    return budget or None


def _normalize_suggested_task(value):
    if not isinstance(value, dict):
        return None

    prompt = str(value.get("prompt") or "").strip()
    task_type = str(value.get("type") or "").strip() or "follow_up"

    if not prompt:
        return None

    return {"type": task_type, "prompt": prompt}


def normalize_visit_outcome(raw):
    """Return the display/save shape. Empty or invalid input is ``{}``."""
    if not raw:
        return {}

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}

    if not isinstance(raw, dict):
        return {}

    interest = str(raw.get("interest") or "").strip()
    if interest not in INTERESTS:
        interest = ""

    objections = _as_list(raw.get("objections"))
    if not objections:
        objections = _as_list(raw.get("objection"))

    areas = _as_list(raw.get("areas"))
    if not areas:
        areas = _as_list(raw.get("area"))

    preferences = _as_list(raw.get("preferences"))
    budget = _normalize_budget(raw.get("budget"))
    next_action = str(raw.get("next_action") or "").strip()
    suggested = _normalize_suggested_task(raw.get("suggested_task"))
    note = str(raw.get("note") or "").strip()

    outcome = {}
    if note:
        outcome["note"] = note
    if interest:
        outcome["interest"] = interest
    if objections:
        outcome["objections"] = objections
        outcome["objection"] = objections[0]
    if areas:
        outcome["areas"] = areas
        outcome["area"] = areas[0]
    if preferences:
        outcome["preferences"] = preferences
    if budget:
        outcome["budget"] = budget
    if next_action:
        outcome["next_action"] = next_action
    if suggested:
        outcome["suggested_task"] = suggested

    return outcome


def outcome_is_present(raw):
    return bool(normalize_visit_outcome(raw))


def is_search_next_action(value):
    return _fold(value or "") in SEARCH_ACTIONS or _fold(value or "").startswith(
        "buscar alternativa"
    )


def format_budget_label(budget):
    data = _normalize_budget(budget)
    if not data or data.get("max") is None:
        return ""

    amount = int(data["max"])
    grouped = f"{amount:,}".replace(",", ".")
    currency = data.get("currency") or "USD"
    return f"{currency} {grouped}"


def outcome_from_form(form):
    raw_json = (form.get("outcome_json") or "").strip()
    base = {}
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except (TypeError, ValueError):
            parsed = {}
        if isinstance(parsed, dict):
            base = parsed

    if form.get("note") is not None:
        base["note"] = form.get("note")
    if form.get("interest"):
        base["interest"] = form.get("interest")
    if form.get("objections") is not None or form.get("objection") is not None:
        base["objections"] = _lines(
            form.get("objections") if form.get("objections") is not None
            else form.get("objection")
        )
    if form.get("areas") is not None or form.get("area") is not None:
        base["areas"] = _lines(
            form.get("areas") if form.get("areas") is not None
            else form.get("area")
        )
    if form.get("preferences") is not None:
        base["preferences"] = _lines(form.get("preferences"))
    if any(
        form.get(key) not in (None, "")
        for key in ("budget_max", "budget_min", "budget_currency", "budget")
    ):
        base["budget"] = {
            "min": form.get("budget_min"),
            "max": form.get("budget_max") or form.get("budget"),
            "currency": form.get("budget_currency"),
        }
    if form.get("next_action") is not None:
        base["next_action"] = form.get("next_action")
    if form.get("suggested_task_prompt"):
        base["suggested_task"] = {
            "type": form.get("suggested_task_type") or "follow_up",
            "prompt": form.get("suggested_task_prompt"),
        }

    return normalize_visit_outcome(base)


def attach_suggested_task(outcome, task):
    """Fill ``suggested_task`` from the note/task when the IA omitted it."""
    data = normalize_visit_outcome(outcome)
    if data.get("suggested_task"):
        return data
    if is_search_next_action(data.get("next_action")):
        return data

    name = str((task or {}).get("contact_name") or "").strip()
    note = data.get("note") or ""
    folded = _fold(note)
    next_action = data.get("next_action") or ""

    if "llamar" in folded or "llamar" in _fold(next_action):
        prompt = f"Llamar a {name}".strip() if name else "Llamar"
        time_match = re.search(
            r"(ma[nñ]ana|hoy|el lunes|el martes|el mi[eé]rcoles|"
            r"el jueves|el viernes|el s[aá]bado|el domingo)"
            r"(?:\s+a\s+las?\s+\d{1,2}(?::\d{2})?)?",
            note,
            re.IGNORECASE,
        )
        if time_match:
            prompt = f"{prompt} {time_match.group(0)}"
        data["suggested_task"] = {"type": "call", "prompt": prompt}
        if not next_action:
            data["next_action"] = "Llamar"
        return data

    if next_action:
        prompt = next_action
        if name:
            prompt = f"{next_action} con {name}"
        data["suggested_task"] = {"type": "follow_up", "prompt": prompt}

    return data


def properties_search_args(outcome):
    data = normalize_visit_outcome(outcome)
    args = {}
    areas = data.get("areas") or []
    if areas:
        args["q"] = areas[0]
    budget = data.get("budget") or {}
    if budget.get("max"):
        args["max_price"] = str(budget["max"])
    return args
