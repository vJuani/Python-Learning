"""Deterministic intent signals and property-filter extraction.

Used by the mock provider, as an OpenAI guard, and by the Home router
so “mostrame” never means Agenda by itself.
"""

from __future__ import annotations

import re
import unicodedata

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
    r"\b(agendame|anotame|programame|recordame|agendar|anotar|programar)\b"
)
CREATE_EVENT_RE = re.compile(
    r"\b(visita|visitar|llamar|llamada|llamo|reunion|meeting)\b"
)
TIME_RE = re.compile(r"\b(a las|las)\s+\d{1,2}(?:[:h.]\d{2})?\b|\b\d{1,2}\s*(?:hs|hrs)\b")
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
        "las", "los", "la", "el", "una", "un", "hoy", "manana", "mañana",
        "septiembre", "fee", "cuenta", "factura", "pago", "capital",
        "caba", "nunez", "nuñez", "belgrano", "palermo",
        "propiedades", "propiedad", "departamentos", "departamento",
        "casas", "casa",
    }
)


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


def extract_person_name(text):
    match = PERSON_RE.search(text or "")
    if not match:
        after_debe = re.search(
            r"\bdebe\s+([A-Za-zÁÉÍÓÚÑÜáéíóúñü]{3,}"
            r"(?:\s+[A-Za-zÁÉÍÓÚÑÜáéíóúñü]{2,})?)",
            text or "",
            re.IGNORECASE,
        )
        if not after_debe:
            return ""
        name = after_debe.group(1).strip()
    else:
        name = match.group(1).strip()
    if fold_text(name) in PERSON_STOP:
        return ""
    if fold_text(name) in CABA_ALIASES or fold_text(name) in NEIGHBORHOOD_ALIASES:
        return ""
    return name


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
    }
    phrases = (
        "lo disponible",
        "que propiedades",
        "que tenemos",
        "que tenemos publicado",
        "propiedades disponibles",
        "hay algo",
        "hasta ",
    )
    if inventory_nouns:
        return True
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


def _looks_like_agenda_query(folded):
    if "pendiente" in folded:
        return False
    return any(
        phrase in folded
        for phrase in (
            "que tengo hoy",
            "que tengo manana",
            "visitas tengo",
            "visita tengo",
            "tengo agendado",
            "que tengo agendado",
            "mi agenda",
            "mostrame mi agenda",
            "agenda de hoy",
            "agenda de manana",
            "tengo algo el",
        )
    )


def has_agenda_query_signal(text):
    folded = fold_text(text)
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
    if CREATE_TASK_RE.search(folded):
        return True
    if CREATE_EVENT_RE.search(folded) and (
        "manana" in folded
        or re.search(r"\bhoy\b", folded)
        or any(day in folded for day in WEEKDAYS)
        or TIME_RE.search(folded)
    ):
        return True
    return False


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

    if "manana" in folded:
        entities["when"] = "tomorrow"
    elif re.search(r"\bhoy\b", folded):
        entities["when"] = "today"

    address = re.search(
        r"(libertador|santa fe|cabildo|corrientes|madero)(?:\s+\d+)?",
        folded,
    )
    if address:
        entities["address"] = address.group(0)
    return entities


def extract_entities(text, *, context=None):
    context = context or {}
    last = context.get("last_entity") or {}
    folded = fold_text(text)
    entities = extract_property_entities(text)
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
    text = (prompt or "").strip()
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
        for phrase in ("que debe", "saldo de", "cuenta de", "cuanto debe")
    ):
        scores[QUERY_AGENT_ACCOUNT] = 0.94
    if "mi cuenta" in folded or "que debo" in folded:
        scores[QUERY_AGENT_ACCOUNT] = 0.9
        entities["self"] = True
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
    elif rule_intent == QUERY_AGENDA and result.get("intent") == CREATE_TASK:
        result["intent"] = QUERY_AGENDA
        result["guard"] = "agenda_query"
    elif rule_intent == FALLBACK and result.get("intent") in {QUERY_AGENDA, CREATE_TASK}:
        if not has_create_task_signal(prompt) and not has_agenda_query_signal(prompt):
            result["intent"] = FALLBACK
            result["confidence"] = min(float(result.get("confidence") or 0), 0.4)
            result["guard"] = "no_agenda_default"
    return result
