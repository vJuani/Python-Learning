"""Smart clarifying questions for Marketing IA. Ask only what changes the piece."""

from __future__ import annotations

import re
import unicodedata


CONTEXT_KEYS = (
    "property_id",
    "include_agent",
    "channel",
    "style",
    "tone",
    "cta",
    "hero_photo_id",
    "layout_template",
)
CHANNEL_TO_TYPE = {
    "instagram_post": "post",
    "instagram_story": "story",
    "carousel": "carousel",
    "whatsapp": "whatsapp",
}
CHANNEL_TO_ACTION = {
    "instagram_post": "generate_post_image",
    "instagram_story": "generate_story_image",
    "carousel": "generate_carousel",
    "whatsapp": "generate_whatsapp",
}
GENERATION_STYLE = {
    "premium": ("premium", "exclusive"),
    "commercial": ("dynamic", "commercial"),
    "minimal": ("minimal", "close"),
    "modern": ("modern", "commercial"),
    "elegant": ("elegant", "exclusive"),
    "dynamic": ("dynamic", "commercial"),
    "corporate": ("corporate", "formal"),
}
DEFAULT_CTA_SALE = "Consultame para visitarla"
DEFAULT_CTA_RENT = "Consultame disponibilidad"
TEMPLATE_BY_STYLE = {
    "commercial": "modern_commercial_v2",
    "modern": "modern_commercial_v2",
    "dynamic": "social_punch_v2",
    "premium": "premium_editorial_v2",
    "elegant": "premium_editorial_v2",
    "minimal": "premium_editorial_v2",
    "corporate": "premium_editorial_v2",
}
DEFAULT_LAYOUT_TEMPLATE = "modern_commercial_v2"
SKIP_CLARIFY_ACTIONS = frozenset(
    {"chat", "generate_visual", "edit_existing_generation"}
)
HERO_PHOTO_MIN = 4

_AUTO_RE = re.compile(
    r"\b(elegilo vos|eligelo vos|eligilo vos|vos eligi|como quieras|lo que prefieras|da igual|tu elige)\b"
)
_AGENT_YES_RE = re.compile(
    r"\b(mis datos|mi foto|conmigo|usar mis datos|con mi foto|con mis datos|mi cara)\b"
)
_AGENT_NO_RE = re.compile(
    r"\b(solo inmobiliaria|solo la inmobiliaria|sin agente|sin mi foto|sin mis datos|"
    r"solo (la )?marca|solo oficina|sin foto de agente)\b"
)
_STYLE_ALIASES = (
    ("premium", "premium"),
    ("comercial", "commercial"),
    ("commercial", "commercial"),
    ("punch", "dynamic"),
    ("impacto", "dynamic"),
    ("minimalista", "minimal"),
    ("minimal", "minimal"),
    ("moderno", "modern"),
    ("moderna", "modern"),
    ("modern", "modern"),
    ("elegante", "elegant"),
)
_CHANNEL_EXPLICIT = (
    ("carrusel", "carousel"),
    ("carousel", "carousel"),
    ("historias", "instagram_story"),
    ("historia", "instagram_story"),
    ("stories", "instagram_story"),
    ("story", "instagram_story"),
    ("whatsapp", "whatsapp"),
    (" wpp", "whatsapp"),
    ("instagram", "instagram_post"),
    ("publicacion", "instagram_post"),
    ("publicaciones", "instagram_post"),
    ("post", "instagram_post"),
    ("reel", "instagram_story"),
)
_CHANNEL_GENERIC = ("publicidad", "pieza", "flyer", "folleto", "imagen")
_CTA_SPECIFIC_RE = re.compile(
    r"\b(consultame(?: para visitarlo)?|escribime|pedi(?:me)? una visita|"
    r"consulta(?:me)? disponibilidad|whatsappeame|llame(?:me)?)\b"
)
_CTA_GENERIC_RE = re.compile(r"\b(cta|llamado a la accion|cierre con)\b")
_CREATE_RE = re.compile(
    r"\b(haceme|creame|armame|generame|quiero una|necesito una|convert|hacelo|ahora hace)\b"
)


