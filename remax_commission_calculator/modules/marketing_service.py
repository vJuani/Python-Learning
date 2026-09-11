"""Marketing IA orchestration. One prompt → async batch → composed assets."""

from __future__ import annotations

import logging
import os
import re
import threading
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from modules.auth import is_admin, is_agent
from modules.config import get_private_upload_root
from modules.database.marketing_repository import (
    STATUS_GENERATED,
    STATUS_SELECTED,
    create_marketing_asset,
    create_marketing_batch,
    delete_marketing_asset,
    find_batch_by_idempotency,
    get_marketing_asset,
    get_marketing_batch,
    list_expired_marketing_assets,
    list_generation_assets,
    list_marketing_assets,
    update_marketing_asset,
    update_marketing_batch,
)
from modules.database.properties_repository import get_properties, get_property_record
from modules.database.tenant import require_organization_id
from modules.i18n import translate
from modules.marketing_art_director import plan_item
from modules.marketing_composer import compose_marketing_image
from modules.marketing_context import (
    MarketingError,
    assert_marketing_access,
    build_property_marketing_context,
    context_to_snapshot,
    list_wizard_photos,
)
from modules.marketing_image_provider import (
    MarketingImageError,
    finished_ad_prompt,
    get_marketing_image_provider,
    get_marketing_image_provider_name,
)
from modules.marketing_quality import validate_creative
from modules.marketing_references import collect_reference_images
from modules.marketing_photo_selector import select_photos_for_item
from modules.marketing_renderer import FORMAT_SIZES, png_to_pdf_bytes
from modules.marketing_request import (
    DEFAULT_PROMPT,
    MAX_BATCH_ITEMS,
    counts_match_request,
    expand_items,
    parse_marketing_request,
)

logger = logging.getLogger(__name__)


def ensure_json_serializable(value, *, path="root"):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {
            str(key): ensure_json_serializable(nested, path=f"{path}.{key}")
            for key, nested in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            ensure_json_serializable(nested, path=f"{path}[{index}]")
            for index, nested in enumerate(value)
        ]
    if callable(value):
        raise TypeError(f"non-json-serializable callable at {path}")
    raise TypeError(f"non-json-serializable {type(value).__name__} at {path}")

FORMAT_ORDER = ("story", "status", "post", "flyer")
PIPELINE_QUEUED = "queued"
PIPELINE_GENERATING = "generating"
PIPELINE_COMPOSITING = "compositing"
PIPELINE_COMPLETED = "completed"
PIPELINE_FAILED = "failed"
PIPELINE_FAILED_QUALITY = "failed_quality"
_DB_LOCK = threading.Lock()
_WORKERS = {}


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


def _pipeline_status(asset):
    return ((asset or {}).get("options") or {}).get("pipeline_status") or PIPELINE_COMPLETED


def _decorate_asset(asset, language="es"):
    item = dict(asset or {})
    fmt = item.get("format") or "story"
    options = item.get("options") if isinstance(item.get("options"), dict) else {}
    copy = item.get("copy_snapshot") if isinstance(item.get("copy_snapshot"), dict) else {}
    item["format_label"] = translate(f"marketing_format_{fmt}_short", language=language)
    if item["format_label"] == f"marketing_format_{fmt}_short":
        item["format_label"] = translate(f"marketing_format_{fmt}", language=language)
    item["template_label"] = options.get("visual_direction") or item.get("template") or ""
    item["when_label"] = _relative_day(item.get("created_at"), language)
    item["headline"] = copy.get("headline") or ""
    item["pipeline_status"] = _pipeline_status(item)
    item["failed"] = item["pipeline_status"] in {PIPELINE_FAILED, PIPELINE_FAILED_QUALITY}
    item["ready"] = item["pipeline_status"] == PIPELINE_COMPLETED and bool(item.get("storage_key"))
    item["source"] = options.get("source") or "ai"
    item["sort_index"] = options.get("sort_index") or 0
    item["download_name"] = asset_download_name(item)
    item["options"] = options
    item["copy_snapshot"] = copy
    return item


def _safe_decorate_asset(item, language="es"):
    try:
        return _decorate_asset(item, language)
    except Exception:
        logger.exception(
            "Marketing generation item failed batch_id=%s item_id=%s stage=%s",
            (item or {}).get("generation_id"),
            (item or {}).get("id"),
            "rendering",
        )
        fallback = dict(item or {})
        fallback["pipeline_status"] = PIPELINE_FAILED
        fallback["failed"] = True
        fallback["ready"] = False
        fallback["format_label"] = fallback.get("format") or ""
        fallback["template_label"] = ""
        fallback["headline"] = ""
        fallback["when_label"] = ""
        fallback["options"] = fallback.get("options") if isinstance(fallback.get("options"), dict) else {}
        fallback["copy_snapshot"] = (
            fallback.get("copy_snapshot") if isinstance(fallback.get("copy_snapshot"), dict) else {}
        )
        return fallback


