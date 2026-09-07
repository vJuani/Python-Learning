"""Deterministic intent signals and property-filter extraction.

Used by the mock provider, as an OpenAI guard, and by the Home router
so “mostrame” never means Agenda by itself.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

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

ORIGIN_CHARGE = "agent_account_charge"
ORIGIN_OPERATION = "operation"
ORIGIN_UNKNOWN = "unknown"
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
        "las", "los", "la", "el", "de", "del", "lo", "una", "un", "hoy", "manana", "mañana",
        "septiembre", "fee", "cuenta", "factura", "pago", "capital",
        "caba", "nunez", "nuñez", "belgrano", "palermo",
        "propiedades", "propiedad", "departamentos", "departamento",
        "casas", "casa", "fee", "jueves", "lunes", "martes",
        "agente", "agentes", "todos", "todas", "mis",
    }
)

GENERIC_AGENT_RE = re.compile(
    r"^(?:de\s+)?(?:"
    r"los\s+agentes|mis\s+agentes|todos(?:\s+los\s+agentes)?|"
    r"un\s+agente|una\s+agente|agentes|agente"
    r")$",
    re.IGNORECASE,
)
NAME_LIKE_RE = re.compile(
    r"^[A-Za-zÁÉÍÓÚÑÜáéíóúñü.'\-]+(?:\s+[A-Za-zÁÉÍÓÚÑÜáéíóúñü.'\-]+){0,3}$"
)
CANCEL_INVOICE_RE = re.compile(
    r"\b(cancelar|cancela|cancelá|olvidalo|olvidá|dejalo|dejá)\b",
    re.IGNORECASE,
)
EXPLICIT_NEW_INTENTS = frozenset(
    {
        QUERY_PENDINGS,
        QUERY_AGENT_ACCOUNT,
        QUERY_AGENDA,
        QUERY_PROPERTIES,
        QUERY_PROPERTY_NEEDS,
        QUERY_OPERATIONS,
        QUERY_INVOICES,
        CREATE_TASK,
        START_AGENT_PAYMENT,
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


def is_generic_agent_reference(text):
    folded = fold_text(text)
    if not folded:
        return False
    return bool(GENERIC_AGENT_RE.match(folded))


def looks_like_person_name(text):
    cleaned = " ".join((text or "").split())
    if not cleaned or is_generic_agent_reference(cleaned):
        return False
    if re.search(r"\d", cleaned):
        return False
    if len(cleaned.split()) > 4:
        return False
    return bool(NAME_LIKE_RE.fullmatch(cleaned))


def _usable_person_name(name):
    parts = [part for part in (name or "").split() if part]
    while parts and fold_text(parts[-1]) in PERSON_STOP:
        parts.pop()
    while parts and fold_text(parts[0]) in PERSON_STOP:
        parts.pop(0)
    name = " ".join(parts)
    folded = fold_text(name)
    if not folded or folded in PERSON_STOP:
        return ""
    if is_generic_agent_reference(name):
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


def extract_invoice_side(text):
    folded = fold_text(text)
    buyer = any(
        token in folded
        for token in (
            "comprador",
            "compradora",
            "buyer",
            "punta compradora",
            "punta comprador",
        )
    )
    seller = any(
        token in folded
        for token in (
            "vendedor",
            "vendedora",
            "seller",
            "punta vendedora",
            "punta vendedor",
        )
    )
    if buyer and not seller:
        return "buyer"
    if seller and not buyer:
        return "seller"
    return None


def normalize_billing_period(text, *, today=None):
    folded = fold_text(text)
    today = today or date.today()
    year = today.year
    year_match = re.search(r"\b(20\d{2})\b", folded)
    if year_match:
        year = int(year_match.group(1))
    slash = re.search(r"\b(\d{1,2})[/-](20\d{2})\b", folded)
    if slash:
        return f"{int(slash.group(2)):04d}-{int(slash.group(1)):02d}"
    if "este mes" in folded:
        return f"{today.year:04d}-{today.month:02d}"
    if "mes pasado" in folded:
        month = today.month - 1 or 12
        year = today.year if today.month > 1 else today.year - 1
        return f"{year:04d}-{month:02d}"
    for index, name in enumerate(MONTH_LABELS_ES, start=1):
        if name in folded:
            return f"{year:04d}-{index:02d}"
    return ""


def detect_invoice_origin(text, entities=None):
    folded = fold_text(text)
    entities = entities or {}
    charge_hits = any(
        token in folded
        for token in (
            "fee",
            "cargo",
            "cuenta corriente",
            "jrh one",
            "lo que tengo",
            "lo pendiente",
            "lo que tenga",
        )
    )
    operation_hits = any(
        token in folded
        for token in (
            "operacion",
            "venta",
            "alquiler",
            "comprador",
            "vendedor",
            "punta",
            "propiedad",
        )
    ) or bool(entities.get("address") or entities.get("property_text"))
    if charge_hits and not operation_hits:
        return ORIGIN_CHARGE
    if operation_hits and not charge_hits:
        return ORIGIN_OPERATION
    if charge_hits:
        return ORIGIN_CHARGE
    if operation_hits:
        return ORIGIN_OPERATION
    if entities.get("amount") and entities.get("agent_name"):
        return ORIGIN_CHARGE
    return ORIGIN_UNKNOWN


def extract_invoice_entities(text, *, context=None, today=None):
    context = context or {}
    last = context.get("last_entity") or {}
    folded = fold_text(text)
    entities = {}
    side = extract_invoice_side(text)
    if side:
        entities["side"] = side
    period = normalize_billing_period(text, today=today)
    if period:
        entities["billing_period"] = period
        entities["period"] = period
    amount = parse_price_amount(text)
    if amount and "hasta" not in folded:
        entities["amount"] = amount
    if any(token in folded for token in ("usd", "dolar", "dolares")):
        entities["currency"] = "USD"
    elif any(token in folded for token in ("ars", "peso", "pesos")):
        entities["currency"] = "ARS"
    if "fee" in folded:
        entities["charge_category"] = "fee"
        entities["charge_hint"] = "fee"
    elif "jrh" in folded:
        entities["charge_category"] = "jrh"
        entities["charge_hint"] = "jrh"
    if is_generic_agent_reference(text) or re.search(
        r"\b(los agentes|mis agentes|todos los agentes|un agente|una agente)\b",
        folded,
    ):
        entities["generic_agents"] = True
    address = re.search(
        r"(libertador|santa fe|cabildo|corrientes|madero|hubac|quesada|fitz roy)(?:\s+\d+)?",
        folded,
    )
    if address:
        entities["property_text"] = address.group(0)
    if re.search(r"\bmi fee\b|\blo que tengo\b|\blo pendiente\b", folded):
        entities["self"] = True
    if last.get("kind") == "operation" and last.get("id") and (
        side or re.search(r"\b(la del|eso|esa)\b", folded)
    ):
        entities["refers_to_previous"] = True
        entities["previous_kind"] = "operation"
        entities["previous_id"] = last.get("id")
        entities["origin_type"] = ORIGIN_OPERATION
    entities["origin_type"] = entities.get("origin_type") or detect_invoice_origin(
        text,
        entities,
    )
    return entities


def extract_entities(text, *, context=None):
    context = context or {}
    last = context.get("last_entity") or {}
    text = normalize_user_text(text)
    folded = fold_text(text)
    entities = extract_property_entities(text)
    entities.update(extract_when(text))
    person = extract_person_name(text)
    if person and not is_generic_agent_reference(person):
        entities["agent_name"] = person
        entities["contact_name"] = person
    invoice = extract_invoice_entities(text, context=context)
    for key, value in invoice.items():
        if value not in (None, ""):
            entities[key] = value
    if "fee" in folded:
        entities["charge_hint"] = "fee"
    period = re.search(
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
        r"septiembre|octubre|noviembre|diciembre|\d{4})",
        folded,
    )
    if period:
        entities["period"] = period.group(1)
    if entities.get("generic_agents"):
        entities.pop("agent_name", None)
        entities.pop("contact_name", None)
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


def apply_expected_entity(entities, text, pending):
    """Fill the slot JRH asked for. Do not re-parse the reply as a new origin."""
    pending = pending or {}
    expected = str(pending.get("expected_entity") or "").strip()
    incoming = dict(entities or {})
    for key, value in (pending.get("entities") or {}).items():
        if incoming.get(key) in (None, "") and value not in (None, ""):
            incoming[key] = value
    if pending.get("origin_type"):
        incoming["origin_type"] = pending["origin_type"]
    category = pending.get("charge_category") or incoming.get("charge_category")
    if category and not incoming.get("charge_category"):
        incoming["charge_category"] = category
        incoming["charge_hint"] = incoming.get("charge_hint") or category
    if expected:
        incoming["expected_entity"] = expected
    raw = " ".join((text or "").split())
    if expected == "agent":
        incoming.pop("generic_agents", None)
        if is_generic_agent_reference(raw):
            incoming.pop("agent_name", None)
            incoming["generic_agents"] = True
        elif not incoming.get("agent_name"):
            person = extract_person_name(raw)
            if not person and looks_like_person_name(raw):
                person = raw
            if person and not is_generic_agent_reference(person):
                incoming["agent_name"] = person
    elif expected == "side":
        side = extract_invoice_side(raw)
        if side:
            incoming["side"] = side
    elif expected == "operation":
        if not incoming.get("property_text"):
            incoming["operation_reference"] = raw
    elif expected == "property":
        incoming["property_text"] = incoming.get("property_text") or raw
    elif expected == "charge":
        incoming["charge_hint"] = incoming.get("charge_hint") or raw
    elif expected == "issuer":
        incoming["issuer"] = raw
    elif expected == "billing_period":
        incoming["billing_period"] = incoming.get("billing_period") or normalize_billing_period(raw)
    elif expected == "currency":
        if not incoming.get("currency"):
            folded = fold_text(raw)
            if any(token in folded for token in ("usd", "dolar", "dolares")):
                incoming["currency"] = "USD"
            elif any(token in folded for token in ("ars", "peso", "pesos")):
                incoming["currency"] = "ARS"
    return incoming


def _has_explicit_intent_change(scores):
    competing = {
        intent: confidence
        for intent, confidence in (scores or {}).items()
        if intent != START_INVOICE
    }
    if not competing:
        return False
    intent, confidence = max(competing.items(), key=lambda item: item[1])
    return intent in EXPLICIT_NEW_INTENTS and confidence >= 0.75


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
            "facturale",
            "quiero facturar",
            "quiero hacer la factura",
            "haceme la factura",
            "haceme la del",
            "la del comprador",
            "la del vendedor",
            "la de usd",
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
        if entities.get("origin_type") == ORIGIN_UNKNOWN and entities.get("side"):
            entities["origin_type"] = ORIGIN_OPERATION
    if START_INVOICE not in scores and not re.search(
        r"\b(debo|debe|deuda|saldo)\b", folded
    ):
        if any(
            phrase in folded
            for phrase in (
                "fee de",
                "el fee",
                "un fee",
                "cargo pendiente",
                "cargos pendientes",
            )
        ):
            scores[START_INVOICE] = 0.86
            if "fee" in folded:
                entities["charge_category"] = entities.get("charge_category") or "fee"
                entities["charge_hint"] = entities.get("charge_hint") or "fee"
                entities["origin_type"] = ORIGIN_CHARGE
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

    pending = (context or {}).get("pending_invoice") or {}
    expected = str(pending.get("expected_entity") or "").strip()
    if expected and CANCEL_INVOICE_RE.search(folded):
        entities["cancel_pending"] = True
        return {
            "intent": FALLBACK,
            "entities": entities,
            "confidence": 0.9,
        }
    if expected and not _has_explicit_intent_change(scores):
        origin_now = entities.get("origin_type") or detect_invoice_origin(text, entities)
        operation_retarget = expected == "agent" and origin_now == ORIGIN_OPERATION and (
            entities.get("property_text") or entities.get("address")
        )
        if not operation_retarget:
            scores[START_INVOICE] = max(scores.get(START_INVOICE, 0), 0.9)
            entities = apply_expected_entity(entities, text, pending)

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


def apply_intent_guards(parsed, prompt, context=None):
    """Keep Agenda/create from stealing inventory language after an LLM pass."""
    result = dict(parsed or {})
    classified = classify_intent(prompt, context=context)
    rule_intent = classified["intent"]
    entities = dict(classified.get("entities") or {})
    incoming = result.get("entities") if isinstance(result.get("entities"), dict) else {}
    merged = dict(entities)
    merged.update({key: value for key, value in incoming.items() if value not in (None, "")})
    result["entities"] = merged
    if rule_intent == START_INVOICE and result.get("intent") in {FALLBACK}:
        result["intent"] = START_INVOICE
        result["confidence"] = max(float(result.get("confidence") or 0), 0.86)
        result["guard"] = "invoice_slot"
    elif rule_intent == QUERY_PROPERTIES and result.get("intent") in {
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