def _fold(text):
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip().lower()


def _tokens(text):
    return [part for part in re.split(r"[^\w]+", text or "") if part]


def empty_context():
    return {
        "property_id": None,
        "include_agent": None,
        "channel": None,
        "style": None,
        "tone": None,
        "cta": None,
        "hero_photo_id": None,
        "layout_template": None,
        "asked": [],
        "pending_decision": None,
        "pending_prompt": None,
    }


def conversation_context(conversation):
    data = empty_context()
    raw = (conversation or {}).get("context") or {}
    if isinstance(raw, dict):
        data.update({key: raw.get(key, data.get(key)) for key in data})
        asked = raw.get("asked") or data["asked"]
        data["asked"] = [str(item) for item in asked if item]
    if (conversation or {}).get("property_id") and not data.get("property_id"):
        data["property_id"] = conversation.get("property_id")
    return data


def public_context(context):
    payload = {}
    for key in CONTEXT_KEYS:
        value = (context or {}).get(key)
        if value is not None:
            payload[key] = value
    return payload


def _mark_asked(context, key):
    asked = list(context.get("asked") or [])
    if key and key not in asked:
        asked.append(key)
    context["asked"] = asked
    return context


def parse_decision_value(key, value):
    if value is None:
        return None
    if key == "include_agent" and value in (True, False):
        return value
    if key == "hero_photo_id" and isinstance(value, int):
        return value
    text = _fold(str(value))
    if text in {"auto", "elegilo_vos", "elegila_vos"}:
        return "auto"
    if key == "include_agent":
        if text in {"1", "true", "yes", "si", "agent", "usar_mis_datos"}:
            return True
        if text in {"0", "false", "no", "office", "solo_inmobiliaria"}:
            return False
        return None
    if key == "channel":
        return {
            "post": "instagram_post",
            "instagram_post": "instagram_post",
            "story": "instagram_story",
            "instagram_story": "instagram_story",
            "carousel": "carousel",
            "whatsapp": "whatsapp",
        }.get(text)
    if key == "style":
        return {
            "premium": "premium",
            "commercial": "commercial",
            "comercial": "commercial",
            "minimal": "minimal",
            "minimalista": "minimal",
            "modern": "modern",
            "moderno": "modern",
            "elegant": "elegant",
            "dynamic": "dynamic",
            "punch": "dynamic",
        }.get(text)
    if key == "hero_photo_id":
        if text in {"pick", "choose", "quiero_elegir"}:
            return "pick"
        try:
            return int(text)
        except (TypeError, ValueError):
            return None
    if key == "cta":
        if text in {"yes", "si", "1", "true"}:
            return "suggested"
        if text in {"other", "otro", "otro_cta"}:
            return "other"
        return str(value).strip() or None
    return str(value).strip() or None


def parse_prompt_decisions(prompt):
    folded = _fold(prompt)
    result = {
        "include_agent": None,
        "channel": None,
        "channel_explicit": False,
        "style": None,
        "tone": None,
        "cta": None,
        "hero_photo_id": None,
        "auto": bool(_AUTO_RE.search(folded)),
    }
    if not folded:
        return result
    if _AGENT_NO_RE.search(folded):
        result["include_agent"] = False
    elif _AGENT_YES_RE.search(folded):
        result["include_agent"] = True
    for needle, mapped in _CHANNEL_EXPLICIT:
        if needle in folded:
            result["channel"] = mapped
            result["channel_explicit"] = True
            break
    for needle, mapped in _STYLE_ALIASES:
        if needle in folded:
            result["style"] = mapped
            if mapped == "commercial":
                result["tone"] = "commercial"
            break
    specific = _CTA_SPECIFIC_RE.search(folded)
    if specific:
        result["cta"] = specific.group(0)
    elif _CTA_GENERIC_RE.search(folded):
        result["cta"] = "ask"
    return result


