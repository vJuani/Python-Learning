"""Deterministic intent signals and property-filter extraction.

Used by the mock provider, as an OpenAI guard, and by the Home router
so “mostrame” never means Agenda by itself.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import timedelta

from modules.jrh_ai_intents import (
    ALL_INTENTS,
    CREATE_TASK,
    FALLBACK,
    LOW_CONFIDENCE,
    QUERY_AGENDA,
    QUERY_AGENT_ACCOUNT,
    QUERY_INVOICES,
    QUERY_OPERATIONS,
    QUERY_PENDINGS,
    QUERY_PROPERTIES,
    QUERY_PROPERTY_NEEDS,
    START_AGENT_PAYMENT,
    START_INVOICE,
)
from modules.property_types import (
    COMMERCIAL_STATUS_AVAILABLE,
    normalize_listing_purpose,
    normalize_property_type,
)
from modules.validators import JURISDICTIONS


def fold_text(text):
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).lower()


WEEKDAYS = (
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo",
)

CABA_ALIASES = frozenset(
    {
        "capital",
        "caba",
        "capital federal",
        "ciudad de buenos aires",
        "cap fed",
    }
)

ZONA_NORTE_ALIASES = frozenset({"zona norte", "zona nte"})
ZONA_NORTE_NEIGHBORHOODS = frozenset(
    {
        "nunez",
        "nuñez",
        "belgrano",
        "colegiales",
        "saavedra",
        "vicente lopez",
        "olivos",
        "martinez",
        "san isidro",
        "florida",
    }
)

NEIGHBORHOOD_ALIASES = {
    "nunez": "Núñez",
    "nuñez": "Núñez",
    "palermo": "Palermo",
    "belgrano": "Belgrano",
    "recoleta": "Recoleta",
    "caballito": "Caballito",
    "colegiales": "Colegiales",
    "saavedra": "Saavedra",
}

CREATE_TASK_RE = re.compile(
    r"\b(agendame|anotame|programame|recordame|creame|agregame|"
    r"poneme|agendar|anotar|programar|agrega|crea)\b"
)
WEEKDAY_INDEX = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}
MONTH_LABELS_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
WEEKDAY_LABELS_ES = (
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo",
)
PRICE_RE = re.compile(
    r"(?:hasta\s+)?(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(mil|k|lucas)?"
    r"|(?:hasta\s+)(\d{1,3}(?:[.,]\d{3})+|\d+)",
    re.IGNORECASE,
)
PERSON_RE = re.compile(
    r"\b(?:para|de|a|con)\s+([A-Za-zÁÉÍÓÚÑÜáéíóúñü]{3,}"
    r"(?:\s+[A-Za-zÁÉÍÓÚÑÜáéíóúñü]{2,})?)",
    re.IGNORECASE,
)
PERSON_STOP = frozenset(
    {
        "las", "los", "la", "el", "de", "del", "una", "un", "hoy", "manana", "mañana",
        "septiembre", "fee", "cuenta", "factura", "pago", "capital",
        "caba", "nunez", "nuñez", "belgrano", "palermo",
        "propiedades", "propiedad", "departamentos", "departamento",
        "casas", "casa", "fee", "jueves", "lunes", "martes",
    }
)


def normalize_user_text(text):
    cleaned = (text or "").strip()
    cleaned = re.sub(r"\bq\b", "que", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\blo que\b", "lo que", cleaned, flags=re.IGNORECASE)
    return cleaned


def _tokens(folded):
    return set(re.findall(r"[a-z0-9]+", folded or ""))


def normalize_location(location_text):
    """Map spoken location to real property filters. Never invent matches."""
    raw = " ".join(str(location_text or "").split())
    folded = fold_text(raw)
    if not folded:
        return {}
    if folded in CABA_ALIASES or folded in {item.lower() for item in JURISDICTIONS if item == "CABA"}:
        return {
            "location_text": raw,
            "jurisdiction": "CABA",
            "neighborhood": None,
            "location_group": None,
        }
    if folded in ZONA_NORTE_ALIASES:
        return {
            "location_text": raw,
            "jurisdiction": None,
            "neighborhood": None,
            "location_group": "zona_norte",
        }
    neighborhood = NEIGHBORHOOD_ALIASES.get(folded)
    if neighborhood:
        return {
            "location_text": raw,
            "jurisdiction": None,
            "neighborhood": neighborhood,
            "location_group": None,
        }
    if folded in {item.lower() for item in JURISDICTIONS}:
        return {
            "location_text": raw,
            "jurisdiction": next(
                item for item in JURISDICTIONS if item.lower() == folded
            ),
            "neighborhood": None,
            "location_group": None,
        }
    return {
        "location_text": raw,
        "jurisdiction": None,
        "neighborhood": raw,
        "location_group": None,
    }


def location_group_matches(neighborhood, group):
    if group != "zona_norte":
        return True
    return fold_text(neighborhood) in ZONA_NORTE_NEIGHBORHOODS


def parse_price_amount(text):
    folded = fold_text(text)
    match = PRICE_RE.search(folded)
    if not match:
        return None
    raw = match.group(1) or match.group(3)
    suffix = (match.group(2) or "").lower()
    if raw is None:
        return None
    compact = raw.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", compact):
        number = float(compact.replace(".", ""))
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+", compact):
        number = float(compact.replace(",", ""))
    else:
        number = float(compact.replace(",", "."))
    if suffix in {"mil", "k", "lucas"}:
        number *= 1000
    return number


def _usable_person_name(name):
    folded = fold_text(name)
    if not folded or folded in PERSON_STOP:
        return ""
    if folded in CABA_ALIASES or folded in NEIGHBORHOOD_ALIASES:
        return ""
    return name


def extract_person_name(text):
    after_debe = re.search(
        r"\bdebe\s+([A-Za-zÁÉÍÓÚÑÜáéíóúñü]{3,})"
        r"(?:\s+([A-Za-zÁÉÍÓÚÑÜáéíóúñü]{2,}))?",
        text or "",
        re.IGNORECASE,
    )
    if after_debe:
        first = after_debe.group(1).strip()
        second = (after_debe.group(2) or "").strip()
        if second and fold_text(second) not in PERSON_STOP:
            name = _usable_person_name(f"{first} {second}")
        else:
            name = _usable_person_name(first)
        if name:
            return name
    for match in PERSON_RE.finditer(text or ""):
        name = _usable_person_name(match.group(1).strip())
        if name:
            return name
    return ""


def _has_location_signal(folded):
    if any(alias in folded for alias in CABA_ALIASES):
        return True
    if any(alias in folded for alias in ZONA_NORTE_ALIASES):
        return True
    return any(
        re.search(rf"\b{re.escape(alias)}\b", folded)
        for alias in NEIGHBORHOOD_ALIASES
    )


def has_property_inventory_signal(text):
    folded = fold_text(text)
    tokens = _tokens(folded)
    inventory_nouns = tokens & {
        "propiedad",
        "propiedades",
        "depto",
        "deptos",
        "departamento",
        "departamentos",
        "casa",
        "casas",
        "alquiler",
        "alquileres",
        "publicado",
        "publicadas",
        "disponible",
        "disponibles",
        "inventario",
        "listado",
    }
    show_verbs = tokens & {
        "mostrame",
        "mostrar",
        "buscame",
        "buscar",
        "hay",
        "tenes",
        "tenemos",
        "cuales",
    }
    phrases = (
        "lo disponible",
        "que propiedades",
        "que tenemos",
        "que tenemos publicado",
        "propiedades disponibles",
        "cuales hay",
        "hasta ",
    )
    if inventory_nouns:
        return True
    if "hay algo" in folded:
        return bool(
            _has_location_signal(folded)
            or parse_price_amount(text)
            or inventory_nouns
        )
    if show_verbs and any(phrase in folded for phrase in phrases):
        return True
    if "lo disponible" in folded:
        return True
    return False


def has_property_need_signal(text):
    folded = fold_text(text)
    return any(
        phrase in folded
        for phrase in ("que tengo para", "propiedades para", "algo para")
    ) and not has_create_task_signal(text)


def _has_weekday(folded):
    return any(day in folded for day in WEEKDAYS)


def _looks_like_agenda_query(folded):
    if "pendiente" in folded:
        return False
    if CREATE_TASK_RE.search(folded):
        return False
    if any(token in folded for token in ("facturame", "facturar", "factura")):
        return False
    phrases = (
        "que tengo hoy",
        "que tengo manana",
        "visitas tengo",
        "visita tengo",
        "tengo agendado",
        "que tengo agendado",
        "que hay agendado",
        "hay agendado",
        "mi agenda",
        "mostrame mi agenda",
        "agenda de hoy",
        "agenda de manana",
        "tengo algo",
        "que visitas",
        "estoy ocupado",
        "como estoy",
        "reservado",
    )
    if any(phrase in folded for phrase in phrases):
        return True
    if _has_weekday(folded) and any(
        token in folded
        for token in ("tengo", "hay", "ocupado", "agendado", "reservado", "agenda")
    ):
        return True
    return False


def has_agenda_query_signal(text):
    folded = fold_text(text)
    if has_property_need_signal(text):
        return False
    if has_property_inventory_signal(text) and "agenda" not in folded:
        return False
    if _looks_like_agenda_query(folded):
        return True
    return (
        "agenda" in folded
        and not CREATE_TASK_RE.search(folded)
        and not has_property_inventory_signal(text)
    )


def has_create_task_signal(text):
    folded = fold_text(text)
    if _looks_like_agenda_query(folded):
        return False
    if has_property_inventory_signal(text) and not CREATE_TASK_RE.search(folded):
        return False
    return bool(CREATE_TASK_RE.search(folded))


def extract_property_entities(text):
    folded = fold_text(text)
    entities = {}
    if any(token in folded for token in ("disponible", "publicado", "lo disponible")):
        entities["availability"] = COMMERCIAL_STATUS_AVAILABLE
    if has_property_inventory_signal(text) and "disponib" in folded:
        entities["availability"] = COMMERCIAL_STATUS_AVAILABLE

    if any(token in folded for token in ("alquiler", "alquileres", "alquilar")):
        entities["operation_type"] = "rental"
        entities["listing_purpose"] = normalize_listing_purpose("rental")
    elif any(token in folded for token in ("venta", "vender", "comprar")):
        entities["operation_type"] = "sale"
        entities["listing_purpose"] = normalize_listing_purpose("sale")

    if any(token in folded for token in ("depto", "deptos", "departamento", "departamentos")):
        entities["property_type"] = normalize_property_type("departamento")
    elif any(token in folded for token in ("casa", "casas")):
        entities["property_type"] = normalize_property_type("casa")
    elif "ph" in _tokens(folded):
        entities["property_type"] = "ph"

    location_text = None
    for alias in list(CABA_ALIASES) + list(ZONA_NORTE_ALIASES):
        if alias in folded:
            location_text = alias
            break
    if location_text is None:
        for alias, label in NEIGHBORHOOD_ALIASES.items():
            if re.search(rf"\b{re.escape(alias)}\b", folded):
                location_text = label
                break
    if location_text:
        entities["location"] = location_text
        entities.update(
            {
                key: value
                for key, value in normalize_location(location_text).items()
                if value
            }
        )

    rooms = re.search(r"(\d)\s*ambientes?", folded)
    if rooms:
        entities["rooms"] = int(rooms.group(1))
    bedrooms = re.search(r"(\d)\s*(?:dormitorios?|habitaciones?)", folded)
    if bedrooms:
        entities["bedrooms"] = int(bedrooms.group(1))
    bathrooms = re.search(r"(\d)\s*banos?", folded)
    if bathrooms:
        entities["bathrooms"] = int(bathrooms.group(1))
    area = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:m2|metros)", folded)
    if area:
        entities["min_area"] = float(area.group(1).replace(",", "."))

    price = parse_price_amount(text)
    if price:
        entities["max_price"] = price
    if "lucas" in folded and not any(
        token in folded for token in ("usd", "dolar", "ars", "peso")
    ):
        entities["currency_ambiguous"] = True
    elif any(token in folded for token in ("usd", "dolar")):
        entities["currency"] = "USD"
    elif "ars" in folded or "peso" in folded:
        entities["currency"] = "ARS"

    if any(token in folded for token in ("cochera", "garage", "estacionamiento")):
        entities["parking"] = True
    if "balcon" in folded:
        entities["balcony"] = True
    if "terraza" in folded:
        entities["terrace"] = True
    if "jardin" in folded:
        entities["garden"] = True

    address = re.search(
        r"(libertador|santa fe|cabildo|corrientes|madero)(?:\s+\d+)?",
        folded,
    )
    if address:
        entities["address"] = address.group(0)
    return entities


def extract_when(text):
    folded = fold_text(text)
    entities = {}
    if "esta semana" in folded:
        entities["when"] = "this_week"
        return entities
    if "pasado manana" in folded:
        entities["when"] = "day_after_tomorrow"
        return entities
    weekday = next((day for day in WEEKDAYS if day in folded), None)
    if weekday:
        entities["weekday"] = weekday
        entities["weekday_mode"] = (
            "next" if re.search(r"\bproxim[oa]\b", folded) else "upcoming"
        )
        if re.search(r"\b(a|por) la manana\b", folded):
            entities["time_hint"] = "morning"
        elif "tarde" in folded:
            entities["time_hint"] = "afternoon"
        return entities
    if "manana" in folded:
        entities["when"] = "tomorrow"
        return entities
    if re.search(r"\bhoy\b", folded):
        entities["when"] = "today"
    return entities


def resolve_agenda_date(entities, now):
    """Resolve relative agenda language to local calendar dates."""
    today = now.date()
    when = (entities or {}).get("when")
    weekday = (entities or {}).get("weekday")
    mode = (entities or {}).get("weekday_mode") or "upcoming"
    if when == "this_week":
        end = today + timedelta(days=(6 - today.weekday()))
        return {"start": today, "end": end, "when": "this_week"}
    if when == "day_after_tomorrow":
        day = today + timedelta(days=2)
        return {"start": day, "end": day, "when": "day_after_tomorrow"}
    if when == "tomorrow":
        day = today + timedelta(days=1)
        return {"start": day, "end": day, "when": "tomorrow"}
    if when == "today":
        return {"start": today, "end": today, "when": "today"}
    if weekday and weekday in WEEKDAY_INDEX:
        delta = (WEEKDAY_INDEX[weekday] - today.weekday()) % 7
        if mode == "next" and delta == 0:
            delta = 7
        day = today + timedelta(days=delta)
        return {"start": day, "end": day, "when": weekday}
    return {"start": today, "end": today, "when": "today"}


def format_agenda_day_label(day, language="es"):
    if language != "es":
        return day.strftime("%A %d %B")
    return (
        f"{WEEKDAY_LABELS_ES[day.weekday()]} {day.day} "
        f"de {MONTH_LABELS_ES[day.month - 1]}"
    )


def extract_entities(text, *, context=None):
    context = context or {}
    last = context.get("last_entity") or {}
    text = normalize_user_text(text)
    folded = fold_text(text)
    entities = extract_property_entities(text)
    entities.update(extract_when(text))
    person = extract_person_name(text)
    if person:
        entities["agent_name"] = person
        entities["contact_name"] = person
    if "fee" in folded:
        entities["charge_hint"] = "fee"
    period = re.search(
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
        r"septiembre|octubre|noviembre|diciembre|\d{4})",
        folded,
    )
    if period:
        entities["period"] = period.group(1)
    pronoun = bool(
        re.search(
            r"\b(lo|la|eso|esa|este|esta|el cargo|facturamelo|facturamel[oa])\b",
            folded,
        )
    )
    if pronoun and last.get("kind") and last.get("id"):
        entities["refers_to_previous"] = True
        entities["previous_kind"] = last.get("kind")
        entities["previous_id"] = last.get("id")
    return entities


def classify_intent(prompt, *, context=None):
    text = normalize_user_text(prompt)
    folded = fold_text(text)
    entities = extract_entities(text, context=context)
    scores = {}

    if any(
        phrase in folded
        for phrase in (
            "registrame este pago",
            "registrar pago",
            "registrar un pago",
        )
    ):
        scores[START_AGENT_PAYMENT] = 0.9
    if any(
        phrase in folded
        for phrase in (
            "facturame",
            "quiero facturar",
            "quiero hacer la factura",
            "haceme la factura",
        )
    ) or re.search(r"\bfactur[aá]\b", folded):
        scores[START_INVOICE] = 0.88
        if "fee" in folded:
            scores[START_INVOICE] = 0.93
        if any(
            phrase in folded
            for phrase in ("lo que tengo", "lo que tenga", "lo pendiente")
        ) and not entities.get("agent_name"):
            entities["self"] = True
    if any(
        phrase in folded
        for phrase in ("que debe", "saldo de", "cuenta de", "cuanto debe")
    ):
        scores[QUERY_AGENT_ACCOUNT] = 0.94
    if (
        "mi cuenta" in folded
        or "que debo" in folded
        or re.search(r"\bdebo\b", folded)
    ):
        scores[QUERY_AGENT_ACCOUNT] = 0.92
        if not entities.get("agent_name"):
            entities["self"] = True
    if "fee" in folded and re.search(r"\b(debo|debe)\b", folded):
        scores[QUERY_AGENT_ACCOUNT] = 0.95
    if any(
        phrase in folded
        for phrase in ("que tengo pendiente", "tengo pendiente", "pendientes")
    ):
        scores[QUERY_PENDINGS] = 0.95
    if has_agenda_query_signal(text):
        scores[QUERY_AGENDA] = 0.92
    if has_property_need_signal(text):
        scores[QUERY_PROPERTY_NEEDS] = 0.9
    if has_property_inventory_signal(text) and not has_property_need_signal(text):
        scores[QUERY_PROPERTIES] = 0.9
        if entities.get("availability") or entities.get("jurisdiction") or entities.get("neighborhood"):
            scores[QUERY_PROPERTIES] = 0.94
    if any(
        phrase in folded
        for phrase in ("operacion de", "buscame la operacion")
    ):
        scores[QUERY_OPERATIONS] = 0.88
    if "facturas pendientes" in folded or "facturas tengo" in folded:
        scores[QUERY_INVOICES] = 0.9
    if has_create_task_signal(text):
        scores[CREATE_TASK] = 0.88
        entities["title"] = text

    if (
        not scores
        and entities.get("refers_to_previous")
        and entities.get("previous_kind") == "charge"
    ):
        scores[START_INVOICE] = 0.8

    if not scores:
        return {
            "intent": FALLBACK,
            "entities": entities,
            "confidence": 0.25,
        }

    intent, confidence = max(scores.items(), key=lambda item: item[1])
    if confidence < LOW_CONFIDENCE:
        return {
            "intent": FALLBACK,
            "entities": entities,
            "confidence": confidence,
        }
    return {
        "intent": intent if intent in ALL_INTENTS else FALLBACK,
        "entities": entities,
        "confidence": confidence,
    }


def apply_intent_guards(parsed, prompt):
    """Keep Agenda/create from stealing inventory language after an LLM pass."""
    result = dict(parsed or {})
    classified = classify_intent(prompt, context={"last_entity": {}})
    rule_intent = classified["intent"]
    entities = dict(classified.get("entities") or {})
    incoming = result.get("entities") if isinstance(result.get("entities"), dict) else {}
    merged = dict(entities)
    merged.update({key: value for key, value in incoming.items() if value not in (None, "")})
    result["entities"] = merged
    if rule_intent == QUERY_PROPERTIES and result.get("intent") in {
        QUERY_AGENDA,
        CREATE_TASK,
        FALLBACK,
    }:
        result["intent"] = QUERY_PROPERTIES
        result["confidence"] = max(float(result.get("confidence") or 0), 0.86)
        result["guard"] = "property_inventory"
    elif rule_intent == QUERY_AGENDA and result.get("intent") in {
        CREATE_TASK,
        FALLBACK,
    }:
        result["intent"] = QUERY_AGENDA
        result["confidence"] = max(float(result.get("confidence") or 0), 0.86)
        result["guard"] = "agenda_query"
    elif rule_intent == QUERY_AGENT_ACCOUNT and result.get("intent") in {
        FALLBACK,
        START_INVOICE,
    } and not has_create_task_signal(prompt):
        if "factur" not in fold_text(prompt):
            result["intent"] = QUERY_AGENT_ACCOUNT
            result["guard"] = "account_query"
    elif rule_intent == FALLBACK and result.get("intent") in {QUERY_AGENDA, CREATE_TASK}:
        if not has_create_task_signal(prompt) and not has_agenda_query_signal(prompt):
            result["intent"] = FALLBACK
            result["confidence"] = min(float(result.get("confidence") or 0), 0.4)
            result["guard"] = "no_agenda_default"
    return result
