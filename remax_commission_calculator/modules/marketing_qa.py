"""Temporary Marketing AI QA: one raw OpenAI Story, no legacy compositor."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from modules.auth import is_admin
from modules.config import get_private_upload_root
from modules.database.properties_repository import filter_properties, get_property_record
from modules.marketing_context import (
    MarketingError,
    assert_marketing_access,
    build_property_marketing_context,
)
from modules.marketing_image_provider import (
    generate_with_audit,
    get_marketing_image_model,
    get_marketing_image_provider_name,
    log_marketing_pipeline,
    sha256_bytes,
)
from modules.marketing_references import collect_reference_images
from modules.marketing_renderer import FORMAT_SIZES
from modules.openai_image_service import (
    assert_openai_configured,
    build_marketing_image_prompt,
)

logger = logging.getLogger(__name__)

QA_ADDRESS = "Don Bosco 477"
QA_FORMAT = "story"


def find_qa_property(organization_id, user, *, property_id=None, address=QA_ADDRESS):
    if property_id:
        property_data = get_property_record(property_id, organization_id)
        if property_data is None:
            raise MarketingError("marketing_err_property_missing", 404)
        return assert_marketing_access(user, property_data)
    matches = filter_properties(organization_id, address=address)
    if not matches:
        raise MarketingError("marketing_err_property_missing", 404)
    if user and not is_admin(user) and user.get("agent_id"):
        scoped = [
            item
            for item in matches
            if int(item.get("agent_id") or 0) == int(user.get("agent_id") or 0)
        ]
        if scoped:
            matches = scoped
    return assert_marketing_access(user, matches[0])


def run_raw_story_qa(
    organization_id,
    user,
    *,
    property_id=None,
    address=QA_ADDRESS,
    language="es",
):
    """Generate one 9:16 Story and show the raw OpenAI bytes as the final file."""
    assert_openai_configured()
    property_data = find_qa_property(
        organization_id,
        user,
        property_id=property_id,
        address=address,
    )
    context = build_property_marketing_context(
        property_data,
        language=language,
        include_agent=True,
    )
    options = {
        "include_agent": True,
        "show_agent_photo": True,
        "show_price": True,
        "style": "premium",
        "cta": "Consultame",
        "request_text": "Una historia 9:16, pocas palabras, foto real del agente.",
    }
    packed = collect_reference_images(context, options)
    prompt = build_marketing_image_prompt(
        context,
        QA_FORMAT,
        options=options,
        request_text=options["request_text"],
        style="premium",
        cta="Consultame",
        include_price=True,
        include_agent=True,
        variation_index=1,
    )
    size = FORMAT_SIZES[QA_FORMAT]
    audit = generate_with_audit(
        prompt=prompt,
        size=size,
        visual_direction=None,
        references=packed["references"],
        apply_fit=False,
    )
    raw = audit["raw_bytes"]
    shown = raw
    run_id = uuid.uuid4().hex
    folder = (
        Path(get_private_upload_root())
        / "organizations"
        / str(organization_id)
        / "marketing"
        / "qa"
        / run_id
    )
    folder.mkdir(parents=True, exist_ok=True)
    raw_path = folder / "raw_openai_output.png"
    final_path = folder / "final_output.png"
    raw_path.write_bytes(raw)
    final_path.write_bytes(shown)
    raw_hash = sha256_bytes(raw)
    final_hash = sha256_bytes(shown)
    report = {
        "run_id": run_id,
        "property_id": property_data.get("id"),
        "property_address": property_data.get("address"),
        "provider": audit.get("provider") or get_marketing_image_provider_name(),
        "model": audit.get("model") or get_openai_image_model_safe(),
        "endpoint": audit.get("endpoint"),
        "input_image_count": audit.get("input_image_count"),
        "input_roles": audit.get("input_roles") or [],
        "api_size": audit.get("api_size"),
        "raw_size": audit.get("raw_size"),
        "final_size": audit.get("final_size") or audit.get("raw_size"),
        "legacy_compositor": bool(audit.get("legacy_compositor")),
        "legacy_compositor_fn": audit.get("legacy_compositor_fn"),
        "post_process": "none",
        "post_process_fn": None,
        "fallback": bool(audit.get("fallback")),
        "raw_sha256": raw_hash,
        "final_sha256": final_hash,
        "hashes_match": raw_hash == final_hash,
        "png_producer": (
            "MockMarketingImageProvider.generate_creative"
            if audit.get("provider") == "mock"
            else "OpenAI Images API raw bytes (no Pillow compositor)"
        ),
        "png_producer_fn": (
            audit.get("legacy_compositor_fn")
            if audit.get("provider") == "mock"
            else "modules.marketing_qa.run_raw_story_qa → generate_with_audit(apply_fit=False)"
        ),
        "style_reference_sent": any(role == "style" for role in (audit.get("input_roles") or [])),
        "agent_photo_sent": any(role == "agent" for role in (audit.get("input_roles") or [])),
        "property_photo_count": packed.get("property_photo_count") or 0,
        "prompt_chars": len(prompt),
        "raw_relpath": str(raw_path.relative_to(get_private_upload_root())).replace("\\", "/"),
        "final_relpath": str(final_path.relative_to(get_private_upload_root())).replace("\\", "/"),
    }
    if report["hashes_match"]:
        report["difference_reason"] = None
    else:
        report["difference_reason"] = (
            "final_output was modified after the OpenAI response. "
            "Inspect post_process_fn."
        )
    log_marketing_pipeline(report)
    logger.info(
        "marketing_qa raw_story run=%s address=%s hashes_match=%s model=%s endpoint=%s",
        run_id,
        property_data.get("address"),
        report["hashes_match"],
        report["model"],
        report["endpoint"],
    )
    return report


def get_openai_image_model_safe():
    return get_marketing_image_model()


def resolve_qa_file(organization_id, run_id, filename):
    if filename not in {"raw_openai_output.png", "final_output.png"}:
        return None
    path = (
        Path(get_private_upload_root())
        / "organizations"
        / str(organization_id)
        / "marketing"
        / "qa"
        / run_id
        / filename
    )
    if path.is_file():
        return path
    return None
