"""Conversational Marketing IA: intent, context, and in-thread generation."""

from __future__ import annotations

import logging
import re
import unicodedata

from modules.auth import is_admin, scoped_agent_id
from modules.database.marketing_conversations_repository import (
    add_marketing_message,
    create_conversation_folder,
    create_marketing_conversation,
    delete_conversation_folder,
    delete_marketing_conversation,
    get_conversation_folder,
    get_marketing_conversation,
    last_generation_id_for_conversation,
    list_conversation_folders,
    list_marketing_conversations,
    list_marketing_messages,
    update_conversation_folder,
    update_marketing_conversation,
)
from modules.database.marketing_generations_repository import (
    get_marketing_generation,
    update_marketing_generation,
)
from modules.database.properties_repository import get_properties, get_property_record
from modules.database.tenant import require_organization_id
from modules.i18n import translate
from modules.marketing_context import MarketingError, assert_marketing_access
from modules.marketing_chat_decisions import (
    action_from_channel,
    apply_intelligent_defaults,
    content_type_from_channel,
    conversation_context,
    determine_missing_decisions,
    generation_style_tone,
    listing_label,
    looks_like_decision_reply,
    parse_prompt_decisions,
    persistable_context,
    question_spec,
    resolve_context,
    should_clarify,
)
from modules.marketing_generation_service import (
    STYLES,
    TONES,
    create_and_run_generation,
    regenerate_generation,
)


logger = logging.getLogger(__name__)

CONTENT_ALIASES = (
    ("carrusel", "carousel", None),
    ("carousel", "carousel", None),
    ("historia", "story", None),
    ("stories", "story", None),
    ("story", "story", None),
    ("whatsapp", "whatsapp", None),
    (" wpp", "whatsapp", None),
    ("reel", "story", "reel"),
    ("flyer", "post", "flyer"),
    ("folleto", "post", "flyer"),
    ("publicacion", "post", None),
    ("publicidad", "post", None),
    ("instagram", "post", None),
    ("post", "post", None),
)
OBJECTIVE_ALIASES = (
    ("captar", "capture_owner"),
    ("dueno", "capture_owner"),
    ("dueño", "capture_owner"),
    ("propietario", "capture_owner"),
    ("alquiler", "rent_property"),
    ("alquilar", "rent_property"),
    ("renta", "rent_property"),
    ("marca personal", "personal_brand"),
    ("consulta", "generate_leads"),
    ("lead", "generate_leads"),
    ("vender", "sell_property"),
    ("venta", "sell_property"),
)
STYLE_ALIASES = (
    ("premium", "premium"),
    ("punch", "dynamic"),
    ("impacto", "dynamic"),
    ("comercial", "commercial"),
    ("moderno", "modern"),
    ("modern", "modern"),
    ("minimal", "minimal"),
    ("elegante", "elegant"),
    ("dinamico", "dynamic"),
    ("corporativo", "corporate"),
)
TONE_ALIASES = (
    ("vendedor", "commercial"),
    ("comercial", "commercial"),
    ("formal", "formal"),
    ("cercano", "close"),
    ("aspiracional", "aspirational"),
    ("exclusivo", "exclusive"),
)
TEMPLATE_ALIASES = (
    ("modern commercial", "modern_commercial_v2"),
    ("comercial moderno", "modern_commercial_v2"),
    ("premium editorial", "premium_editorial_v2"),
    ("social punch", "social_punch_v2"),
    ("modern premium", "modern_premium_v1"),
    ("moderno premium", "modern_premium_v1"),
    ("minimal", "minimal_v1"),
)
REVISION_RE = re.compile(
    r"\b("
    r"cambia|cambiame|cambiale|regener|otra version|mas moderna|mas modern|"
    r"saca |sacale|usa otra|usala|hace otra|ahora hace|con esto|"
    r"pone la|el titulo|el titular|el cta|segunda foto|quita "
    r")\b"
)
CHAT_RE = re.compile(
    r"\b("
    r"ideas?|opciones?|escrib(i|ime)|write me|dame \d|"
    r"recomend|ayudame|ayuda con|mejorame|brainstorm|"
    r"solo (el )?copy|solo texto|opciones de titulos?|hashtags?|captions?"
    r")\b"
)
IMAGE_RE = re.compile(
    r"\b("
    r"imagen|pieza|publicidad|flyer|folleto|"
    r"hacelo imagen|convert\w*(?:lo)?(?: en| a)? imagen"
    r")\b"
)
GENERATE_VISUAL_RE = re.compile(
    r"\b("
    r"hacelo imagen|"
    r"generame la imagen|"
    r"creame la pieza|"
    r"armame la publicidad|"
    r"haceme el flyer|"
    r"convert\w*(?:lo)?(?: esto| la opcion \d+)?(?: en| a)(?: una)? (?:publicacion|imagen|pieza)|"
    r"hace(?:me)? una historia"
    r")\b"
)
FORMAT_RE = re.compile(
    r"\b(publicacion|historia|historias|carrusel|carousel|reel|instagram|\bposts?\b)\b"
)
CREATE_RE = re.compile(
    r"\b(haceme|creame|armame|generame|quiero una|necesito una|convert|hacelo)\b"
)
FORMAT_LABELS = {
    "post": "marketing_ia_format_post",
    "story": "marketing_ia_format_story",
    "carousel": "marketing_ia_format_carousel",
    "whatsapp": "marketing_ia_format_whatsapp",
    "copy": "marketing_ia_format_copy",
    "flyer": "marketing_ia_format_flyer",
    "reel": "marketing_ia_format_reel",
}
ACTION_FORMATS = {
    "generate_post_image": ["post"],
    "generate_story_image": ["story"],
    "generate_carousel": ["post", "story"],
    "generate_reel_storyboard": ["story"],
    "generate_visual": ["post"],
}
VISUAL_ACTIONS = frozenset(ACTION_FORMATS) - {"generate_visual"}
CHAT_SYSTEM_PROMPT = (
    "Sos JRH IA, el asistente creativo de un agente inmobiliario. "
    "Respondé en el idioma del usuario, con ideas concretas y accionables. "
    "Si conviene una pieza visual, ofrecé generar la imagen. "
    "No inventes datos de una propiedad que no te pasaron."
)


def _fold(text):
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip().lower()


def _has_usable_copy(generation):
    if not generation or (generation.get("status") or "") == "failed":
        return False
    data = generation.get("generated_data") or {}
    if data.get("copy_status") == "failed":
        return False
    if generation.get("generated_copy"):
        return True
    return bool(
        data.get("headline")
        or data.get("caption")
        or data.get("message")
        or data.get("body")
        or data.get("frames")
        or data.get("slides")
    )


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


def can_view_conversation(user, conversation):
    if not user or not conversation:
        return False
    if conversation.get("organization_id") != user.get("organization_id"):
        return False
    return conversation.get("user_id") == user.get("id")


def require_conversation(organization_id, user, conversation_id):
    organization_id = require_organization_id(organization_id)
    conversation = get_marketing_conversation(conversation_id, organization_id)
    if conversation is None:
        raise MarketingError("marketing_ia_err_conversation", 404)
    if not can_view_conversation(user, conversation):
        raise MarketingError("access_denied", 403)
    return conversation


def _workspace_groups(conversations, folders):
    pinned = []
    recent = []
    folder_items = {item["id"]: {**item, "conversations": []} for item in folders}
    for item in conversations:
        if item.get("is_archived"):
            continue
        if item.get("is_pinned"):
            pinned.append(item)
            continue
        folder_id = item.get("folder_id")
        if folder_id and folder_id in folder_items:
            folder_items[folder_id]["conversations"].append(item)
        else:
            recent.append(item)
    return {
        "pinned": pinned,
        "folders": [item for item in folder_items.values()],
        "recent": recent,
    }


def pin_conversation(organization_id, user, conversation_id):
    conversation = require_conversation(organization_id, user, conversation_id)
    return update_marketing_conversation(
        conversation["id"],
        organization_id,
        is_pinned=not conversation.get("is_pinned"),
        touch=False,
    )