def _group_assets(assets, language="es"):
    grouped = {fmt: [] for fmt in FORMAT_ORDER}
    for asset in assets:
        fmt = asset.get("format")
        if fmt not in grouped:
            grouped[fmt] = []
        grouped[fmt].append(asset)
    groups = []
    for fmt in list(grouped):
        items = grouped[fmt]
        if not items:
            continue
        groups.append(
            {
                "format": fmt,
                "label": translate(f"marketing_group_{fmt}", language=language),
                "assets": items,
            }
        )
    return groups


def list_marketing_home(organization_id, user, *, language="es"):
    organization_id = require_organization_id(organization_id)
    agent_id = scoped_agent_id(user)
    items = list_marketing_assets(organization_id, agent_id=agent_id)
    history = [
        _decorate_asset(item, language)
        for item in items
        if item.get("status") != "archived" and (item.get("storage_key") or _pipeline_status(item) == PIPELINE_FAILED)
    ]
    properties = get_properties(
        organization_id,
        agent_id=agent_id,
        include_all_statuses=False,
    )
    return {
        "history": history[:36],
        "property_count": len(properties),
        "properties": properties,
        "can_create": bool(properties),
        "default_prompt": DEFAULT_PROMPT,
        "idempotency_key": uuid.uuid4().hex,
    }


def prepare_create_view(
    organization_id,
    user,
    *,
    property_id=None,
    prompt=None,
    language="es",
    generation_id=None,
):
    organization_id = require_organization_id(organization_id)
    cleanup_expired_marketing_assets(organization_id)
    if generation_id:
        view = get_batch_view(organization_id, user, generation_id, language=language)
        if property_id is None:
            property_id = view.get("property_id")
    else:
        view = {
            "batch": None,
            "generation_id": None,
            "assets": [],
            "groups": [],
            "creating": False,
            "total": 0,
        }
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
    cover = None
    if property_data:
        context = build_property_marketing_context(property_data, language=language)
        missing_photos = context["photo_count"] == 0
        photos = list_wizard_photos(property_data)
        cover = next((item for item in photos if item.get("is_cover") or item.get("selected")), None)
        if cover is None and photos:
            cover = photos[0]
    view.update(
        {
            "property": property_data,
            "properties": properties,
            "context": context,
            "missing_photos": missing_photos,
            "cover": cover,
            "prompt": (prompt or view.get("prompt") or "").strip()
            or (DEFAULT_PROMPT if property_data else ""),
            "default_prompt": DEFAULT_PROMPT,
            "idempotency_key": uuid.uuid4().hex,
            "can_create": bool(property_data or properties),
        }
    )
    return view


def _truthy(value, *, default=True):
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "off", "false", ""}


def _options_from_request(parsed, context):
    facts = (context or {}).get("facts") or {}
    policy = facts.get("price_policy") or {}
    show_price = parsed.get("show_price") is not False
    if policy.get("private"):
        show_price = False
    include_agent = parsed.get("with_agent") is not False
    presentation = parsed.get("agent_presentation") or {}
    show_agent_photo = include_agent and (
        parsed.get("with_agent_photo") is not False and presentation.get("show_photo", True)
    )
    return {
        "show_price": show_price,
        "include_agent": include_agent,
        "show_agent_photo": show_agent_photo,
        "show_name": presentation.get("show_name", True),
        "show_phone": presentation.get("show_phone", True),
        "show_email": presentation.get("show_email", False),
        "show_features": parsed.get("copy_density") != "none",
        "photo_ids": [],
        "copy_density": parsed.get("copy_density") or "very_low",
        "variation_strength": parsed.get("variation_strength") or "high",
        "temporary": True,
        "agent_presentation": presentation,
    }


def marketing_ttl_hours():
    try:
        value = int(os.environ.get("MARKETING_ASSET_TTL_HOURS") or 6)
    except (TypeError, ValueError):
        value = 6
    return max(1, min(24, value))


def _expires_at_iso():
    return (datetime.utcnow() + timedelta(hours=marketing_ttl_hours())).replace(
        microsecond=0
    ).isoformat()