def looks_like_decision_reply(prompt, pending_key, parsed=None):
    if not pending_key:
        return False
    parsed = parsed or parse_prompt_decisions(prompt)
    folded = _fold(prompt)
    if _CREATE_RE.search(folded) and not parsed.get("auto"):
        return False
    if parsed.get("auto"):
        return True
    if pending_key == "include_agent" and parsed.get("include_agent") is not None:
        return True
    if pending_key == "channel" and parsed.get("channel"):
        return True
    if pending_key == "style" and parsed.get("style"):
        return True
    if pending_key == "cta" and parsed.get("cta"):
        return True
    if pending_key == "hero_photo" and parsed.get("hero_photo_id") is not None:
        return True
    return 1 <= len(_tokens(folded)) <= 6


def apply_form_decision(context, key, value):
    context = dict(context or empty_context())
    if not key:
        return context
    mapped_key = "hero_photo_id" if key == "hero_photo" else key
    coerced = parse_decision_value(mapped_key, value)
    if coerced is None and value not in (None, ""):
        return context
    if mapped_key == "cta" and coerced == "suggested":
        context["cta"] = context.get("cta") or "auto"
    elif mapped_key == "cta" and coerced == "other":
        context["cta"] = "other"
    else:
        context[mapped_key] = coerced
    if mapped_key == "hero_photo_id" and coerced == "pick":
        context["pending_decision"] = "hero_photo"
        return context
    context["pending_decision"] = None
    context["pending_prompt"] = None
    asked_key = "hero_photo" if mapped_key == "hero_photo_id" else key
    return _mark_asked(context, asked_key)


def merge_prompt_decisions(context, parsed, *, pending_key=None, overwrite=True):
    context = dict(context or empty_context())
    parsed = parsed or {}
    if parsed.get("auto"):
        target = pending_key or None
        if target == "hero_photo":
            target = "hero_photo_id"
        if target:
            context[target] = "auto"
            context = _mark_asked(context, pending_key)
        context["pending_decision"] = None
        context["pending_prompt"] = None
        return context
    mapping = (
        ("include_agent", "include_agent"),
        ("channel", "channel"),
        ("style", "style"),
        ("tone", "tone"),
        ("cta", "cta"),
        ("hero_photo_id", "hero_photo"),
    )
    for field, asked_key in mapping:
        value = parsed.get(field)
        if value is None:
            continue
        if field == "cta" and value == "ask":
            continue
        current = context.get(field)
        if not overwrite and current not in (None, "auto"):
            continue
        context[field] = value
        context = _mark_asked(context, asked_key)
    if context.get("hero_photo_id") == "pick":
        context["pending_decision"] = "hero_photo"
        return context
    context["pending_decision"] = None
    context["pending_prompt"] = None
    return context


