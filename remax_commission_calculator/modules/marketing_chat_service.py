"""Conversational Marketing IA: intent, context, and in-thread generation."""

from __future__ import annotations

import logging
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
}
VISUAL_ACTIONS = frozenset(ACTION_FORMATS)
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
        token_hits = {}
        for item in properties or []:
            blob = " ".join(
                [
                    _fold(item.get("address") or ""),
                    _fold(item.get("locality") or ""),
                    _fold(item.get("neighborhood") or ""),
                ]
            )
            for token in {part for part in re.split(r"[^\w]+", blob) if len(part) >= 4}:
                if token in text:
                    token_hits.setdefault(token, []).append(item)
        unique = [
            items[0]
            for items in token_hits.values()
            if len(items) == 1
        ]
        if len({item.get("id") for item in unique}) == 1:
            return unique[0]
        return None
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked[0][1]


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
    last_action = ((last_generation or {}).get("generated_data") or {}).get("action")
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

    if last_generation and not format_switch and (
        (visual_hit or create_hit or image_hit) and len(folded) < 140
    ):
        if last_action in VISUAL_ACTIONS or visual_hit or create_hit or image_hit:
            return "edit_existing_generation"

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
    content_type = content_type or (
        "post"
        if origin == "property"
        and (CREATE_RE.search(folded) or IMAGE_RE.search(folded) or FORMAT_RE.search(folded))
        else "copy"
    )
    needs_property = origin == "property" and not property_id
    parent_id = last_generation["id"] if revising and last_generation else None
    action = _resolve_action(
        folded,
        content_type,
        requested_format,
        last_generation,
        (preferred_mode or "auto").strip().lower(),
    )
    if action == "edit_existing_generation":
        parent_id = last_generation["id"] if last_generation else parent_id
    if action == "chat":
        needs_property = False
    elif action in VISUAL_ACTIONS and origin == "property" and not property_id:
        needs_property = True
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
        "revising": bool(parent_id) or action == "edit_existing_generation",
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
    if intent.get("action") == "chat":
        return None
    if intent.get("revising") or intent.get("action") == "edit_existing_generation":
        return "marketing_ia_ack_revise"
    return "marketing_ia_ack_visual"


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


def _visual_formats(intent, last_generation=None):
    action = intent.get("action")
    if action == "edit_existing_generation":
        last_action = ((last_generation or {}).get("generated_data") or {}).get("action")
        if last_action in ACTION_FORMATS:
            return ACTION_FORMATS[last_action]
        fmt = intent.get("requested_format") or intent.get("content_type") or "post"
        if fmt == "story":
            return ["story"]
        if fmt == "flyer":
            return ["flyer"]
        return ["post"]
    if intent.get("requested_format") == "flyer":
        return ["flyer"]
    return ACTION_FORMATS.get(action) or ["post"]


def _last_visual_asset_id(generation):
    data = (generation or {}).get("generated_data") or {}
    if data.get("visual_asset_id"):
        return data["visual_asset_id"]
    ids = data.get("visual_asset_ids") or []
    return ids[0] if ids else None


def _attach_visual_assets(generation, batch_view):
    from modules.database.marketing_generations_repository import update_marketing_generation

    assets = []
    for item in (batch_view or {}).get("assets") or []:
        if not item.get("id"):
            continue
        ready = item.get("ready") or item.get("storage_key")
        assets.append(
            {
                "id": item["id"],
                "format": item.get("format"),
                "ready": bool(ready),
                "preview_url": f"/marketing/assets/{item['id']}/preview" if ready else None,
                "download_url": (
                    f"/marketing/assets/{item['id']}/download.png" if ready else None
                ),
            }
        )
    data = dict(generation.get("generated_data") or {})
    data["visual_batch_id"] = (batch_view or {}).get("generation_id") or (batch_view or {}).get("id")
    data["visual_asset_ids"] = [item["id"] for item in assets]
    data["visual_failed"] = not any(item.get("ready") for item in assets)
    if assets:
        data["visual_asset_id"] = assets[0]["id"]
    generation = update_marketing_generation(
        generation["id"],
        generation["organization_id"],
        generated_data=data,
    )
    return generation


