"""
Local natural-language parser for the agent agenda.

The OpenAI provider is optional. These functions always work without
an API key so tests, voice transcripts and typed prompts stay
deterministic. The structured shape matches ``validate_task_payload``.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta


TASK_TYPE_HINTS = (
    ("visit", ("visita", "visitar", "mostrame", "mostrar", "ir a ver", "vamos a ver")),
    ("call", ("llamar", "llamada", "llamen", "llamo")),
    ("meeting", ("reunion", "reunión", "meeting")),
    ("follow_up", ("seguimiento", "seguir")),
    ("documentation", ("documentacion", "documentación", "papeles")),
    ("valuation", ("tasacion", "tasación", "tasar")),
    ("reminder", ("recordame", "recordar", "recordatorio")),
)

WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}

DEFAULT_DURATION = {
    "visit": 60,
    "call": 15,
    "meeting": 45,
    "follow_up": 15,
    "documentation": 30,
    "valuation": 45,
    "reminder": 5,
    "other": 30,
}

_TIME_RE = re.compile(
    r"(?:a\s+las?\s+|las\s+)(\d{1,2})(?:[:h\.](\d{2}))?"
    r"|(\d{1,2})[:h](\d{2})"
    r"|(\d{1,2})\s*(?:hs|hrs)\b",
    re.IGNORECASE,
)

TYPE_TITLES = {
    "visit": "Visita",
    "call": "Llamada",
    "meeting": "Reunión",
    "follow_up": "Seguimiento",
    "documentation": "Documentación",
    "valuation": "Tasación",
    "reminder": "Recordatorio",
    "other": "Seguimiento",
}
_PERSON_RE = re.compile(
    r"\b(?:con|a)\s+([A-Za-zÁÉÍÓÚÑÜáéíóúñü]+"
    r"(?:\s+[A-Za-zÁÉÍÓÚÑÜáéíóúñü]+)?)",
)
_PROPERTY_RE = re.compile(
    r"(?:para|por|en|de)\s+"
    r"((?:av\.?|avenida|calle)?\s*"
    r"[A-ZÁÉÍÓÚÑÜ][\wÁÉÍÓÚÑÜáéíóúñü\.]+"
    r"(?:\s+\d{1,5})?)",
    re.IGNORECASE,
)


def _fold(text):
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).lower()


def detect_task_type(text):
    folded = _fold(text)

    for task_type, hints in TASK_TYPE_HINTS:
        if any(hint in folded for hint in hints):
            return task_type

    return "other"


def _next_weekday(today, weekday):
    delta = (weekday - today.weekday()) % 7

    return today + timedelta(days=delta or 7)


def parse_relative_date(text, *, today):
    folded = _fold(text)

    if "pasado manana" in folded or "pasado mañana" in folded:
        return today + timedelta(days=2)
    if "manana" in folded:
        return today + timedelta(days=1)
    if re.search(r"\bhoy\b", folded):
        return today

    for name, weekday in WEEKDAYS.items():
        if name in folded:
            return _next_weekday(today, weekday)

    return None


def parse_time(text):
    folded = _fold(text)
    afternoon = any(
        token in folded
        for token in ("tarde", "pm")
    )
    match = _TIME_RE.search(text or "")

    if match is None:
        return None

    hour = int(next(group for group in match.groups()[0:6:2] if group))
    minute_raw = next(
        (group for group in match.groups()[1:6:2] if group),
        "0",
    )
    minute = int(minute_raw)

    if afternoon and hour <= 12:
        hour += 12 if hour < 12 else 0

    if hour > 23:
        hour = 23

    return f"{hour:02d}:{minute:02d}"


def parse_person(text):
    skipped = {
        "las", "la", "el", "los", "una", "un", "visita", "llamada",
        "propiedad", "casa", "depto", "mi", "me", "para", "por",
        "en", "de", "que", "tengo", "ver",
        *WEEKDAYS.keys(),
    }

    for match in _PERSON_RE.finditer(text or ""):
        parts = [
            part
            for part in match.group(1).split()
            if _fold(part) not in skipped
        ]

        if not parts:
            continue

        return " ".join(parts)

    return ""


def parse_property_query(text):
    match = _PROPERTY_RE.search(text or "")

    if match is None:
        return ""

    return re.sub(r"\s+", " ", match.group(1)).strip(" .,")


def build_task_title(task_type, contact_name, property_query, prompt=None):
    label = TYPE_TITLES.get(task_type) or TYPE_TITLES["other"]
    name = (contact_name or "").strip()
    place = re.sub(r"\s+", " ", (property_query or "").strip()).strip(" .,")
    parts = [label]

    if name:
        parts[0] = f"{label} con {name}"
    if place:
        parts.append(place)

    return " · ".join(parts)


def parse_agenda_prompt(prompt, *, today=None, now_local=None):
    """
    Turn a Spanish sentence into a task draft.

    ``today``/``now_local`` are organization-local so "mañana a las 16"
    is never interpreted in UTC.
    """
    prompt = (prompt or "").strip()
    now_local = now_local or datetime.now()
    today = today or now_local.date()
    task_type = detect_task_type(prompt)
    contact_name = parse_person(prompt)
    property_query = parse_property_query(prompt)
    due_date = parse_relative_date(prompt, today=today)
    due_time = parse_time(prompt)

    return {
        "title": build_task_title(
            task_type,
            contact_name,
            property_query,
            prompt,
        ),
        "task_type": task_type,
        "priority": "normal",
        "due_date": due_date.isoformat() if due_date else "",
        "due_time": due_time or "",
        "date_found": due_date is not None,
        "time_found": due_time is not None,
        "contact_name": contact_name,
        "property_query": property_query,
        "description": prompt,
        "duration_minutes": DEFAULT_DURATION.get(task_type, 30),
        "reminder_minutes": 15 if task_type == "visit" else None,
        "attendance_status": (
            "pending_confirmation" if task_type == "visit" else None
        ),
        "source_prompt": prompt,
    }


def parse_visit_outcome(text):
    """Extract a structured post-visit summary from free text."""
    raw = (text or "").strip()
    folded = _fold(raw)
    interest = "neutral"

    if any(token in folded for token in ("positivo", "interesad", "le gusto", "le gustó")):
        interest = "positive"
    elif any(token in folded for token in ("negativo", "no le gusto", "no va")):
        interest = "negative"

    objection = ""
    objection_match = re.search(
        r"(?:objeci[oó]n[:\s]+|pero\s+|el\s+)(.{8,80})",
        raw,
        re.IGNORECASE,
    )
    if "chico" in folded or "pequeño" in folded or "pequeno" in folded:
        objection = "El segundo dormitorio es chico."
    elif objection_match:
        objection = objection_match.group(1).strip(" .")

    area = ""
    area_match = re.search(
        r"(?:zona|en|por)\s+([A-ZÁÉÍÓÚÑÜ][\wÁÉÍÓÚÑÜáéíóúñü]+)",
        raw,
    )
    if area_match:
        area = area_match.group(1)

    budget = ""
    budget_match = re.search(
        r"(?:usd|u\$s|us\$|presupuesto)?\s*([\d\.]{3,})",
        folded,
        re.IGNORECASE,
    )
    if budget_match:
        budget = budget_match.group(1)

    next_action = "Seguimiento"
    if "alternativa" in folded or "buscar" in folded:
        next_action = "Buscar alternativas"

    return {
        "note": raw,
        "interest": interest,
        "objection": objection,
        "area": area,
        "budget": budget,
        "next_action": next_action,
    }
