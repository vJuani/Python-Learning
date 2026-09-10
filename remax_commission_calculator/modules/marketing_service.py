"""Marketing content orchestration. Routes stay thin."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path

from modules.auth import is_admin, is_agent
from modules.config import get_private_upload_root
from modules.database.marketing_repository import (
    STATUS_GENERATED,
    STATUS_SELECTED,
    create_marketing_asset,
    get_marketing_asset,
    list_generation_assets,
    list_marketing_assets,
    update_marketing_asset,
)
from modules.database.properties_repository import get_properties, get_property_record
from modules.database.tenant import require_organization_id
from modules.i18n import translate
from modules.marketing_context import (
    FORMATS,
    PHOTO_LIMIT,
    STYLES,
    TEMPLATES,
    TONES,
    MarketingError,
    assert_marketing_access,
    build_property_marketing_context,
    context_to_snapshot,
    list_wizard_photos,
)
from modules.marketing_copy import generate_marketing_copy
from modules.marketing_renderer import FORMAT_SIZES, png_to_pdf_bytes, render_marketing_image


def _normalize_choice(value, allowed, default):
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def scoped_agent_id(user):
    if is_agent(user):
        return user.get("agent_id")
    return None


def can_view_asset(user, asset):
    if not user or not asset:
        return False
    if int(user.get("organization_id") or 0) != int(asset.get("organization_id") or 0):
        return False
    if is_admin(user):
        return True
    if is_agent(user):
        return int(user.get("agent_id") or 0) == int(asset.get("agent_id") or 0)
    return False


def require_asset_access(user, asset):
    if asset is None:
        raise MarketingError("marketing_err_asset_missing", 404)
    if not can_view_asset(user, asset):
        raise MarketingError("access_denied", 403)
    return asset


def _relative_day(created_at, language):
    try:
        day = datetime.fromisoformat(str(created_at)[:19]).date()
    except (TypeError, ValueError):
        return created_at or ""
    delta = (date.today() - day).days
    if delta == 0:
        return translate("marketing_today", language=language)
    if delta == 1:
        return translate("marketing_yesterday", language=language)
    return str(created_at)[:10]


def _decorate_asset(asset, language="es"):
    item = dict(asset or {})
    fmt = item.get("format") or "story"
    item["format_label"] = translate(f"marketing_format_{fmt}", language=language)
    item["template_label"] = translate(
        f"marketing_variant_{item.get('template') or 'editorial'}",
        language=language,
    )
    item["when_label"] = _relative_day(item.get("created_at"), language)
    item["headline"] = (item.get("copy_snapshot") or {}).get("headline") or ""
    return item


def list_marketing_home(organization_id, user, *, language="es"):
    organization_id = require_organization_id(organization_id)
    agent_id = scoped_agent_id(user)
    items = list_marketing_assets(organization_id, agent_id=agent_id)
    selected = [item for item in items if item.get("status") == STATUS_SELECTED]
    history = selected or items
    properties = get_properties(
        organization_id,
        agent_id=agent_id,
        include_all_statuses=False,
    )
    return {
        "history": [_decorate_asset(item, language) for item in history[:30]],
        "property_count": len(properties),
        "can_create": bool(properties),
    }


def prepare_create_view(organization_id, user, *, property_id=None, language="es"):
    organization_id = require_organization_id(organization_id)
    property_data = None
    if property_id:
        property_data = get_property_record(property_id, organization_id)
        if property_data is None:
            raise MarketingError("marketing_err_property_missing", 404)
        assert_marketing_access(user, property_data)
    properties = []
    if property_data is None:
        properties = get_properties(
            organization_id,
            agent_id=scoped_agent_id(user),
        )
    context = None
    missing_photos = False
    wizard_photos = []
    if property_data:
        context = build_property_marketing_context(property_data, language=language)
        missing_photos = context["photo_count"] == 0
        wizard_photos = list_wizard_photos(property_data)
    return {
        "property": property_data,
        "properties": properties,
        "context": context,
        "missing_photos": missing_photos,
        "wizard_photos": wizard_photos,
        "formats": FORMATS,
        "styles": STYLES,
        "tones": TONES,
    }


def _form_list(form, key):
    if form is None:
        return []
    if hasattr(form, "getlist"):
        return list(form.getlist(key) or [])
    value = form.get(key) if hasattr(form, "get") else None
    if isinstance(value, str):
        return [item for item in value.split(",") if item]
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _form_last(form, key, default=None):
    values = _form_list(form, key)
    if not values:
        return default
    return values[-1]


def _truthy_flag(form, key, *, default=True):
    raw = _form_last(form, key, "1" if default else "0")
    return str(raw or "").strip() not in {"0", "off", "false", ""}


def _options_from_form(form, context):
    form = form or {}
    facts = (context or {}).get("facts") or {}
    policy = facts.get("price_policy") or {}
    if _form_list(form, "show_price"):
        show_price = _truthy_flag(form, "show_price", default=False)
    else:
        show_price = bool(policy.get("default_show"))
    include_agent = _truthy_flag(form, "include_agent", default=True)
    show_agent_photo = _truthy_flag(form, "show_agent_photo", default=True)
    show_features = _truthy_flag(form, "show_features", default=True)
    photo_ids = _form_list(form, "photo_ids")
    return {
        "show_price": show_price,
        "include_agent": include_agent,
        "show_agent_photo": show_agent_photo,
        "show_features": show_features,
        "photo_ids": [int(item) for item in photo_ids if str(item).isdigit()][:PHOTO_LIMIT],
    }


def _write_bytes(organization_id, asset_id, filename, payload):
    root = (
        Path(get_private_upload_root())
        / "organizations"
        / str(organization_id)
        / "marketing"
        / str(asset_id)
    )
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    path.write_bytes(payload)
    return str(path.relative_to(get_private_upload_root())).replace("\\", "/")


def _render_and_store(asset, context, copy, options):
    fmt = asset["format"]
    png_bytes, size = render_marketing_image(
        context,
        copy,
        fmt=fmt,
        template=asset["template"],
        style=asset["style"],
        options=options,
    )
    storage_key = _write_bytes(asset["organization_id"], asset["id"], "creative.png", png_bytes)
    pdf_key = None
    if fmt == "flyer":
        pdf_key = _write_bytes(
            asset["organization_id"],
            asset["id"],
            "creative.pdf",
            png_to_pdf_bytes(png_bytes),
        )
    update_marketing_asset(
        asset["id"],
        asset["organization_id"],
        storage_key=storage_key,
        pdf_storage_key=pdf_key,
    )
    return get_marketing_asset(asset["id"], asset["organization_id"]), size


def generate_marketing_proposals(
    organization_id,
    user,
    *,
    property_id,
    form,
    language="es",
):
    organization_id = require_organization_id(organization_id)
    property_data = get_property_record(property_id, organization_id)
    if property_data is None:
        raise MarketingError("marketing_err_property_missing", 404)
    assert_marketing_access(user, property_data)
    fmt = _normalize_choice(form.get("format"), FORMATS, "story")
    if fmt == "pack":
        fmt = "story"
    style = _normalize_choice(form.get("style"), STYLES, "elegant")
    tone = _normalize_choice(form.get("tone"), TONES, "professional")
    options = _options_from_form(form, None)
    context = build_property_marketing_context(
        property_data,
        language=language,
        include_agent=options["include_agent"],
        selected_photo_ids=options.get("photo_ids"),
    )
    if context["photo_count"] == 0:
        raise MarketingError("marketing_err_no_photos", 400)
    options = _options_from_form(form, context)
    copy = generate_marketing_copy(context, tone=tone, language=language)
    snapshot = context_to_snapshot(context)
    generation_id = uuid.uuid4().hex
    assets = []
    for template in TEMPLATES:
        asset = create_marketing_asset(
            organization_id,
            property_id=property_data["id"],
            generation_id=generation_id,
            format=fmt,
            style=style,
            tone=tone,
            template=template,
            copy_snapshot=copy,
            property_snapshot=snapshot,
            agent_branding_snapshot=context.get("agent") or {},
            options=options,
            agent_id=property_data.get("agent_id"),
            created_by_user_id=(user or {}).get("id"),
            status=STATUS_GENERATED,
        )
        stored, _size = _render_and_store(asset, context, copy, options)
        assets.append(_decorate_asset(stored, language))
    return {
        "generation_id": generation_id,
        "assets": assets,
        "copy": copy,
        "context": context,
        "options": options,
        "format": fmt,
        "style": style,
        "tone": tone,
    }


def get_generation_view(organization_id, user, generation_id, *, language="es"):
    assets = [
        require_asset_access(user, item)
        for item in list_generation_assets(organization_id, generation_id)
    ]
    if not assets:
        raise MarketingError("marketing_err_asset_missing", 404)
    first = assets[0]
    return {
        "generation_id": generation_id,
        "assets": [_decorate_asset(item, language) for item in assets],
        "copy": first.get("copy_snapshot") or {},
        "options": first.get("options") or {},
        "format": first.get("format"),
        "style": first.get("style"),
        "tone": first.get("tone"),
        "property_id": first.get("property_id"),
        "property_address": first.get("property_address"),
    }


def select_marketing_asset(organization_id, user, asset_id):
    asset = require_asset_access(user, get_marketing_asset(asset_id, organization_id))
    for sibling in list_generation_assets(organization_id, asset["generation_id"]):
        status = STATUS_SELECTED if sibling["id"] == asset["id"] else STATUS_GENERATED
        update_marketing_asset(sibling["id"], organization_id, status=status)
    return get_marketing_asset(asset_id, organization_id)


def update_asset_copy(organization_id, user, asset_id, form, *, language="es"):
    asset = require_asset_access(user, get_marketing_asset(asset_id, organization_id))
    copy = dict(asset.get("copy_snapshot") or {})
    for key in ("headline", "subheadline", "description", "cta", "caption"):
        if key in form:
            copy[key] = (form.get(key) or "").strip()
    options = dict(asset.get("options") or {})
    if _form_list(form, "show_price") or "show_price" in form:
        options["show_price"] = _truthy_flag(form, "show_price", default=False)
    if _form_list(form, "include_agent") or "include_agent" in form:
        options["include_agent"] = _truthy_flag(form, "include_agent", default=True)
    if _form_list(form, "show_agent_photo") or "show_agent_photo" in form:
        options["show_agent_photo"] = _truthy_flag(form, "show_agent_photo", default=True)
    if _form_list(form, "show_features") or "show_features" in form:
        options["show_features"] = _truthy_flag(form, "show_features", default=True)
    update_marketing_asset(
        asset_id,
        organization_id,
        copy_snapshot_json=copy,
        options_json=options,
    )
    asset = get_marketing_asset(asset_id, organization_id)
    context = {
        "facts": (asset.get("property_snapshot") or {}).get("facts") or {},
        "photos": (asset.get("property_snapshot") or {}).get("photos") or [],
        "agent": asset.get("agent_branding_snapshot") or {},
        "include_agent": options.get("include_agent"),
    }
    stored, _size = _render_and_store(asset, context, copy, options)
    return _decorate_asset(stored, language)


def get_asset_view(organization_id, user, asset_id, *, language="es"):
    asset = require_asset_access(user, get_marketing_asset(asset_id, organization_id))
    item = _decorate_asset(asset, language)
    item["caption_text"] = caption_text(asset)
    item["width"], item["height"] = asset_dimensions(asset)
    return item


def resolve_asset_file(asset, *, kind="png"):
    key = asset.get("pdf_storage_key") if kind == "pdf" else asset.get("storage_key")
    if not key:
        return None
    path = Path(get_private_upload_root()) / key
    if path.is_file():
        return path
    return None


def caption_text(asset):
    copy = asset.get("copy_snapshot") or {}
    parts = [
        copy.get("headline"),
        copy.get("description") or copy.get("subheadline"),
        " ".join(copy.get("hashtags") or []),
    ]
    return "\n\n".join(part for part in parts if part)


def asset_dimensions(asset):
    return FORMAT_SIZES.get(asset.get("format")) or FORMAT_SIZES["story"]
