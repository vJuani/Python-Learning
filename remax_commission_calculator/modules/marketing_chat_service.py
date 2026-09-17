"""Conversational Marketing IA: intent, context, and in-thread generation."""

from __future__ import annotations

import re
import unicodedata

from modules.auth import is_admin, scoped_agent_id
from modules.database.marketing_conversations_repository import (
    add_marketing_message,
    create_marketing_conversation,
    get_marketing_conversation,
    last_generation_id_for_conversation,
    list_marketing_conversations,
    list_marketing_messages,
    update_marketing_conversation,
)
from modules.database.marketing_generations_repository import get_marketing_generation
from modules.database.properties_repository import get_properties, get_property_record
from modules.database.tenant import require_organization_id
from modules.i18n import translate
from modules.marketing_context import MarketingError, assert_marketing_access
from modules.marketing_generation_service import (
    STYLES,
    TONES,
    create_and_run_generation,
    regenerate_generation,
)


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
    ("modern premium", "modern_premium_v1"),
    ("moderno premium", "modern_premium_v1"),
    ("minimal", "minimal_v1"),
)
REVISION_RE = re.compile(
    r"\b("
    r"cambia|cambiale|regener|otra version|mas moderna|mas modern|"
    r"saca |sacale|usa otra|usala|hace otra|ahora hace|con esto|"
    r"pone la|el titulo|el titular|el cta|segunda foto|quita "
    r")\b"
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


def _fold(text):
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip().lower()


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


def match_property_from_prompt(prompt, properties):
    text = _fold(prompt)
    if not text:
        return None
    ranked = []
    for item in properties or []:
        address = _fold(item.get("address") or "")
        locality = _fold(item.get("locality") or item.get("neighborhood") or "")
        if not address:
            continue
        score = 0
        if address in text or (len(address) >= 8 and address[:12] in text):
            score += 8
        tokens = [token for token in re.split(r"[^\w]+", address) if len(token) >= 3]
        digits = [token for token in re.split(r"[^\w]+", address) if token.isdigit()]
        score += sum(2 for token in tokens if token in text)
        score += sum(3 for token in digits if token in text)
        if locality and locality in text:
            score += 1
        if score >= 5:
            ranked.append((score, item))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked[0][1]


def interpret_prompt(
    prompt,
    *,
    conversation=None,
    last_generation=None,
    property_id=None,
    properties=None,
):
    folded = _fold(prompt)
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

    matched = None
    if not property_id:
        matched = match_property_from_prompt(prompt, properties)
        if matched:
            property_id = matched.get("id")
    if property_id is None and conversation:
        property_id = conversation.get("property_id")

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
        property_id = property_id or last_generation.get("property_id")

    if origin is None:
        if property_id:
            origin = "property"
        elif objective == "personal_brand":
            origin = "personal_brand"
        else:
            origin = "free"
    content_type = content_type or ("post" if origin == "property" else "copy")
    needs_property = origin == "property" and not property_id
    parent_id = last_generation["id"] if revising and last_generation else None
    return {
        "content_type": content_type,
        "origin": origin,
        "objective": objective,
        "style": style if style in STYLES else None,
        "tone": tone if tone in TONES else None,
        "format": template,
        "requested_format": requested_format,
        "property_id": property_id,
        "matched_property": matched,
        "revising": bool(parent_id),
        "parent_generation_id": parent_id,
        "needs_property": needs_property,
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
    if intent.get("revising"):
        return "marketing_ia_ack_revise"
    if intent.get("origin") == "personal_brand":
        return "marketing_ia_ack_brand"
    if intent.get("objective") == "capture_owner":
        return "marketing_ia_ack_capture"
    if intent.get("origin") == "property":
        return "marketing_ia_ack_property"
    return "marketing_ia_ack_generic"


def _picker_properties(organization_id, user):
    rows = get_properties(organization_id, agent_id=scoped_agent_id(user))
    try:
        from modules.property_sync.media import get_property_media_url, list_covers_for_properties

        covers = list_covers_for_properties(
            organization_id,
            [row["id"] for row in rows if row.get("id")],
        )
        for row in rows:
            cover = covers.get(int(row["id"])) if row.get("id") else None
            row["cover_url"] = get_property_media_url(cover, row.get("id"))
    except Exception:
        for row in rows:
            row.setdefault("cover_url", None)
    return rows


def _resolve_property(organization_id, user, property_id):
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
        "conversation": conversation,
        "messages": messages,
        "properties": properties,
        "selected_property": selected_property,
        "language": language,
        "is_admin": is_admin(user),
    }


def send_chat_message(
    organization_id,
    user,
    *,
    prompt,
    conversation_id=None,
    property_id=None,
    language="es",
    attachment_name=None,
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
    intent = interpret_prompt(
        prompt,
        conversation=conversation,
        last_generation=last,
        property_id=property_id,
        properties=properties,
    )
    property_row = None
    if intent.get("property_id"):
        property_row = _resolve_property(organization_id, user, intent["property_id"])
        intent["property_id"] = property_row["id"]
        intent["needs_property"] = False
    if conversation is None:
        conversation = create_marketing_conversation(
            organization_id,
            user_id=user.get("id"),
            title=_conversation_title(prompt, property_row),
            property_id=intent.get("property_id"),
        )
    else:
        updates = {}
        if intent.get("property_id") and intent["property_id"] != conversation.get("property_id"):
            updates["property_id"] = intent["property_id"]
        if not conversation.get("title"):
            updates["title"] = _conversation_title(prompt, property_row)
        if updates:
            conversation = update_marketing_conversation(
                conversation["id"], organization_id, **updates
            )
    user_meta = {"intent": {k: intent[k] for k in intent if k != "matched_property"}}
    if attachment_name:
        user_meta["attachment_name"] = attachment_name
    add_marketing_message(
        conversation["id"],
        organization_id,
        role="user",
        message_type="text",
        content=prompt,
        metadata=user_meta,
    )
    ack = _t(_ack_key(intent, needs_property=intent.get("needs_property")), language)
    add_marketing_message(
        conversation["id"],
        organization_id,
        role="assistant",
        message_type="text",
        content=ack,
        metadata={"intent": user_meta["intent"]},
    )
    generation = None
    if not intent.get("needs_property"):
        extra_notes = prompt
        if intent.get("requested_format") == "reel":
            extra_notes = f"{prompt}\nFormato pedido: reel 9:16."
        elif intent.get("requested_format") == "flyer":
            extra_notes = f"{prompt}\nFormato pedido: flyer."
        generation = create_and_run_generation(
            organization_id,
            user,
            content_type=intent["content_type"],
            origin=intent["origin"],
            property_id=intent.get("property_id"),
            objective=intent.get("objective"),
            style=intent.get("style"),
            tone=intent.get("tone"),
            format=intent.get("format"),
            prompt_input=extra_notes,
            language=language,
            parent_generation_id=intent.get("parent_generation_id"),
        )
        gen_meta = {
            "requested_format": intent.get("requested_format") or intent["content_type"],
            "parent_generation_id": intent.get("parent_generation_id"),
        }
        add_marketing_message(
            conversation["id"],
            organization_id,
            role="assistant",
            message_type="generation",
            content=generation.get("generated_copy"),
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
):
    organization_id = require_organization_id(organization_id)
    require_conversation(organization_id, user, conversation_id)
    source = get_marketing_generation(generation_id, organization_id)
    if source is None:
        raise MarketingError("marketing_ia_err_missing", 404)
    add_marketing_message(
        conversation_id,
        organization_id,
        role="user",
        message_type="text",
        content=_t("marketing_ia_regenerate", language),
        metadata={"action": "regenerate", "generation_id": generation_id},
    )
    add_marketing_message(
        conversation_id,
        organization_id,
        role="assistant",
        message_type="text",
        content=_t("marketing_ia_ack_revise", language),
        metadata={"action": "regenerate"},
    )
    generation = regenerate_generation(
        organization_id,
        user,
        generation_id,
        language=language,
    )
    add_marketing_message(
        conversation_id,
        organization_id,
        role="assistant",
        message_type="generation",
        content=generation.get("generated_copy"),
        generation_id=generation["id"],
        metadata={
            "requested_format": (source.get("generated_data") or {}).get("requested_format")
            or source.get("content_type"),
            "parent_generation_id": source["id"],
        },
    )
    return {
        "conversation": get_marketing_conversation(conversation_id, organization_id),
        "generation": generation,
    }