def resolve_context(intent, conversation, prompt, listing=None, *, form_key=None, form_value=None):
    context = conversation_context(conversation)
    parsed = parse_prompt_decisions(prompt)
    pending = context.get("pending_decision")
    if form_key:
        work_prompt = context.get("pending_prompt") or prompt
        context = apply_form_decision(context, form_key, form_value)
        parsed = parse_prompt_decisions(work_prompt)
        context = merge_prompt_decisions(context, parsed, overwrite=False)
        context["_work_prompt"] = work_prompt
    elif pending and looks_like_decision_reply(prompt, pending, parsed):
        work_prompt = context.get("pending_prompt") or prompt
        context = merge_prompt_decisions(context, parsed, pending_key=pending)
        context["_work_prompt"] = work_prompt
    else:
        if pending and _CREATE_RE.search(_fold(prompt)):
            auto_key = "hero_photo_id" if pending == "hero_photo" else pending
            if context.get(auto_key) is None:
                context[auto_key] = "auto"
            context = _mark_asked(context, pending)
            context["pending_decision"] = None
            context["pending_prompt"] = None
        context = merge_prompt_decisions(context, parsed)
        context["_work_prompt"] = prompt
    if intent.get("property_id"):
        context["property_id"] = intent["property_id"]
    if intent.get("tone") and not context.get("tone"):
        context["tone"] = intent["tone"]
    creative = intent.get("creative_style") or intent.get("style")
    if creative and context.get("style") is None:
        context["style"] = "commercial" if creative == "commercial" else creative
        context = _mark_asked(context, "style")
    if intent.get("include_agent") is not None and context.get("include_agent") is None:
        context["include_agent"] = intent["include_agent"]
        context = _mark_asked(context, "include_agent")
    if intent.get("channel_explicit") and intent.get("channel"):
        context["channel"] = intent["channel"]
        context = _mark_asked(context, "channel")
    elif context.get("channel") is None and intent.get("channel_explicit"):
        mapped = {
            "story": "instagram_story",
            "carousel": "carousel",
            "whatsapp": "whatsapp",
            "post": "instagram_post",
        }.get(intent.get("content_type"))
        if mapped:
            context["channel"] = mapped
            context = _mark_asked(context, "channel")
    latest = parse_prompt_decisions(prompt)
    if latest.get("channel_explicit") and latest.get("channel"):
        context["channel"] = latest["channel"]
        context = _mark_asked(context, "channel")
    if latest.get("style") and latest.get("style") != "auto":
        context["style"] = latest["style"]
        context["layout_template"] = TEMPLATE_BY_STYLE.get(
            latest["style"], DEFAULT_LAYOUT_TEMPLATE
        )
        context = _mark_asked(context, "style")
    if latest.get("include_agent") is not None:
        context["include_agent"] = latest["include_agent"]
        context = _mark_asked(context, "include_agent")
    return context


def apply_intelligent_defaults(context, listing=None):
    context = dict(context or empty_context())
    listing = listing or {}
    purpose = _purpose(listing)
    if context.get("channel") in (None, "auto"):
        context["channel"] = "instagram_post"
    if context.get("style") in (None, "auto"):
        context["style"] = "commercial"
        if not context.get("tone"):
            context["tone"] = "close" if purpose == "rent" else "commercial"
    if context.get("cta") in (None, "auto", "suggested"):
        context["cta"] = DEFAULT_CTA_RENT if purpose == "rent" else DEFAULT_CTA_SALE
    if context.get("include_agent") in (None, "auto"):
        agent = listing.get("agent") or {}
        context["include_agent"] = bool(agent.get("name") or agent.get("has_photo"))
    if context.get("hero_photo_id") in (None, "auto", "pick"):
        context["hero_photo_id"] = "auto"
    context["layout_template"] = TEMPLATE_BY_STYLE.get(
        context.get("style"), DEFAULT_LAYOUT_TEMPLATE
    )
    return context


def generation_style_tone(context):
    style = (context or {}).get("style")
    mapped = GENERATION_STYLE.get(style) or GENERATION_STYLE.get("premium")
    tone = (context or {}).get("tone") or mapped[1]
    return mapped[0], tone


def content_type_from_channel(context, fallback="post"):
    channel = (context or {}).get("channel")
    return CHANNEL_TO_TYPE.get(channel) or fallback


def action_from_channel(context, fallback="generate_post_image"):
    channel = (context or {}).get("channel")
    return CHANNEL_TO_ACTION.get(channel) or fallback


def _purpose(listing):
    raw = _fold(str((listing or {}).get("purpose") or ""))
    if raw in {"rent", "alquiler", "rental", "rent_property"}:
        return "rent"
    return "sale"


def _agent_usable(listing):
    agent = (listing or {}).get("agent") or {}
    if not agent.get("has_photo"):
        return False
    return bool(agent.get("name") or agent.get("whatsapp") or agent.get("email"))


def _already_asked(context, key):
    return key in (context.get("asked") or [])


def _resolved(value):
    return value not in (None, "", "ask")


def should_clarify(intent):
    action = (intent or {}).get("action")
    if action in SKIP_CLARIFY_ACTIONS:
        return False
    if (intent or {}).get("property_status") in {"none", "ambiguous"}:
        return False
    if (intent or {}).get("needs_property"):
        return False
    return action in {
        "generate_post_image",
        "generate_story_image",
        "generate_carousel",
        "generate_reel_storyboard",
        "generate_whatsapp",
    }