def rename_conversation(organization_id, user, conversation_id, title):
    conversation = require_conversation(organization_id, user, conversation_id)
    label = " ".join(str(title or "").split())
    if not label:
        raise MarketingError("marketing_ia_err_rename", 400)
    return update_marketing_conversation(
        conversation["id"],
        organization_id,
        title=label[:80],
        touch=False,
    )


def archive_conversation(organization_id, user, conversation_id):
    conversation = require_conversation(organization_id, user, conversation_id)
    return update_marketing_conversation(
        conversation["id"],
        organization_id,
        is_archived=not conversation.get("is_archived"),
        is_pinned=False if not conversation.get("is_archived") else conversation.get("is_pinned"),
        touch=False,
    )


def delete_conversation(organization_id, user, conversation_id):
    conversation = require_conversation(organization_id, user, conversation_id)
    return delete_marketing_conversation(conversation["id"], organization_id)


def move_conversation(organization_id, user, conversation_id, folder_id=None):
    conversation = require_conversation(organization_id, user, conversation_id)
    if folder_id:
        folder = get_conversation_folder(
            folder_id, organization_id, user_id=user.get("id")
        )
        if folder is None:
            raise MarketingError("marketing_ia_err_folder", 404)
        return update_marketing_conversation(
            conversation["id"],
            organization_id,
            folder_id=folder["id"],
            touch=False,
        )
    return update_marketing_conversation(
        conversation["id"],
        organization_id,
        clear_folder=True,
        touch=False,
    )


def create_folder(organization_id, user, name):
    folder = create_conversation_folder(
        organization_id, user_id=user.get("id"), name=name
    )
    if folder is None:
        raise MarketingError("marketing_ia_err_folder_name", 400)
    return folder


def rename_folder(organization_id, user, folder_id, name):
    folder = update_conversation_folder(
        folder_id, organization_id, user_id=user.get("id"), name=name
    )
    if folder is None:
        raise MarketingError("marketing_ia_err_folder", 404)
    return folder


def delete_folder(organization_id, user, folder_id):
    folder = delete_conversation_folder(
        folder_id, organization_id, user_id=user.get("id")
    )
    if folder is None:
        raise MarketingError("marketing_ia_err_folder", 404)
    return folder


_PROPERTY_NOUN_RE = re.compile(
    r"\b(?:la |el |las |los )?(?:propiedades|propiedad|departamentos|departamento|"
    r"depto|dpto|casas|casa|ph|lotes|lote|locales|local|inmuebles|inmueble|"
    r"listings|listing|duplex|oficinas|oficina)s?"
    r"(?:\s+que\s+tengo)?"
    r"(?:\s+(?:de\s+la|del|de|en|sobre|llamada|llamado))?"
    r"\s+(?P<ref>.+)"
)
_QUERY_STOPWORDS = frozenset(
    {
        "haceme",
        "creame",
        "armame",
        "generame",
        "quiero",
        "necesito",
        "hacelo",
        "convert",
        "converti",
        "una",
        "un",
        "unas",
        "unos",
        "la",
        "el",
        "las",
        "los",
        "de",
        "del",
        "en",
        "para",
        "por",
        "con",
        "que",
        "tengo",
        "esta",
        "este",
        "eso",
        "esa",
        "propiedad",
        "propiedades",
        "publicacion",
        "publicaciones",
        "publicidad",
        "pieza",
        "flyer",
        "folleto",
        "historia",
        "historias",
        "carrusel",
        "carousel",
        "reel",
        "instagram",
        "post",
        "posts",
        "imagen",
        "copy",
        "whatsapp",
        "vender",
        "venderlo",
        "venderla",
        "venta",
        "alquilar",
        "alquiler",
        "premium",
        "moderno",
        "moderna",
        "minimal",
        "minimalista",
        "elegante",
        "comercial",
        "datos",
        "foto",
        "fotos",
        "mis",
        "mi",
        "tu",
        "tus",
        "su",
        "sus",
        "me",
        "te",
        "vos",
        "solo",
        "inmobiliaria",
        "oficina",
        "marca",
        "otra",
        "otro",
        "otras",
        "otros",
        "ahora",
        "luego",
        "despues",
        "después",
        "tambien",
        "también",
        "version",
        "versión",
        "primera",
        "version",
        "llamada",
        "llamado",
        "depto",
        "departamento",
        "dpto",
        "casa",
        "ph",
        "lote",
        "local",
        "inmueble",
        "listing",
    }
)
_ABBREVIATIONS = {
    "av": "avenida",
    "avda": "avenida",
    "pje": "pasaje",
    "sta": "santa",
    "sto": "santo",
    "dpto": "departamento",
    "depto": "departamento",
    "dto": "departamento",
}
_MIN_PROPERTY_SCORE = 18
_CLEAR_PROPERTY_GAP = 12
_CLEAR_MARKETING_GAP = 16
_EQUIV_MARKETING_GAP = 10
_INACTIVE_COMMERCIAL = frozenset(
    {
        "sold",
        "rented",
        "reserved",
        "unavailable",
        "vendido",
        "alquilado",
        "reservado",
        "no_disponible",
        "inactive",
        "archived",
    }
)
LISTING_ACTIONS = VISUAL_ACTIONS | {"generate_visual", "generate_whatsapp"}


def _tokens(text):
    return [part for part in re.split(r"[^\w]+", text or "") if part]