def _run_visual_for_chat(organization_id, user, intent, prompt, language, last_generation=None):
    import os

    from modules.marketing_service import start_marketing_batch

    property_id = intent.get("property_id")
    if not property_id:
        return None
    formats = _visual_formats(intent, last_generation)
    reference_id = None
    variation = False
    if intent.get("action") == "edit_existing_generation":
        reference_id = _last_visual_asset_id(last_generation)
        variation = bool(reference_id)
    previous = os.environ.get("MARKETING_SYNC")
    os.environ["MARKETING_SYNC"] = "1"
    try:
        return start_marketing_batch(
            organization_id,
            user,
            property_id=property_id,
            prompt=prompt,
            language=language,
            formats=formats,
            count=1,
            style=intent.get("style"),
            request_text=prompt,
            reference_asset_id=reference_id,
            variation=variation,
        )
    finally:
        if previous is None:
            os.environ.pop("MARKETING_SYNC", None)
        else:
            os.environ["MARKETING_SYNC"] = previous


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
        preferred_mode=preferred_mode,
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
    generation = None
    if intent.get("needs_property"):
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
    else:
        extra_notes = prompt
        if intent.get("requested_format") == "reel":
            extra_notes = f"{prompt}\nFormato pedido: reel 9:16."
        elif intent.get("requested_format") == "flyer":
            extra_notes = f"{prompt}\nFormato pedido: flyer."
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
        )
        data = dict(generation.get("generated_data") or {})
        data["action"] = intent.get("action")
        data["requested_format"] = intent.get("requested_format") or intent["content_type"]
        from modules.database.marketing_generations_repository import update_marketing_generation

        generation = update_marketing_generation(
            generation["id"],
            organization_id,
            generated_data=data,
        )
        if intent.get("action") in VISUAL_ACTIONS or intent.get("action") == "edit_existing_generation":
            try:
                batch = _run_visual_for_chat(
                    organization_id,
                    user,
                    intent,
                    prompt,
                    language,
                    last_generation=last,
                )
                if batch:
                    generation = _attach_visual_assets(generation, batch)
                else:
                    data = dict(generation.get("generated_data") or {})
                    data["visual_failed"] = True
                    generation = update_marketing_generation(
                        generation["id"], organization_id, generated_data=data
                    )
            except Exception:
                logger.exception("marketing chat visual generation failed")
                data = dict(generation.get("generated_data") or {})
                data["visual_failed"] = True
                generation = update_marketing_generation(
                    generation["id"], organization_id, generated_data=data
                )
        gen_meta = {
            "action": intent.get("action"),
            "requested_format": intent.get("requested_format") or intent["content_type"],
            "parent_generation_id": intent.get("parent_generation_id"),
            "visual_asset_id": (generation.get("generated_data") or {}).get("visual_asset_id"),
            "visual_asset_ids": (generation.get("generated_data") or {}).get("visual_asset_ids") or [],
        }
        lead = _t(_ack_key(intent) or "marketing_ia_ack_visual", language)
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
    generation = regenerate_generation(
        organization_id,
        user,
        generation_id,
        language=language,
    )
    data = dict(generation.get("generated_data") or {})
    data["action"] = (source.get("generated_data") or {}).get("action") or "generate_post_image"
    data["requested_format"] = (source.get("generated_data") or {}).get("requested_format") or source.get(
        "content_type"
    )
    from modules.database.marketing_generations_repository import update_marketing_generation

    generation = update_marketing_generation(
        generation["id"], organization_id, generated_data=data
    )
    source_action = data.get("action")
    if source_action in VISUAL_ACTIONS or source_action == "edit_existing_generation":
        try:
            intent = {
                "action": source_action if source_action in VISUAL_ACTIONS else "edit_existing_generation",
                "content_type": source.get("content_type"),
                "requested_format": data.get("requested_format"),
                "property_id": source.get("property_id"),
                "style": source.get("style"),
            }
            batch = _run_visual_for_chat(
                organization_id,
                user,
                intent,
                source.get("prompt_input") or "",
                language,
                last_generation=source,
            )
            if batch:
                generation = _attach_visual_assets(generation, batch)
        except Exception:
            logger.exception("marketing chat visual regenerate failed")
    add_marketing_message(
        conversation_id,
        organization_id,
        role="assistant",
        message_type="generation",
        content=_t("marketing_ia_ack_revise", language),
        generation_id=generation["id"],
        metadata={
            "action": source_action,
            "requested_format": data.get("requested_format"),
            "parent_generation_id": source["id"],
            "visual_asset_id": (generation.get("generated_data") or {}).get("visual_asset_id"),
            "visual_asset_ids": (generation.get("generated_data") or {}).get("visual_asset_ids") or [],
        },
    )
    return {
        "conversation": get_marketing_conversation(conversation_id, organization_id),
        "generation": generation,
    }