def determine_missing_decisions(context, listing, intent):
    if not should_clarify(intent):
        return []
    missing = []
    if context.get("cta") == "ask" and not _already_asked(context, "cta"):
        missing.append("cta")
    if context.get("hero_photo_id") == "pick" and not _already_asked(context, "hero_photo"):
        missing.append("hero_photo")
    return missing


def listing_label(listing, intent=None):
    address = ((listing or {}).get("address") or "").strip()
    query = ((intent or {}).get("property_query") or "").strip()
    if address:
        return address
    if query:
        return " ".join(part.capitalize() if not part.isdigit() else part for part in query.split())
    return ""


def question_spec(key, *, language, listing=None, intent=None, context=None):
    from modules.i18n import translate

    def t(item, **kwargs):
        return translate(item, language=language, **kwargs)

    found = ""
    asked = (context or {}).get("asked") or []
    if (
        key == "include_agent"
        and not asked
        and (intent or {}).get("property_status") in {"resolved", "selected"}
    ):
        label = listing_label(listing, intent)
        if label:
            found = t("marketing_ia_found_property", address=label) + "\n"
    if key == "include_agent":
        return {
            "key": key,
            "text": found + t("marketing_ia_ask_agent"),
            "options": [
                {"value": "true", "label": t("marketing_ia_opt_agent_yes")},
                {"value": "false", "label": t("marketing_ia_opt_agent_no")},
            ],
        }
    if key == "channel":
        return {
            "key": key,
            "text": t("marketing_ia_ask_channel"),
            "options": [
                {"value": "instagram_post", "label": t("marketing_ia_opt_channel_post")},
                {"value": "instagram_story", "label": t("marketing_ia_opt_channel_story")},
                {"value": "carousel", "label": t("marketing_ia_opt_channel_carousel")},
                {"value": "whatsapp", "label": t("marketing_ia_opt_channel_whatsapp")},
                {"value": "auto", "label": t("marketing_ia_opt_auto")},
            ],
        }
    if key == "style":
        return {
            "key": key,
            "text": t("marketing_ia_ask_style"),
            "options": [
                {"value": "premium", "label": t("marketing_ia_opt_style_premium")},
                {"value": "commercial", "label": t("marketing_ia_opt_style_commercial")},
                {"value": "minimal", "label": t("marketing_ia_opt_style_minimal")},
                {"value": "modern", "label": t("marketing_ia_opt_style_modern")},
                {"value": "auto", "label": t("marketing_ia_opt_auto")},
            ],
        }
    if key == "cta":
        suggested = DEFAULT_CTA_RENT if _purpose(listing) == "rent" else DEFAULT_CTA_SALE
        return {
            "key": key,
            "text": t("marketing_ia_ask_cta", cta=suggested),
            "options": [
                {"value": "suggested", "label": t("marketing_ia_opt_cta_yes")},
                {"value": "other", "label": t("marketing_ia_opt_cta_other")},
                {"value": "auto", "label": t("marketing_ia_opt_auto")},
            ],
            "suggested_cta": suggested,
        }
    if key == "hero_photo":
        photos = (listing or {}).get("photos") or []
        if (context or {}).get("hero_photo_id") == "pick" and photos:
            return {
                "key": key,
                "text": t("marketing_ia_ask_hero_pick"),
                "options": [
                    {
                        "value": str(item.get("id")),
                        "label": item.get("label") or str(item.get("id")),
                        "cover_url": item.get("url"),
                    }
                    for item in photos[:8]
                    if item.get("id")
                ],
                "as_cards": True,
            }
        return {
            "key": key,
            "text": t("marketing_ia_ask_hero"),
            "options": [
                {"value": "auto", "label": t("marketing_ia_opt_hero_auto")},
                {"value": "pick", "label": t("marketing_ia_opt_hero_pick")},
            ],
        }
    return {"key": key, "text": "", "options": []}


def persistable_context(context):
    data = conversation_context({"context": context or {}})
    data.pop("_work_prompt", None)
    return data