def asset_download_name(asset, *, kind="png"):
    facts = ((asset or {}).get("property_snapshot") or {}).get("facts") or {}
    address = facts.get("title") or (asset or {}).get("property_address") or "propiedad"
    folded = unicodedata.normalize("NFKD", str(address))
    ascii_text = folded.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-") or "propiedad"
    fmt = str((asset or {}).get("format") or "story")
    index = ((asset or {}).get("options") or {}).get("format_index") or 1
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = 1
    ext = "pdf" if kind == "pdf" else "png"
    return f"{slug}-{fmt}-{index:02d}.{ext}"


def cleanup_expired_marketing_assets(organization_id=None, *, limit=40):
    removed = 0
    try:
        expired = list_expired_marketing_assets(organization_id, limit=limit)
    except Exception:
        logger.info("marketing cleanup skipped")
        return 0
    root = Path(get_private_upload_root())
    for asset in expired:
        for key in (asset.get("storage_key"), asset.get("pdf_storage_key")):
            if not key:
                continue
            path = root / key
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                logger.info("marketing cleanup file skipped")
        try:
            delete_marketing_asset(asset["id"], asset["organization_id"])
            removed += 1
        except Exception:
            logger.info("marketing cleanup row skipped")
    if removed:
        logger.info("marketing cleanup removed=%s", removed)
    return removed


def conversation_context(asset=None, *, prompt="", batch_id=None):
    options = (asset or {}).get("options") or {}
    return {
        "property_id": (asset or {}).get("property_id"),
        "batch_id": batch_id or (asset or {}).get("generation_id"),
        "selected_asset_id": (asset or {}).get("id"),
        "previous_prompt": options.get("prompt") or prompt,
        "previous_art_direction": options.get("visual_direction"),
    }


def _write_bytes(organization_id, asset_id, filename, payload, *, generation_id=None):
    folder = "tmp" if generation_id else "assets"
    root = (
        Path(get_private_upload_root())
        / "organizations"
        / str(organization_id)
        / "marketing"
        / folder
        / str(generation_id or asset_id)
    )
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{asset_id}-{filename}"
    path.write_bytes(payload)
    return str(path.relative_to(get_private_upload_root())).replace("\\", "/")