def _as_property_id(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _find_property_by_id(properties, property_id):
    target = _as_property_id(property_id)
    if target is None or not properties:
        return None
    for item in properties:
        if _as_property_id(item.get("id")) == target:
            return item
    return None


def _expand_token(token):
    return _ABBREVIATIONS.get(token, token)


def _normalize_search(text):
    folded = _fold(text)
    parts = [_expand_token(token) for token in _tokens(folded)]
    return " ".join(part for part in parts if part)


def extract_property_query(prompt):
    folded = _fold(prompt)
    if not folded:
        return None
    match = _PROPERTY_NOUN_RE.search(folded)
    raw = match.group("ref") if match else folded
    tokens = [
        _expand_token(token)
        for token in _tokens(raw)
        if token not in _QUERY_STOPWORDS and len(token) >= 2
    ]
    query = " ".join(tokens).strip()
    return query or None


def _property_search_fields(item):
    try:
        from modules.property_detail_view import compact_property_title

        title = compact_property_title(item) or ""
    except Exception:
        title = ""
    return {
        "address": _normalize_search(item.get("address") or ""),
        "formatted": _normalize_search(item.get("formatted_address") or ""),
        "title": _normalize_search(item.get("title") or title),
        "compact": _normalize_search(title),
        "locality": _normalize_search(item.get("locality") or ""),
        "neighborhood": _normalize_search(item.get("neighborhood") or ""),
        "jurisdiction": _normalize_search(
            item.get("jurisdiction") or item.get("administrative_area") or ""
        ),
        "external_id": _normalize_search(item.get("external_id") or ""),
        "description": _normalize_search((item.get("description") or "")[:400]),
        "property_type": _normalize_search(item.get("property_type") or ""),
    }


def _score_property_query(item, query):
    query = _normalize_search(query)
    if not query:
        return 0
    fields = _property_search_fields(item)
    haystack = " ".join(value for value in fields.values() if value)
    query_tokens = [token for token in _tokens(query) if len(token) >= 2]
    score = 0
    address = fields["address"]
    if query == address or query == fields["compact"]:
        score += 100
    elif address.startswith(query) or query in address or query in fields["formatted"]:
        score += 80
    elif fields["compact"] and query in fields["compact"]:
        score += 75
    elif fields["title"] and query in fields["title"]:
        score += 70
    if query in {fields["locality"], fields["neighborhood"], fields["jurisdiction"]} and query:
        score += 55
    elif query and (
        query in fields["locality"]
        or query in fields["neighborhood"]
        or query in fields["jurisdiction"]
    ):
        score += 40
    if fields["external_id"] and query == fields["external_id"]:
        score += 90
    if query_tokens and address and all(token in address for token in query_tokens):
        score = max(score, 72)
    token_hits = sum(1 for token in query_tokens if token in haystack)
    score += token_hits * 10
    digits = [token for token in query_tokens if token.isdigit()]
    if digits and any(token in address for token in digits):
        score += 18
    if fields["description"] and query in fields["description"] and score < _MIN_PROPERTY_SCORE:
        score += 16
    return score


def _photo_count(item):
    if not item:
        return 0
    raw = item.get("photo_count")
    if raw not in (None, ""):
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    photos = item.get("photos")
    if isinstance(photos, list):
        return len(photos)
    if item.get("has_photos") is False:
        return 0
    if item.get("has_photos") or item.get("has_cover") or item.get("cover_url"):
        return 1
    return 0


def _has_listing_photos(item):
    return _photo_count(item) > 0


def _log_property_trace(event, **fields):
    parts = [f"{key}={fields[key]}" for key in sorted(fields) if fields[key] is not None]
    logger.info("%s %s", event, " ".join(parts))


def _is_publishable_listing(item):
    commercial = _fold(str((item or {}).get("commercial_status") or ""))
    if commercial in _INACTIVE_COMMERCIAL:
        return False
    status = _fold(str((item or {}).get("status") or ""))
    if status in {"archived", "inactive", "draft", "rejected"}:
        return False
    return True


def _completeness_score(item):
    item = item or {}
    score = 0
    if item.get("address") or item.get("title"):
        score += 4
    if item.get("locality") or item.get("neighborhood") or item.get("jurisdiction"):
        score += 3
    if item.get("rooms") not in (None, ""):
        score += 3
    price = item.get("listing_price")
    if price not in (None, "", 0, "0"):
        score += 4
    if item.get("property_type") or item.get("type_label"):
        score += 2
    if item.get("description"):
        score += 2
    if item.get("bedrooms") not in (None, "") or item.get("bathrooms") not in (None, ""):
        score += 2
    return score


def listing_marketing_score(item, query):
    query_score = _score_property_query(item, query)
    photos = _photo_count(item)
    score = query_score
    if photos:
        score += 45 + min(photos, 10) * 2
    else:
        score -= 30
    if _is_publishable_listing(item):
        score += 14
    else:
        score -= 10
    if query_score >= 80:
        score += 10
    elif query_score >= 70:
        score += 4
    return score + _completeness_score(item)


def _equivalent_listings(left, right, query):
    if _has_listing_photos(left) != _has_listing_photos(right):
        return False
    if _is_publishable_listing(left) != _is_publishable_listing(right):
        return False
    if abs(_photo_count(left) - _photo_count(right)) > 2:
        return False
    query_gap = abs(_score_property_query(left, query) - _score_property_query(right, query))
    market_gap = abs(listing_marketing_score(left, query) - listing_marketing_score(right, query))
    complete_gap = abs(_completeness_score(left) - _completeness_score(right))
    return query_gap < 12 and market_gap < _EQUIV_MARKETING_GAP and complete_gap <= 4


def _compact_property_choice(item):
    return {
        "id": item.get("id"),
        "address": item.get("address") or item.get("title") or "",
        "locality": item.get("neighborhood")
        or item.get("locality")
        or item.get("jurisdiction")
        or "",
        "rooms": item.get("rooms"),
        "listing_price": item.get("listing_price"),
        "listing_currency": item.get("listing_currency"),
        "cover_url": item.get("cover_url"),
        "photo_count": _photo_count(item),
        "property_type": item.get("property_type"),
    }


def resolve_property_from_prompt(prompt, properties):
    query = extract_property_query(prompt)
    empty = {
        "query": query,
        "status": "unset",
        "property": None,
        "matches": [],
        "auto_picked": False,
    }
    if not query or not properties:
        return empty
    ranked = []
    for item in properties:
        query_score = _score_property_query(item, query)
        if query_score >= _MIN_PROPERTY_SCORE:
            ranked.append((listing_marketing_score(item, query), query_score, item))
    ranked.sort(
        key=lambda pair: (
            pair[0],
            pair[1],
            _photo_count(pair[2]),
            pair[2].get("id") or 0,
        ),
        reverse=True,
    )
    matches = [item for _market, _query, item in ranked]
    if not matches:
        return {**empty, "status": "none", "query": query}
    if len(matches) == 1:
        return {
            "query": query,
            "status": "resolved",
            "property": matches[0],
            "matches": matches[:8],
            "auto_picked": False,
        }
    top_market, top_query, top = ranked[0]
    next_market, next_query, nxt = ranked[1]
    photo_split = _has_listing_photos(top) and not _has_listing_photos(nxt)
    if photo_split or (
        not _equivalent_listings(top, nxt, query)
        and (
            (top_market - next_market) >= _CLEAR_MARKETING_GAP
            or (top_query - next_query) >= _CLEAR_PROPERTY_GAP
        )
    ):
        return {
            "query": query,
            "status": "resolved",
            "property": top,
            "matches": matches[:8],
            "auto_picked": True,
        }
    return {
        "query": query,
        "status": "ambiguous",
        "property": None,
        "matches": matches[:8],
        "auto_picked": False,
    }


def match_property_from_prompt(prompt, properties):
    resolved = resolve_property_from_prompt(prompt, properties)
    if resolved.get("status") == "resolved":
        return resolved.get("property")
    return None


def _should_search_property(folded, query):
    if not query:
        return False
    if _PROPERTY_NOUN_RE.search(folded):
        return True
    if any(token.isdigit() for token in _tokens(query)):
        return True
    if REVISION_RE.search(folded) and not CREATE_RE.search(folded) and not IMAGE_RE.search(folded):
        return False
    exclusive_chat = bool(
        CHAT_RE.search(folded) and not CREATE_RE.search(folded) and not IMAGE_RE.search(folded)
    )
    if exclusive_chat:
        return False
    return bool(
        CREATE_RE.search(folded)
        or IMAGE_RE.search(folded)
        or FORMAT_RE.search(folded)
        or 1 <= len(_tokens(query)) <= 4
    )


def _wants_listing_property(action, origin):
    if action in {"chat", "edit_existing_generation"}:
        return False
    if origin in {"personal_brand", "office"}:
        return False
    return action in LISTING_ACTIONS or origin == "property"


def _action_from_type(content_type, requested_format):
    if requested_format == "flyer":
        return "generate_post_image"
    if requested_format == "reel":
        return "generate_reel_storyboard"
    if content_type == "story":
        return "generate_story_image"
    if content_type == "carousel":
        return "generate_carousel"
    if content_type == "whatsapp":
        return "generate_whatsapp"
    if content_type == "post":
        return "generate_post_image"
    return "chat"


def _resolve_action(folded, content_type, requested_format, last_generation, preferred_mode):
    chat_hit = bool(CHAT_RE.search(folded))
    visual_hit = bool(IMAGE_RE.search(folded) or FORMAT_RE.search(folded))
    image_hit = bool(IMAGE_RE.search(folded))
    create_hit = bool(CREATE_RE.search(folded))
    exclusive_chat = chat_hit and not create_hit and not image_hit
    last_type = (last_generation or {}).get("content_type")
    format_switch = bool(
        content_type in ("post", "story", "carousel", "whatsapp")
        and last_type
        and content_type != last_type
    )

    if preferred_mode == "chat" and not image_hit and not create_hit and not REVISION_RE.search(folded):
        return "chat"
    if preferred_mode == "create" and not exclusive_chat:
        mapped = _action_from_type(content_type, requested_format)
        return mapped if mapped != "chat" else "generate_post_image"

    if last_generation and not format_switch and REVISION_RE.search(folded):
        return "edit_existing_generation"

    if exclusive_chat:
        return "chat"

    if (
        last_generation
        and not format_switch
        and _has_usable_copy(last_generation)
        and not REVISION_RE.search(folded)
        and (
            GENERATE_VISUAL_RE.search(folded)
            or (image_hit and len(folded) < 80)
        )
    ):
        return "generate_visual"

    if visual_hit or create_hit or image_hit:
        mapped = _action_from_type(content_type, requested_format)
        if mapped != "chat":
            return mapped
        if visual_hit or create_hit or image_hit:
            return "generate_post_image"

    if content_type == "whatsapp":
        return "generate_whatsapp"
    return "chat"


def interpret_prompt(
    prompt,
    *,
    conversation=None,
    last_generation=None,
    property_id=None,
    properties=None,
    preferred_mode="auto",
):
    folded = _fold(prompt)
    decisions = parse_prompt_decisions(prompt)
    content_type = None
    requested_format = None
    for needle, mapped, display in CONTENT_ALIASES:
        if needle in folded:
            content_type = mapped
            requested_format = display
            break
    objective = None
    for needle, mapped in OBJECTIVE_ALIASES:
        if needle in folded:
            objective = mapped
            break
    style = None
    for needle, mapped in STYLE_ALIASES:
        if needle in folded:
            style = mapped
            break
    tone = None
    for needle, mapped in TONE_ALIASES:
        if needle in folded:
            tone = mapped
            break
    template = None
    for needle, mapped in TEMPLATE_ALIASES:
        if needle in folded:
            template = mapped
            break
    origin = None
    if "marca personal" in folded:
        origin = "personal_brand"
        content_type = content_type or "copy"
        objective = objective or "personal_brand"
    elif objective == "capture_owner" and not property_id:
        origin = "office"
        content_type = content_type or "post"

    composer_property_id = _as_property_id(property_id)
    conversation_property_id = _as_property_id((conversation or {}).get("property_id"))
    last_property_id = _as_property_id((last_generation or {}).get("property_id"))
    property_id = composer_property_id
    query = extract_property_query(prompt)
    resolution = {
        "query": query,
        "status": "unset",
        "property": None,
        "matches": [],
        "auto_picked": False,
    }
    matched = None
    current_listing = _find_property_by_id(properties, conversation_property_id)
    last_listing = _find_property_by_id(properties, last_property_id)
    if composer_property_id:
        resolution["status"] = "selected"
        matched = _find_property_by_id(properties, composer_property_id)
        if matched:
            resolution["property"] = matched
            resolution["matches"] = [matched]
    elif conversation_property_id and current_listing:
        # Pinned conversation listing wins: never re-resolve twins by address/title.
        query_matches_pinned = (
            not query
            or _score_property_query(current_listing, query) >= _MIN_PROPERTY_SCORE
        )
        if query_matches_pinned or not _should_search_property(folded, query):
            resolution = {
                "query": query,
                "status": "inherited",
                "property": current_listing,
                "matches": [current_listing],
                "auto_picked": False,
            }
            matched = current_listing
            property_id = conversation_property_id
        elif _should_search_property(folded, query):
            resolution = resolve_property_from_prompt(prompt, properties)
            if resolution.get("status") == "resolved":
                matched = resolution.get("property")
                property_id = _as_property_id((matched or {}).get("id"))
                reason = "has_photos" if resolution.get("auto_picked") else "unique_match"
                _log_property_trace(
                    "marketing_property_resolved",
                    property_id=property_id,
                    reason=reason,
                    score=listing_marketing_score(matched, query) if matched else None,
                    photo_count=_photo_count(matched),
                    auto_picked=int(bool(resolution.get("auto_picked"))),
                )
            elif resolution.get("status") in {"none", "ambiguous"}:
                property_id = None
    elif last_property_id and last_listing and not _should_search_property(folded, query):
        resolution = {
            "query": query,
            "status": "inherited",
            "property": last_listing,
            "matches": [last_listing],
            "auto_picked": False,
        }
        matched = last_listing
        property_id = last_property_id
    elif _should_search_property(folded, query):
        resolution = resolve_property_from_prompt(prompt, properties)
        if resolution.get("status") == "resolved":
            matched = resolution.get("property")
            property_id = _as_property_id((matched or {}).get("id"))
            reason = "has_photos" if resolution.get("auto_picked") else "unique_match"
            _log_property_trace(
                "marketing_property_resolved",
                property_id=property_id,
                reason=reason,
                score=listing_marketing_score(matched, query) if matched else None,
                photo_count=_photo_count(matched),
                auto_picked=int(bool(resolution.get("auto_picked"))),
            )
        elif resolution.get("status") in {"none", "ambiguous"}:
            property_id = None
    explicit_listing_ref = bool(_PROPERTY_NOUN_RE.search(folded)) or any(
        token.isdigit() for token in _tokens(resolution.get("query") or "")
    )
    if (
        not property_id
        and conversation_property_id
        and resolution.get("status") != "ambiguous"
        and not (resolution.get("status") == "none" and explicit_listing_ref)
    ):
        property_id = conversation_property_id
        resolution["status"] = "inherited"
        matched = matched or current_listing

    revising = bool(last_generation) and bool(
        REVISION_RE.search(folded) or content_type or len(folded) < 90
    )
    if last_generation and revising:
        content_type = content_type or last_generation.get("content_type")
        requested_format = requested_format or (last_generation.get("generated_data") or {}).get(
            "requested_format"
        )
        style = style or last_generation.get("style")
        tone = tone or last_generation.get("tone")
        objective = objective or last_generation.get("objective")
        origin = origin or last_generation.get("origin")
        template = template or last_generation.get("format")
        if not property_id and resolution.get("status") != "ambiguous" and not (
            resolution.get("status") == "none" and explicit_listing_ref
        ):
            property_id = last_property_id
            if property_id and resolution.get("status") in {"unset", "none"}:
                resolution["status"] = "inherited"
                matched = matched or last_listing

    property_id = _as_property_id(property_id)

    if origin is None:
        if property_id:
            origin = "property"
        elif objective == "personal_brand":
            origin = "personal_brand"
        else:
            origin = "free"
    content_type = content_type or (
        "post"
        if origin == "property"
        and (CREATE_RE.search(folded) or IMAGE_RE.search(folded) or FORMAT_RE.search(folded))
        else "copy"
    )
    needs_property = False
    parent_id = last_generation["id"] if revising and last_generation else None
    action = _resolve_action(
        folded,
        content_type,
        requested_format,
        last_generation,
        (preferred_mode or "auto").strip().lower(),
    )
    if action in {"edit_existing_generation", "generate_visual"}:
        parent_id = last_generation["id"] if last_generation else parent_id
    if action == "chat":
        needs_property = False
    elif _wants_listing_property(action, origin) and not property_id:
        if resolution.get("status") == "ambiguous":
            needs_property = False
        elif resolution.get("status") == "none":
            needs_property = False
        else:
            needs_property = True
            origin = "property"
    elif property_id:
        origin = origin if origin in {"personal_brand", "office"} else "property"
    return {
        "action": action,
        "content_type": content_type,
        "origin": origin,
        "objective": objective,
        "style": style if style in STYLES else None,
        "tone": tone if tone in TONES else None,
        "format": template,
        "requested_format": requested_format,
        "property_id": property_id,
        "matched_property": matched,
        "property_query": resolution.get("query"),
        "property_status": resolution.get("status") or "unset",
        "property_auto_picked": bool(resolution.get("auto_picked")),
        "property_matches": [
            _compact_property_choice(item) for item in (resolution.get("matches") or [])
        ],
        "revising": action == "edit_existing_generation" or (
            bool(parent_id) and action != "generate_visual"
        ),
        "parent_generation_id": parent_id,
        "needs_property": needs_property,
        "include_agent": decisions.get("include_agent"),
        "channel": decisions.get("channel"),
        "channel_explicit": bool(decisions.get("channel_explicit")),
        "creative_style": decisions.get("style"),
    }


def _conversation_title(prompt, property_row):
    address = (property_row or {}).get("address") if property_row else None
    if address:
        return address[:80]
    text = re.sub(r"\s+", " ", (prompt or "").strip())
    if len(text) <= 42:
        return text or None
    return text[:41].rstrip() + "…"


def _ack_key(intent, *, needs_property=False):
    if needs_property:
        return "marketing_ia_need_property"
    if intent.get("action") == "chat":
        return None
    if intent.get("action") == "generate_visual":
        return "marketing_ia_ack_visual"
    if intent.get("revising") or intent.get("action") == "edit_existing_generation":
        return "marketing_ia_ack_revise"
    return "marketing_ia_ack_visual"


def _picker_properties(organization_id, user):
    rows = get_properties(organization_id, agent_id=scoped_agent_id(user))
    try:
        from modules.property_sync.media import (
            get_property_media_url,
            is_displayable_media,
            list_property_media_for_properties,
            pick_cover_media,
        )

        grouped = {}
        for item in list_property_media_for_properties(
            organization_id,
            [row["id"] for row in rows if row.get("id")],
        ):
            property_id = _as_property_id(item.get("property_id"))
            if property_id is None:
                continue
            if not is_displayable_media(item):
                continue
            grouped.setdefault(property_id, []).append(item)
        for row in rows:
            property_id = _as_property_id(row.get("id"))
            media = grouped.get(property_id, []) if property_id is not None else []
            row["photo_count"] = len(media)
            row["has_photos"] = bool(media)
            cover = pick_cover_media(media)
            row["cover_url"] = get_property_media_url(cover, row.get("id"))
            row["has_cover"] = bool(cover)
    except Exception:
        for row in rows:
            row.setdefault("cover_url", None)
            row.setdefault("photo_count", 0)
            row.setdefault("has_photos", False)
    return rows


def _resolve_property(organization_id, user, property_id):
    property_id = _as_property_id(property_id)
    if not property_id:
        return None
    record = get_property_record(property_id, organization_id)
    if record is None:
        raise MarketingError("marketing_ia_err_property", 404)
    assert_marketing_access(user, record)
    return record


def _hydrate_messages(organization_id, messages, *, properties=None):
    covers = {
        item.get("id"): item.get("cover_url")
        for item in properties or []
        if item.get("id")
    }
    hydrated = []
    for message in messages:
        item = dict(message)
        generation = None
        if item.get("generation_id"):
            generation = get_marketing_generation(item["generation_id"], organization_id)
        if generation:
            display = (item.get("metadata") or {}).get("requested_format") or generation.get(
                "content_type"
            )
            generation = dict(generation)
            generation["display_format"] = display
            generation["format_label_key"] = FORMAT_LABELS.get(
                display, FORMAT_LABELS.get(generation.get("content_type"), "marketing_ia_format_copy")
            )
            generation["cover_url"] = covers.get(generation.get("property_id"))
            data = generation.get("generated_data") or {}
            asset_ids = list(data.get("visual_asset_ids") or [])
            if data.get("visual_asset_id") and data["visual_asset_id"] not in asset_ids:
                asset_ids.insert(0, data["visual_asset_id"])
            generation["visual_assets"] = [
                {
                    "id": asset_id,
                    "preview_url": f"/marketing/assets/{asset_id}/preview",
                    "download_url": f"/marketing/assets/{asset_id}/download.png",
                }
                for asset_id in asset_ids
            ]
            generation["visual_preview_url"] = (
                generation["visual_assets"][0]["preview_url"] if generation["visual_assets"] else None
            )
        item["generation"] = generation
        hydrated.append(item)
    return hydrated


def build_chat_workspace(
    organization_id,
    user,
    *,
    conversation_id=None,
    language="es",
):
    organization_id = require_organization_id(organization_id)
    conversations = list_marketing_conversations(organization_id, user_id=user.get("id"))
    folders = list_conversation_folders(organization_id, user_id=user.get("id"))
    groups = _workspace_groups(conversations, folders)
    properties = _picker_properties(organization_id, user)
    conversation = None
    messages = []
    selected_property = None
    if conversation_id:
        conversation = require_conversation(organization_id, user, conversation_id)
        messages = _hydrate_messages(
            organization_id,
            list_marketing_messages(conversation["id"], organization_id),
            properties=properties,
        )
        if conversation.get("property_id"):
            selected_property = _resolve_property(
                organization_id, user, conversation["property_id"]
            )
    if selected_property:
        for row in properties:
            if row.get("id") == selected_property.get("id"):
                selected_property = {**row, **selected_property}
                break
    return {
        "conversations": conversations,
        "pinned": groups["pinned"],
        "folders": groups["folders"],
        "recent": groups["recent"],
        "conversation": conversation,
        "messages": messages,
        "properties": properties,
        "selected_property": selected_property,
        "language": language,
        "is_admin": is_admin(user),
    }


def _visual_formats(intent, last_generation=None):
    action = intent.get("action")
    if action in {"edit_existing_generation", "generate_visual"}:
        last_action = ((last_generation or {}).get("generated_data") or {}).get("action")
        if last_action in ACTION_FORMATS and last_action != "generate_visual":
            return ACTION_FORMATS[last_action]
        fmt = (
            intent.get("requested_format")
            or intent.get("content_type")
            or (last_generation or {}).get("content_type")
            or "post"
        )
        if fmt == "story":
            return ["story"]
        if fmt == "flyer":
            return ["flyer"]
        return ["post"]
    if intent.get("requested_format") == "flyer":
        return ["flyer"]
    return ACTION_FORMATS.get(action) or ["post"]


def _copy_override_from_generation(generation):
    data = (generation or {}).get("generated_data") or {}
    headline = data.get("headline") or ""
    caption = data.get("caption") or data.get("body") or (generation or {}).get("generated_copy") or ""
    cta = data.get("cta") or ""
    frames = data.get("frames") or []
    if frames and isinstance(frames, list):
        headline = headline or (frames[0] or {}).get("headline") or ""
        caption = caption or (frames[0] or {}).get("body") or ""
        cta = cta or (frames[-1] or {}).get("cta") or ""
    return {
        "headline": headline,
        "subheadline": data.get("subheadline") or "",
        "description": caption,
        "cta": cta,
        "caption": caption,
        "hashtags": data.get("hashtags") or [],
    }


def _patch_generation_data(generation, **fields):
    data = dict(generation.get("generated_data") or {})
    data.update(fields)
    return update_marketing_generation(
        generation["id"],
        generation["organization_id"],
        generated_data=data,
    )


def _mark_visual_failed(generation, *, stage, error):
    logger.info(
        "marketing_visual_failed generation_id=%s stage=%s error=%s",
        generation.get("id"),
        stage,
        error,
    )
    return _patch_generation_data(
        generation,
        visual_status="failed",
        visual_failed=True,
        visual_stage=stage,
        visual_error=error,
        visual_asset_id=None,
        visual_asset_ids=[],
        visual_asset_url=None,
    )


def _attach_visual_assets(generation, batch_view):
    ready_assets = []
    template_used = None
    storage_key = None
    for item in (batch_view or {}).get("assets") or []:
        if not item.get("id"):
            continue
        ready = bool(item.get("ready") or item.get("storage_key"))
        if not ready:
            continue
        preview = f"/marketing/assets/{item['id']}/preview"
        ready_assets.append(
            {
                "id": item["id"],
                "format": item.get("format"),
                "ready": True,
                "preview_url": preview,
                "download_url": f"/marketing/assets/{item['id']}/download.png",
                "storage_key": item.get("storage_key"),
                "template_used": item.get("template_used")
                or ((item.get("options") or {}).get("template_used")),
            }
        )
        template_used = template_used or ready_assets[-1]["template_used"]
        storage_key = storage_key or item.get("storage_key")
    data = dict(generation.get("generated_data") or {})
    data["visual_batch_id"] = (batch_view or {}).get("generation_id") or (batch_view or {}).get("id")
    if ready_assets:
        data["visual_asset_ids"] = [item["id"] for item in ready_assets]
        data["visual_asset_id"] = ready_assets[0]["id"]
        data["visual_asset_url"] = ready_assets[0]["preview_url"]
        data["visual_status"] = "completed"
        data["visual_failed"] = False
        data["visual_stage"] = "complete"
        data["visual_error"] = None
        data["template_used"] = template_used or data.get("template_used")
        logger.info(
            "marketing_visual_storage_ok url=%s template_used=%s",
            data["visual_asset_url"],
            data.get("template_used"),
        )
        logger.info(
            "marketing_visual_complete generation_id=%s path=%s",
            generation.get("id"),
            storage_key,
        )
    else:
        data["visual_asset_ids"] = []
        data["visual_asset_id"] = None
        data["visual_asset_url"] = None
        data["visual_status"] = "failed"
        data["visual_failed"] = True
        data["visual_stage"] = data.get("visual_stage") or "attach"
        data["visual_error"] = data.get("visual_error") or "not_ready"
        logger.info(
            "marketing_visual_failed generation_id=%s stage=%s error=%s",
            generation.get("id"),
            data["visual_stage"],
            data["visual_error"],
        )
    return update_marketing_generation(
        generation["id"],
        generation["organization_id"],
        generated_data=data,
    )


def _run_visual_for_chat(
    organization_id,
    user,
    intent,
    prompt,
    language,
    last_generation=None,
    generation=None,
    conversation=None,
):
    import os

    from modules.marketing_context import build_property_marketing_context
    from modules.marketing_service import start_marketing_batch

    generation_id = (generation or last_generation or {}).get("id")
    # Generation property_id is the source of truth for what copy already used.
    # Never re-resolve by address/title from the prompt.
    generation_property_id = _as_property_id(
        (generation or {}).get("property_id")
        or (last_generation or {}).get("property_id")
    )
    conversation_property_id = _as_property_id((conversation or {}).get("property_id"))
    intent_property_id = _as_property_id((intent or {}).get("property_id"))
    property_id = generation_property_id or conversation_property_id or intent_property_id
    if property_id and intent is not None:
        intent["property_id"] = property_id

    if not property_id:
        _log_property_trace(
            "marketing_visual_start",
            generation_id=generation_id,
            resolved_property_id=None,
            conversation_property_id=conversation_property_id,
            generation_property_id=generation_property_id,
            visual_property_id=None,
            photo_count=0,
        )
        return {"ok": False, "stage": "property", "error": "missing_property", "batch": None}

    property_data = get_property_record(property_id, organization_id)
    if property_data is None:
        _log_property_trace(
            "marketing_visual_start",
            generation_id=generation_id,
            resolved_property_id=property_id,
            conversation_property_id=conversation_property_id,
            generation_property_id=generation_property_id,
            visual_property_id=property_id,
            photo_count=0,
        )
        return {"ok": False, "stage": "property", "error": "missing_property", "batch": None}

    context = build_property_marketing_context(
        property_data,
        language=language,
        include_agent=intent.get("include_agent") is not False,
    )
    photos = context.get("photos") or []
    photo_count = len(photos)
    agent = context.get("agent") or {}
    _log_property_trace(
        "marketing_visual_start",
        generation_id=generation_id,
        resolved_property_id=property_id,
        conversation_property_id=conversation_property_id,
        generation_property_id=generation_property_id,
        visual_property_id=property_id,
        photo_count=photo_count,
    )
    logger.info("marketing_visual_agent resolved=%s", str(bool(agent)).lower())
    if photo_count <= 0:
        return {"ok": False, "stage": "photos", "error": "missing_photos", "batch": None}

    formats = _visual_formats(intent, generation or last_generation)
    copy_override = _copy_override_from_generation(generation or last_generation)
    previous = os.environ.get("MARKETING_SYNC")
    os.environ["MARKETING_SYNC"] = "1"
    try:
        batch = start_marketing_batch(
            organization_id,
            user,
            property_id=property_id,
            prompt=prompt,
            language=language,
            formats=formats,
            count=1,
            style=intent.get("style") or "premium",
            request_text=prompt,
            include_agent=intent.get("include_agent") is not False,
            include_price=True,
            local_render=True,
            copy_override=copy_override,
            layout_template=intent.get("layout_template") or intent.get("format"),
            cta=intent.get("cta"),
        )
        _log_property_trace(
            "marketing_visual_complete",
            generation_id=generation_id,
            property_id=property_id,
            photo_count=photo_count,
        )
        return {"ok": True, "stage": "complete", "error": None, "batch": batch}
    finally:
        if previous is None:
            os.environ.pop("MARKETING_SYNC", None)
        else:
            os.environ["MARKETING_SYNC"] = previous


def _apply_visual_to_generation(
    organization_id,
    user,
    intent,
    prompt,
    language,
    generation,
    last_generation=None,
    conversation=None,
):
    generation = _patch_generation_data(
        generation,
        visual_status="processing",
        visual_failed=False,
        visual_stage="start",
        visual_error=None,
    )
    try:
        result = _run_visual_for_chat(
            organization_id,
            user,
            intent,
            prompt,
            language,
            last_generation=last_generation or generation,
            generation=generation,
            conversation=conversation,
        )
    except Exception as error:
        logger.exception("marketing chat visual generation failed")
        return _mark_visual_failed(
            generation,
            stage="exception",
            error=type(error).__name__,
        )
    if not result or not result.get("ok") or not result.get("batch"):
        stage = (result or {}).get("stage") or "render"
        error = (result or {}).get("error") or "not_ready"
        return _mark_visual_failed(generation, stage=stage, error=error)
    return _attach_visual_assets(generation, result["batch"])


def _wants_visual(intent):
    return intent.get("action") in VISUAL_ACTIONS or intent.get("action") in {
        "edit_existing_generation",
        "generate_visual",
    }


def _mock_chat_reply(prompt, language):
    if (language or "es").startswith("en"):
        return (
            "Here are a few directions you can take with that brief. "
            "If you want, I can turn one into a visual post or story."
        )
    return (
        "Puedo ayudarte con ideas, copy o una pieza visual. "
        f"Sobre «{prompt.strip()[:80]}»: armá 3 ángulos (ubicación, estilo de vida y llamado a la visita) "
        "y después convertimos el que más te cierre en imagen."
    )


def _conversational_reply(prompt, *, language="es"):
    from modules.marketing_ai import MOCK_PROVIDER, resolve_provider
    from modules.marketing_ai_client import MarketingAIClientError, request_marketing_text

    engine = resolve_provider()
    if getattr(engine, "name", MOCK_PROVIDER) != "openai":
        return _mock_chat_reply(prompt, language)
    try:
        return request_marketing_text(
            instructions=CHAT_SYSTEM_PROMPT,
            user_payload=prompt,
            max_tokens=500,
        )
    except MarketingAIClientError:
        return _mock_chat_reply(prompt, language)


def _listing_decision_facts(property_row, language):
    if not property_row:
        return {}
    from modules.marketing_context import build_property_marketing_context

    packed = build_property_marketing_context(
        property_row,
        language=language,
        include_agent=True,
    )
    try:
        from modules.property_sync.media import get_property_media_url
    except Exception:
        get_property_media_url = lambda item, _pid: None
    photos = []
    for index, item in enumerate(packed.get("photos") or [], start=1):
        photos.append(
            {
                **item,
                "url": get_property_media_url(item, property_row.get("id")),
                "label": f"Foto {index}",
            }
        )
    facts = packed.get("facts") or {}
    return {
        "property_id": property_row.get("id"),
        "address": property_row.get("address") or facts.get("title"),
        "purpose": facts.get("purpose")
        or facts.get("operation_type")
        or property_row.get("listing_purpose"),
        "agent": packed.get("agent") or {},
        "photos": photos,
        "photo_count": len(photos),
        "has_cover": any(item.get("is_cover") for item in photos),
    }


def _apply_context_to_intent(intent, context):
    intent = dict(intent or {})
    if context.get("include_agent") is not None:
        intent["include_agent"] = context["include_agent"] is not False
    if context.get("property_id"):
        intent["property_id"] = context["property_id"]
    if context.get("cta"):
        intent["cta"] = context["cta"]
    if context.get("hero_photo_id"):
        intent["hero_photo_id"] = context["hero_photo_id"]
    if context.get("layout_template"):
        intent["layout_template"] = context["layout_template"]
    if intent.get("action") not in {"generate_visual", "edit_existing_generation", "chat"}:
        if context.get("channel") and context.get("channel") != "auto":
            intent["channel"] = context["channel"]
            intent["content_type"] = content_type_from_channel(
                context, intent.get("content_type") or "post"
            )
            if intent.get("content_type") == "story":
                intent["requested_format"] = intent.get("requested_format") or "story"
            intent["action"] = action_from_channel(context, intent.get("action"))
        style, tone = generation_style_tone(context)
        intent["style"] = style
        intent["tone"] = context.get("tone") or tone
    return intent


def _persist_chat_context(conversation, organization_id, context, *, property_id=None):
    payload = persistable_context(context)
    updates = {"context": payload}
    if property_id:
        updates["property_id"] = property_id
    return update_marketing_conversation(
        conversation["id"],
        organization_id,
        **updates,
    )


def send_chat_message(
    organization_id,
    user,
    *,
    prompt,
    conversation_id=None,
    property_id=None,
    language="es",
    attachment_name=None,
    preferred_mode="auto",
    decision_key=None,
    decision_value=None,
):
    organization_id = require_organization_id(organization_id)
    prompt = (prompt or "").strip()
    if not prompt:
        raise MarketingError("marketing_ia_err_prompt", 400)
    properties = _picker_properties(organization_id, user)
    conversation = None
    if conversation_id:
        conversation = require_conversation(organization_id, user, conversation_id)
    last = None
    if conversation:
        last_id = last_generation_id_for_conversation(conversation["id"], organization_id)
        if last_id:
            last = get_marketing_generation(last_id, organization_id)
    stored = conversation_context(conversation)
    parsed_decisions = parse_prompt_decisions(prompt)
    work_prompt = prompt
    if decision_key:
        work_prompt = stored.get("pending_prompt") or prompt
    elif stored.get("pending_decision") and looks_like_decision_reply(
        prompt, stored.get("pending_decision"), parsed_decisions
    ):
        work_prompt = stored.get("pending_prompt") or prompt
    intent = interpret_prompt(
        work_prompt,
        conversation=conversation,
        last_generation=last,
        property_id=property_id,
        properties=properties,
        preferred_mode=preferred_mode,
    )
    property_row = None
    if intent.get("property_id"):
        property_row = _resolve_property(organization_id, user, intent["property_id"])
        intent["property_id"] = _as_property_id(property_row["id"])
        intent["needs_property"] = False
        _log_property_trace(
            "marketing_property_bound",
            resolved_property_id=intent["property_id"],
            conversation_property_id=_as_property_id((conversation or {}).get("property_id")),
            photo_count=(
                _photo_count(_find_property_by_id(properties, intent["property_id"]))
                if properties
                else None
            ),
            status=intent.get("property_status"),
        )
    if conversation is None:
        conversation = create_marketing_conversation(
            organization_id,
            user_id=user.get("id"),
            title=_conversation_title(work_prompt, property_row),
            property_id=intent.get("property_id"),
        )
    else:
        updates = {}
        if intent.get("property_id") and intent["property_id"] != _as_property_id(
            conversation.get("property_id")
        ):
            updates["property_id"] = intent["property_id"]
        if not conversation.get("title"):
            updates["title"] = _conversation_title(work_prompt, property_row)
        if updates:
            conversation = update_marketing_conversation(
                conversation["id"], organization_id, **updates
            )
    user_meta = {
        "intent": {
            k: intent[k]
            for k in intent
            if k not in {"matched_property", "property_matches"}
        }
    }
    if attachment_name:
        user_meta["attachment_name"] = attachment_name
    if decision_key:
        user_meta["decision_key"] = decision_key
        user_meta["decision_value"] = decision_value
    add_marketing_message(
        conversation["id"],
        organization_id,
        role="user",
        message_type="text",
        content=prompt,
        metadata=user_meta,
    )
    generation = None
    listing = _listing_decision_facts(property_row, language)
    context = resolve_context(
        intent,
        conversation,
        prompt,
        listing,
        form_key=decision_key,
        form_value=decision_value,
    )
    listing_blocked = _wants_listing_property(intent.get("action"), intent.get("origin"))
    display_query = " ".join(
        part.capitalize() if not part.isdigit() else part
        for part in (intent.get("property_query") or "").split()
    )
    if intent.get("property_status") == "ambiguous" and listing_blocked:
        add_marketing_message(
            conversation["id"],
            organization_id,
            role="assistant",
            message_type="text",
            content=_t(
                "marketing_ia_property_many",
                language,
                query=display_query,
            ),
            metadata={
                "intent": user_meta["intent"],
                "pending_prompt": work_prompt,
                "property_query": intent.get("property_query"),
                "property_choices": intent.get("property_matches") or [],
            },
        )
    elif intent.get("property_status") == "none" and listing_blocked:
        add_marketing_message(
            conversation["id"],
            organization_id,
            role="assistant",
            message_type="text",
            content=_t(
                "marketing_ia_property_none",
                language,
                query=display_query,
            ),
            metadata={
                "intent": user_meta["intent"],
                "pending_prompt": work_prompt,
                "property_query": intent.get("property_query"),
                "open_picker": True,
            },
        )
    elif intent.get("needs_property"):
        add_marketing_message(
            conversation["id"],
            organization_id,
            role="assistant",
            message_type="text",
            content=_t("marketing_ia_need_property", language),
            metadata={"intent": user_meta["intent"]},
        )
    elif intent.get("action") == "chat":
        reply = _conversational_reply(prompt, language=language)
        add_marketing_message(
            conversation["id"],
            organization_id,
            role="assistant",
            message_type="text",
            content=reply,
            metadata={"intent": user_meta["intent"], "action": "chat"},
        )
    elif should_clarify(intent) and determine_missing_decisions(context, listing, intent):
        key = determine_missing_decisions(context, listing, intent)[0]
        spec = question_spec(
            key,
            language=language,
            listing=listing,
            intent=intent,
            context=context,
        )
        context["pending_decision"] = key
        context["pending_prompt"] = context.get("_work_prompt") or work_prompt
        conversation = _persist_chat_context(
            conversation,
            organization_id,
            context,
            property_id=intent.get("property_id"),
        )
        add_marketing_message(
            conversation["id"],
            organization_id,
            role="assistant",
            message_type="text",
            content=spec.get("text") or "",
            metadata={
                "intent": user_meta["intent"],
                "decision_key": key,
                "decision_options": spec.get("options") or [],
                "pending_prompt": context.get("pending_prompt"),
                "as_cards": bool(spec.get("as_cards")),
            },
        )
    elif intent.get("action") == "generate_visual" and _has_usable_copy(last):
        context = apply_intelligent_defaults(context, listing)
        conversation = _persist_chat_context(
            conversation,
            organization_id,
            context,
            property_id=intent.get("property_id"),
        )
        intent = _apply_context_to_intent(intent, context)
        generation = last
        data = dict(generation.get("generated_data") or {})
        data["action"] = "generate_visual"
        data["requested_format"] = (
            intent.get("requested_format")
            or data.get("requested_format")
            or intent.get("content_type")
            or generation.get("content_type")
        )
        data["copy_status"] = data.get("copy_status") or "completed"
        data["include_agent"] = intent.get("include_agent")
        # Keep visual on the generation's property_id; never re-resolve by prompt text.
        intent["property_id"] = _as_property_id(
            generation.get("property_id") or intent.get("property_id")
        )
        generation = update_marketing_generation(
            generation["id"],
            organization_id,
            generated_data=data,
        )
        generation = _apply_visual_to_generation(
            organization_id,
            user,
            intent,
            work_prompt,
            language,
            generation,
            last_generation=last,
            conversation=conversation,
        )
        gen_meta = {
            "action": "generate_visual",
            "requested_format": data.get("requested_format"),
            "parent_generation_id": intent.get("parent_generation_id") or last.get("id"),
            "visual_asset_id": (generation.get("generated_data") or {}).get("visual_asset_id"),
            "visual_asset_ids": (generation.get("generated_data") or {}).get("visual_asset_ids") or [],
            "visual_status": (generation.get("generated_data") or {}).get("visual_status"),
        }
        lead = _t("marketing_ia_ack_visual", language)
        add_marketing_message(
            conversation["id"],
            organization_id,
            role="assistant",
            message_type="generation",
            content=lead,
            generation_id=generation["id"],
            metadata=gen_meta,
        )
    else:
        context = apply_intelligent_defaults(context, listing)
        context["pending_decision"] = None
        context["pending_prompt"] = None
        conversation = _persist_chat_context(
            conversation,
            organization_id,
            context,
            property_id=intent.get("property_id"),
        )
        intent = _apply_context_to_intent(intent, context)
        extra_notes = work_prompt
        if intent.get("requested_format") == "reel":
            extra_notes = f"{work_prompt}\nFormato pedido: reel 9:16."
        elif intent.get("requested_format") == "flyer":
            extra_notes = f"{work_prompt}\nFormato pedido: flyer."
        if context.get("cta"):
            extra_notes = f"{extra_notes}\nCTA: {context['cta']}."
        if context.get("include_agent") is False:
            extra_notes = (
                f"{extra_notes}\nUsar solo la marca de la inmobiliaria. "
                "No incluir foto ni datos del agente."
            )
        _log_property_trace(
            "marketing_copy",
            property_id=intent.get("property_id"),
            conversation_property_id=_as_property_id(conversation.get("property_id")),
        )
        generation = create_and_run_generation(
            organization_id,
            user,
            content_type=intent["content_type"] if intent["content_type"] in ("post", "story", "carousel", "copy", "whatsapp") else "post",
            origin=intent["origin"],
            property_id=intent.get("property_id"),
            objective=intent.get("objective"),
            style=intent.get("style"),
            tone=intent.get("tone"),
            format=intent.get("format"),
            prompt_input=extra_notes,
            language=language,
            parent_generation_id=intent.get("parent_generation_id"),
            include_agent=False if context.get("include_agent") is False else True,
        )
        data = dict(generation.get("generated_data") or {})
        data["action"] = intent.get("action")
        data["requested_format"] = intent.get("requested_format") or intent["content_type"]
        data["copy_status"] = "completed" if generation.get("status") == "completed" else "failed"
        data["visual_status"] = "idle"
        data["include_agent"] = context.get("include_agent") is not False
        data["channel"] = context.get("channel")
        data["creative_style"] = context.get("style")
        data["layout_template"] = context.get("layout_template")
        data["resolved_property_id"] = _as_property_id(
            generation.get("property_id") or intent.get("property_id")
        )
        generation = update_marketing_generation(
            generation["id"],
            organization_id,
            generated_data=data,
        )
        # Align intent to the generation row — copy already bound this property_id.
        intent["property_id"] = _as_property_id(generation.get("property_id") or intent.get("property_id"))
        if _wants_visual(intent) and data["copy_status"] == "completed":
            generation = _apply_visual_to_generation(
                organization_id,
                user,
                intent,
                work_prompt,
                language,
                generation,
                last_generation=last,
                conversation=conversation,
            )
        gen_meta = {
            "action": intent.get("action"),
            "requested_format": intent.get("requested_format") or intent["content_type"],
            "parent_generation_id": intent.get("parent_generation_id"),
            "visual_asset_id": (generation.get("generated_data") or {}).get("visual_asset_id"),
            "visual_asset_ids": (generation.get("generated_data") or {}).get("visual_asset_ids") or [],
            "visual_status": (generation.get("generated_data") or {}).get("visual_status"),
            "include_agent": data.get("include_agent"),
        }
        lead = _t(_ack_key(intent) or "marketing_ia_ack_visual", language)
        if intent.get("property_auto_picked"):
            label = listing_label(property_row, intent)
            lead = (
                _t(
                    "marketing_ia_auto_picked",
                    language,
                    count=len(intent.get("property_matches") or []),
                    query=display_query,
                    address=label or display_query,
                )
                + "\n\n"
                + lead
            )
        add_marketing_message(
            conversation["id"],
            organization_id,
            role="assistant",
            message_type="generation",
            content=lead,
            generation_id=generation["id"],
            metadata=gen_meta,
        )
    conversation = get_marketing_conversation(conversation["id"], organization_id)
    return {
        "conversation": conversation,
        "generation": generation,
        "intent": intent,
    }


def regenerate_in_conversation(
    organization_id,
    user,
    conversation_id,
    generation_id,
    *,
    language="es",
    visual_only=False,
):
    organization_id = require_organization_id(organization_id)
    require_conversation(organization_id, user, conversation_id)
    source = get_marketing_generation(generation_id, organization_id)
    if source is None:
        raise MarketingError("marketing_ia_err_missing", 404)
    source_data = source.get("generated_data") or {}
    copy_ok = _has_usable_copy(source)
    retry_visual_only = bool(visual_only) and copy_ok
    add_marketing_message(
        conversation_id,
        organization_id,
        role="user",
        message_type="text",
        content=_t("marketing_ia_regenerate", language),
        metadata={
            "action": "regenerate_visual" if retry_visual_only else "regenerate",
            "generation_id": generation_id,
        },
    )
    source_action = source_data.get("action") or "generate_post_image"
    requested_format = source_data.get("requested_format") or source.get("content_type")
    conversation = get_marketing_conversation(conversation_id, organization_id)
    stored = conversation_context(conversation)
    # Retry must reuse the generation property_id — never re-resolve by text.
    retry_property_id = _as_property_id(source.get("property_id"))
    intent = {
        "action": "generate_visual" if retry_visual_only else source_action,
        "content_type": source.get("content_type"),
        "requested_format": requested_format,
        "property_id": retry_property_id,
        "style": source.get("style"),
        "include_agent": stored.get("include_agent"),
    }
    _log_property_trace(
        "marketing_visual_retry",
        generation_id=generation_id,
        generation_property_id=retry_property_id,
        conversation_property_id=_as_property_id(conversation.get("property_id")),
        visual_only=int(bool(retry_visual_only)),
    )
    if retry_visual_only and copy_ok:
        generation = _apply_visual_to_generation(
            organization_id,
            user,
            intent,
            source.get("prompt_input") or "",
            language,
            source,
            last_generation=source,
            conversation=conversation,
        )
        ack_key = "marketing_ia_ack_visual"
    else:
        generation = regenerate_generation(
            organization_id,
            user,
            generation_id,
            language=language,
        )
        data = dict(generation.get("generated_data") or {})
        data["action"] = source_action
        data["requested_format"] = requested_format
        data["copy_status"] = "completed" if generation.get("status") == "completed" else "failed"
        data["visual_status"] = "idle"
        data["resolved_property_id"] = _as_property_id(generation.get("property_id"))
        generation = update_marketing_generation(
            generation["id"], organization_id, generated_data=data
        )
        intent["property_id"] = _as_property_id(generation.get("property_id") or retry_property_id)
        if _wants_visual({"action": source_action}) and data["copy_status"] == "completed":
            generation = _apply_visual_to_generation(
                organization_id,
                user,
                intent,
                source.get("prompt_input") or "",
                language,
                generation,
                last_generation=source,
                conversation=conversation,
            )
        ack_key = "marketing_ia_ack_revise"
    add_marketing_message(
        conversation_id,
        organization_id,
        role="assistant",
        message_type="generation",
        content=_t(ack_key, language),
        generation_id=generation["id"],
        metadata={
            "action": intent.get("action"),
            "requested_format": requested_format,
            "parent_generation_id": source["id"],
            "visual_asset_id": (generation.get("generated_data") or {}).get("visual_asset_id"),
            "visual_asset_ids": (generation.get("generated_data") or {}).get("visual_asset_ids") or [],
            "visual_status": (generation.get("generated_data") or {}).get("visual_status"),
        },
    )
    return {
        "conversation": get_marketing_conversation(conversation_id, organization_id),
        "generation": generation,
    }