def _run_sync():
    flag = (os.environ.get("MARKETING_SYNC") or "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    return get_marketing_image_provider_name() == "mock"


def _concurrency():
    try:
        value = int(os.environ.get("MARKETING_CONCURRENCY") or 2)
    except (TypeError, ValueError):
        value = 2
    return max(1, min(2, value))


def _items_from_request(parsed, *, reference=None):
    items = expand_items(parsed)
    if items:
        if parsed.get("explicit_formats") and not counts_match_request(parsed, items):
            logger.info("marketing counts mismatch explicit request")
        return items
    if reference:
        return [{"format": reference.get("format") or "story", "index": 1}]
    return [{"format": "story", "index": 1}]


def _context_from_asset(asset):
    snapshot = asset.get("property_snapshot") or {}
    options = asset.get("options") or {}
    return {
        "facts": snapshot.get("facts") or {},
        "photos": snapshot.get("photos") or [],
        "photo_count": snapshot.get("photo_count") or 0,
        "cover_id": snapshot.get("cover_id"),
        "agent": asset.get("agent_branding_snapshot") or {},
        "include_agent": options.get("include_agent"),
        "language": snapshot.get("language") or "es",
    }


def _art_from_asset(asset):
    copy = asset.get("copy_snapshot") or {}
    options = asset.get("options") or {}
    return {
        "visual_direction": options.get("visual_direction") or asset.get("template"),
        "creative_brief": options.get("creative_brief") or options.get("background_style"),
        "background_style": options.get("background_style"),
        "layout": options.get("layout") or {},
        "headline": copy.get("headline") or "",
        "short_hook": copy.get("subheadline") or "",
        "cta": copy.get("cta") or "",
        "text_theme": (options.get("layout") or {}).get("text_theme") or "dark",
    }


def _update_options(asset, **fields):
    options = dict(asset.get("options") or {})
    options.update(fields)
    with _DB_LOCK:
        update_marketing_asset(
            asset["id"],
            asset["organization_id"],
            options_json=ensure_json_serializable(options, path="options"),
        )
    return get_marketing_asset(asset["id"], asset["organization_id"])


def _process_item(organization_id, asset_id, *, retry=False):
    stage = "asset_loading"
    asset = get_marketing_asset(asset_id, organization_id)
    if asset is None:
        return None
    batch_id = asset.get("generation_id")
    asset = _update_options(asset, pipeline_status=PIPELINE_GENERATING, error=None)
    try:
        stage = "references"
        context = _context_from_asset(asset)
        options = dict(asset.get("options") or {})
        selected = select_photos_for_item(
            context.get("photos") or [],
            fmt=asset["format"],
            index=max(0, int(options.get("format_index") or 1) - 1),
        )
        if selected:
            context["photos"] = selected
            options["photo_ids"] = [item.get("id") for item in selected if item.get("id")]
        packed = collect_reference_images(context, options)
        references = packed["references"]
        agent_sent = any(item.get("role") == "agent" for item in references)
        logger.info(
            "marketing item=%s property_agent_id=%s agent_branding_found=%s "
            "agent_photo_found=%s agent_photo_variant=%s agent_photo_loaded=%s "
            "agent_photo_sent_to_provider=%s",
            asset_id,
            packed.get("property_agent_id"),
            packed.get("agent_branding_found"),
            packed.get("agent_photo_present"),
            packed.get("agent_photo_variant"),
            packed.get("agent_photo_loaded"),
            agent_sent,
        )
        options["property_agent_id"] = packed.get("property_agent_id")
        options["agent_branding_found"] = packed.get("agent_branding_found")
        options["agent_photo_present"] = packed.get("agent_photo_present")
        options["agent_photo_variant"] = packed.get("agent_photo_variant")
        options["agent_photo_loaded"] = packed.get("agent_photo_loaded")
        options["agent_photo_sent_to_provider"] = agent_sent
        stage = "image_generation"
        provider = get_marketing_image_provider()
        art = _art_from_asset(asset)
        fmt = asset["format"]
        size = FORMAT_SIZES.get(fmt) or FORMAT_SIZES["story"]
        prompt = finished_ad_prompt(
            art,
            fmt,
            references=references,
            options=options,
            used_directions=[options.get("visual_direction")],
        )
        generated = provider.generate_creative(
            prompt=prompt,
            size=size,
            visual_direction=art.get("visual_direction"),
            references=references,
        )
        stage = "quality"
        quality = validate_creative(
            generated,
            size=size,
            options=options,
            references=references,
            agent_photo_sent=agent_sent,
        )
        options["quality_score"] = quality.get("score")
        if not quality.get("ok"):
            if not retry:
                logger.info("marketing quality retry item=%s", asset_id)
                return _process_item(organization_id, asset_id, retry=True)
            options["pipeline_status"] = PIPELINE_FAILED_QUALITY
            options["error"] = "marketing_err_quality"
            with _DB_LOCK:
                update_marketing_asset(
                    asset_id,
                    organization_id,
                    storage_key=None,
                    pdf_storage_key=None,
                    options_json=ensure_json_serializable(options, path="options"),
                )
            return get_marketing_asset(asset_id, organization_id)
        stage = "composition"
        asset = _update_options(
            asset,
            pipeline_status=PIPELINE_COMPOSITING,
            source=get_marketing_image_provider_name(),
            agent_photo_sent_to_provider=agent_sent,
        )
        art["background_png"] = generated
        options = dict(asset.get("options") or {}) | options
        png_bytes, _size, compose_meta = compose_marketing_image(context, art, fmt=fmt, options=options)
        agent_composited = bool(compose_meta.get("agent_photo_composited"))
        options["agent_photo_composited"] = agent_composited
        logger.info(
            "marketing item=%s agent_photo_composited=%s",
            asset_id,
            agent_composited,
        )
        final_quality = validate_creative(
            png_bytes,
            size=size,
            options=options,
            references=references,
            agent_photo_sent=agent_sent,
            agent_photo_composited=agent_composited,
        )
        options["quality_score"] = final_quality.get("score")
        if not final_quality.get("ok"):
            if not retry:
                logger.info("marketing quality retry after compose item=%s", asset_id)
                return _process_item(organization_id, asset_id, retry=True)
            options["pipeline_status"] = PIPELINE_FAILED_QUALITY
            options["error"] = "marketing_err_quality"
            with _DB_LOCK:
                update_marketing_asset(
                    asset_id,
                    organization_id,
                    storage_key=None,
                    pdf_storage_key=None,
                    options_json=ensure_json_serializable(options, path="options"),
                )
            return get_marketing_asset(asset_id, organization_id)
        stage = "persistence"
        storage_key = _write_bytes(
            organization_id,
            asset_id,
            "creative.png",
            png_bytes,
            generation_id=batch_id,
        )
        pdf_key = None
        if fmt == "flyer":
            pdf_key = _write_bytes(
                organization_id,
                asset_id,
                "creative.pdf",
                png_to_pdf_bytes(png_bytes),
                generation_id=batch_id,
            )
        options["pipeline_status"] = PIPELINE_COMPLETED
        options["error"] = None
        options["source"] = get_marketing_image_provider_name()
        options["model"] = os.environ.get("MARKETING_IMAGE_MODEL") or "gpt-image-2.5-sunburst"
        with _DB_LOCK:
            update_marketing_asset(
                asset_id,
                organization_id,
                storage_key=storage_key,
                pdf_storage_key=pdf_key,
                options_json=ensure_json_serializable(options, path="options"),
            )
    except Exception:
        logger.exception(
            "Marketing generation item failed batch_id=%s item_id=%s stage=%s",
            batch_id,
            asset_id,
            stage,
        )
        asset = get_marketing_asset(asset_id, organization_id) or asset
        _update_options(asset, pipeline_status=PIPELINE_FAILED, error="marketing_err_item_failed")
    return get_marketing_asset(asset_id, organization_id)


def _refresh_batch_status(organization_id, batch_id):
    assets = list_generation_assets(organization_id, batch_id)
    if not assets:
        return
    statuses = {_pipeline_status(item) for item in assets}
    if statuses <= {PIPELINE_COMPLETED}:
        status = "completed"
    elif statuses <= {PIPELINE_FAILED, PIPELINE_FAILED_QUALITY}:
        status = "failed"
    elif PIPELINE_COMPLETED in statuses or PIPELINE_FAILED in statuses or PIPELINE_FAILED_QUALITY in statuses:
        if statuses <= {PIPELINE_COMPLETED, PIPELINE_FAILED, PIPELINE_FAILED_QUALITY}:
            status = "completed"
        else:
            status = "generating"
    else:
        status = "generating"
    usage = {
        "items": len(assets),
        "completed": sum(1 for item in assets if _pipeline_status(item) == PIPELINE_COMPLETED),
        "failed": sum(1 for item in assets if _pipeline_status(item) == PIPELINE_FAILED),
        "provider": get_marketing_image_provider_name(),
    }
    batch = get_marketing_batch(batch_id, organization_id) or {}
    request = dict(batch.get("request") or {})
    request["usage"] = usage
    with _DB_LOCK:
        update_marketing_batch(
            batch_id,
            organization_id,
            status=status,
            request_json=request,
        )


def _run_batch(organization_id, batch_id):
    assets = list_generation_assets(organization_id, batch_id)
    update_marketing_batch(batch_id, organization_id, status="generating")
    limit = _concurrency()
    if _run_sync() or limit == 1:
        for asset in assets:
            if _pipeline_status(asset) == PIPELINE_FAILED:
                continue
            _process_item(organization_id, asset["id"])
            _refresh_batch_status(organization_id, batch_id)
        _refresh_batch_status(organization_id, batch_id)
        return
    semaphore = threading.Semaphore(limit)

    def _worker(asset_id):
        with semaphore:
            _process_item(organization_id, asset_id)
            _refresh_batch_status(organization_id, batch_id)

    threads = []
    for asset in assets:
        if _pipeline_status(asset) == PIPELINE_FAILED:
            continue
        thread = threading.Thread(
            target=_worker,
            args=(asset["id"],),
            daemon=True,
            name=f"mkt-item-{asset['id']}",
        )
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(timeout=180)
    _refresh_batch_status(organization_id, batch_id)


def start_marketing_batch(
    organization_id,
    user,
    *,
    property_id,
    prompt,
    language="es",
    idempotency_key=None,
    reference_asset_id=None,
    variation=False,
    include_agent=None,
):
    organization_id = require_organization_id(organization_id)
    cleanup_expired_marketing_assets(organization_id)
    token = (idempotency_key or "").strip() or None
    if token:
        existing = find_batch_by_idempotency(organization_id, token)
        if existing:
            return get_batch_view(organization_id, user, existing["id"], language=language)
    property_data = get_property_record(property_id, organization_id)
    if property_data is None:
        raise MarketingError("marketing_err_property_missing", 404)
    assert_marketing_access(user, property_data)
    reference = None
    if reference_asset_id:
        reference = require_asset_access(
            user, get_marketing_asset(reference_asset_id, organization_id)
        )
        variation = True
    parsed = parse_marketing_request(prompt, language=language, variation=variation)
    if include_agent is False:
        parsed["with_agent"] = False
        parsed["with_agent_photo"] = False
    if reference:
        folded = (parsed.get("prompt") or "").lower()
        if variation and not re.search(r"\d+", parsed.get("prompt") or ""):
            parsed["story_count"] = 0
            parsed["post_count"] = 0
            parsed["flyer_count"] = 0
            parsed["status_count"] = 0
        if not expand_items(parsed):
            fmt = reference.get("format") or "story"
            if re.search(r"historia|story", folded):
                fmt = "story"
            elif re.search(r"\bposts?\b|publicaci", folded):
                fmt = "post"
            elif re.search(r"flyer|folleto", folded):
                fmt = "flyer"
            parsed["story_count"] = 1 if fmt == "story" else 0
            parsed["post_count"] = 1 if fmt == "post" else 0
            parsed["flyer_count"] = 1 if fmt == "flyer" else 0
            parsed["status_count"] = 1 if fmt == "status" else 0
    items = _items_from_request(parsed, reference=reference)
    if len(items) > MAX_BATCH_ITEMS:
        items = items[:MAX_BATCH_ITEMS]
        parsed["capped"] = True
    context = build_property_marketing_context(
        property_data,
        language=language,
        include_agent=parsed.get("with_agent") is not False,
    )
    if context["photo_count"] == 0:
        raise MarketingError("marketing_err_no_photos", 400)
    options = _options_from_request(parsed, context)
    if reference:
        ref_opts = dict(reference.get("options") or {})
        if parsed.get("with_agent") is False:
            options["include_agent"] = False
            options["show_agent_photo"] = False
        elif ref_opts:
            options["include_agent"] = options.get("include_agent", ref_opts.get("include_agent", True))
        if parsed.get("show_price") is False:
            options["show_price"] = False
    snapshot = context_to_snapshot(context)
    batch_id = uuid.uuid4().hex
    request_payload = dict(parsed)
    request_payload["item_count"] = len(items)
    request_payload["reference_asset_id"] = reference_asset_id
    request_payload["conversation"] = conversation_context(
        reference,
        prompt=parsed.get("prompt") or prompt,
        batch_id=batch_id,
    )
    request_payload["temporary"] = True
    expires_at = _expires_at_iso()
    try:
        create_marketing_batch(
            organization_id,
            batch_id=batch_id,
            property_id=property_data["id"],
            prompt=parsed.get("prompt") or prompt,
            request=ensure_json_serializable(request_payload, path="request"),
            idempotency_key=token,
            agent_id=property_data.get("agent_id"),
            created_by_user_id=(user or {}).get("id"),
            status="queued",
        )
    except Exception:
        if token:
            existing = find_batch_by_idempotency(organization_id, token)
            if existing:
                return get_batch_view(organization_id, user, existing["id"], language=language)
        raise
    used = set()
    if reference:
        used.add(((reference.get("options") or {}).get("visual_direction")) or "")
    sort_index = 0
    for item in items:
        sort_index += 1
        try:
            art = plan_item(
                context,
                parsed,
                fmt=item["format"],
                index=item["index"],
                used_directions=used,
            )
            item_options = dict(options)
            selected_photos = select_photos_for_item(
                context.get("photos") or [],
                fmt=item["format"],
                index=max(0, int(item["index"]) - 1),
            )
            item_options.update(
                {
                    "pipeline_status": PIPELINE_QUEUED,
                    "visual_direction": art.get("visual_direction"),
                    "creative_brief": art.get("creative_brief"),
                    "background_style": art.get("background_style"),
                    "layout": art.get("layout") or {},
                    "sort_index": sort_index,
                    "format_index": item["index"],
                    "prompt": parsed.get("prompt") or prompt,
                    "reference_asset_id": reference_asset_id,
                    "source": get_marketing_image_provider_name(),
                    "temporary": True,
                    "expires_at": expires_at,
                    "photo_ids": [photo.get("id") for photo in selected_photos if photo.get("id")],
                }
            )
            copy = {
                "headline": art.get("headline") or "",
                "subheadline": art.get("short_hook") or "",
                "description": "",
                "cta": art.get("cta") or "",
                "caption": art.get("headline") or "",
                "hashtags": [],
            }
            create_marketing_asset(
                organization_id,
                property_id=property_data["id"],
                generation_id=batch_id,
                format=item["format"],
                style=art.get("visual_direction") or "premium",
                tone=parsed.get("visual_direction") or "premium varied",
                template=art.get("visual_direction") or "direction",
                copy_snapshot=ensure_json_serializable(copy, path="copy_snapshot"),
                property_snapshot=ensure_json_serializable(snapshot, path="property_snapshot"),
                agent_branding_snapshot=ensure_json_serializable(
                    context.get("agent") or {}, path="agent_branding_snapshot"
                ),
                options=ensure_json_serializable(item_options, path="options"),
                agent_id=property_data.get("agent_id"),
                created_by_user_id=(user or {}).get("id"),
                status=STATUS_GENERATED,
                temporary=True,
                expires_at=expires_at,
            )
        except Exception:
            logger.exception(
                "Marketing generation item failed batch_id=%s item_id=%s stage=%s",
                batch_id,
                None,
                "art_direction",
            )
            create_marketing_asset(
                organization_id,
                property_id=property_data["id"],
                generation_id=batch_id,
                format=item["format"],
                style="premium",
                tone="premium varied",
                template="direction",
                copy_snapshot={},
                property_snapshot=ensure_json_serializable(snapshot, path="property_snapshot"),
                agent_branding_snapshot=ensure_json_serializable(
                    context.get("agent") or {}, path="agent_branding_snapshot"
                ),
                options=ensure_json_serializable(
                    {
                        **options,
                        "pipeline_status": PIPELINE_FAILED,
                        "error": "marketing_err_item_failed",
                        "sort_index": sort_index,
                        "format_index": item["index"],
                    },
                    path="options",
                ),
                agent_id=property_data.get("agent_id"),
                created_by_user_id=(user or {}).get("id"),
                status=STATUS_GENERATED,
                temporary=True,
                expires_at=expires_at,
            )
    if _run_sync():
        _run_batch(organization_id, batch_id)
    else:
        thread = threading.Thread(
            target=_run_batch,
            args=(organization_id, batch_id),
            daemon=True,
            name=f"mkt-batch-{batch_id}",
        )
        _WORKERS[batch_id] = thread
        thread.start()
    return get_batch_view(organization_id, user, batch_id, language=language)


def generate_marketing_proposals(
    organization_id,
    user,
    *,
    property_id,
    form,
    language="es",
):
    form = form or {}
    prompt = (form.get("prompt") or "").strip()
    if not prompt:
        fmt = str(form.get("format") or "pack").strip().lower()
        if fmt == "story":
            prompt = "Haceme 3 historias, todas diferentes, premium, sin descripción larga y usando mi foto."
        elif fmt == "post":
            prompt = "Haceme 3 posts de Instagram, todos diferentes, premium, sin descripción larga y usando mi foto."
        elif fmt == "status":
            prompt = "Haceme 3 estados de WhatsApp, todos diferentes, premium, sin descripción larga y usando mi foto."
        elif fmt == "flyer":
            prompt = "Haceme 3 flyers, todos diferentes, premium, sin descripción larga y usando mi foto."
        else:
            prompt = DEFAULT_PROMPT
    if form.get("include_agent") in {"0", "off", "false"}:
        prompt += " sin agente."
    if form.get("show_price") in {"0", "off", "false"}:
        prompt += " Sin precio."
    include_agent = None
    if form.get("include_agent") in {"0", "off", "false"}:
        include_agent = False
    elif form.get("include_agent") in {"1", "on", "true"}:
        include_agent = True
    previous = os.environ.get("MARKETING_SYNC")
    os.environ["MARKETING_SYNC"] = "1"
    try:
        return start_marketing_batch(
            organization_id,
            user,
            property_id=property_id,
            prompt=prompt,
            language=language,
            idempotency_key=(form.get("idempotency_key") or "").strip() or None,
            include_agent=include_agent,
        )
    finally:
        if previous is None:
            os.environ.pop("MARKETING_SYNC", None)
        else:
            os.environ["MARKETING_SYNC"] = previous


def get_batch_view(organization_id, user, batch_id, *, language="es"):
    batch = get_marketing_batch(batch_id, organization_id)
    assets = [
        require_asset_access(user, item)
        for item in list_generation_assets(organization_id, batch_id)
    ]
    if batch is None and not assets:
        raise MarketingError("marketing_err_asset_missing", 404)
    decorated = [_safe_decorate_asset(item, language) for item in assets]
    decorated.sort(key=lambda item: (FORMAT_ORDER.index(item["format"]) if item["format"] in FORMAT_ORDER else 9, item.get("sort_index") or 0))
    first = decorated[0] if decorated else {}
    completed = sum(1 for item in decorated if item.get("ready"))
    failed = sum(1 for item in decorated if item.get("failed"))
    total = len(decorated)
    status = (batch or {}).get("status") or "queued"
    if total and completed + failed == total:
        status = "completed" if failed < total else "failed"
    return {
        "batch": batch or {"id": batch_id, "status": status, "prompt": first.get("options", {}).get("prompt")},
        "generation_id": batch_id,
        "assets": decorated,
        "groups": _group_assets(decorated, language),
        "status": status,
        "total": total,
        "completed": completed,
        "failed": failed,
        "prompt": (batch or {}).get("prompt") or "",
        "request": (batch or {}).get("request") or {},
        "property_id": (batch or {}).get("property_id") or first.get("property_id"),
        "property_address": first.get("property_address"),
        "creating": status in {"queued", "generating"},
        "single": total == 1,
        "single_format": first.get("format") if total == 1 else None,
    }


def get_generation_view(organization_id, user, generation_id, *, language="es"):
    return get_batch_view(organization_id, user, generation_id, language=language)


def get_batch_status(organization_id, user, batch_id, *, language="es"):
    view = get_batch_view(organization_id, user, batch_id, language=language)
    items = []
    for asset in view["assets"]:
        items.append(
            {
                "id": asset["id"],
                "format": asset["format"],
                "format_label": asset["format_label"],
                "index": (asset.get("options") or {}).get("format_index") or asset.get("sort_index"),
                "label": f"{asset['format_label']} {(asset.get('options') or {}).get('format_index') or ''}".strip(),
                "pipeline_status": asset["pipeline_status"],
                "ready": asset["ready"],
                "failed": asset["failed"],
                "preview_url": f"/marketing/assets/{asset['id']}/preview" if asset.get("storage_key") else None,
                "download_url": f"/marketing/assets/{asset['id']}/download.png" if asset.get("ready") else None,
            }
        )
    return {
        "id": view["generation_id"],
        "status": view["status"],
        "total": view["total"],
        "completed": view["completed"],
        "failed": view["failed"],
        "creating": view["creating"],
        "steps": {
            "analyze": True,
            "photos": True,
            "creating": view["creating"],
        },
        "items": items,
        "groups": [
            {
                "format": group["format"],
                "label": group["label"],
                "assets": [item["id"] for item in group["assets"]],
            }
            for group in view["groups"]
        ],
    }


def retry_marketing_item(organization_id, user, asset_id, *, language="es"):
    asset = require_asset_access(user, get_marketing_asset(asset_id, organization_id))
    _update_options(asset, pipeline_status=PIPELINE_QUEUED, error=None)
    _process_item(organization_id, asset_id)
    _refresh_batch_status(organization_id, asset["generation_id"])
    return get_asset_view(organization_id, user, asset_id, language=language)


def vary_marketing_asset(organization_id, user, asset_id, prompt, *, language="es"):
    asset = require_asset_access(user, get_marketing_asset(asset_id, organization_id))
    text = (prompt or "").strip()
    if not text:
        raise MarketingError("marketing_err_prompt_missing", 400)
    return start_marketing_batch(
        organization_id,
        user,
        property_id=asset["property_id"],
        prompt=text,
        language=language,
        reference_asset_id=asset_id,
        variation=True,
        idempotency_key=uuid.uuid4().hex,
    )


def select_marketing_asset(organization_id, user, asset_id):
    asset = require_asset_access(user, get_marketing_asset(asset_id, organization_id))
    for sibling in list_generation_assets(organization_id, asset["generation_id"]):
        status = STATUS_SELECTED if sibling["id"] == asset["id"] else STATUS_GENERATED
        update_marketing_asset(sibling["id"], organization_id, status=status)
    return get_marketing_asset(asset_id, organization_id)


def get_asset_view(organization_id, user, asset_id, *, language="es"):
    asset = require_asset_access(user, get_marketing_asset(asset_id, organization_id))
    item = _decorate_asset(asset, language)
    item["caption_text"] = caption_text(asset)
    item["width"], item["height"] = asset_dimensions(asset)
    item["download_name"] = asset_download_name(asset)
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
    facts = ((asset.get("property_snapshot") or {}).get("facts") or {})
    parts = [
        copy.get("headline"),
        copy.get("subheadline"),
        facts.get("price_label") if (asset.get("options") or {}).get("show_price") else None,
        " ".join((facts.get("chips") or [])[:4]),
        copy.get("cta"),
    ]
    return "\n".join(part for part in parts if part)


def asset_dimensions(asset):
    return FORMAT_SIZES.get(asset.get("format")) or FORMAT_SIZES["story"]
