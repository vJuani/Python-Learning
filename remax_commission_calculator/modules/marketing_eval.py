"""Internal quality evaluation for Marketing IA copy. Isolated from production."""

from __future__ import annotations

from modules.database.marketing_eval_repository import (
    SCORES,
    create_eval_run,
    get_eval_run,
    list_eval_runs,
    score_eval_run,
)
from modules.database.properties_repository import get_properties, get_property_record
from modules.marketing_context import (
    MarketingError,
    assert_marketing_access,
    build_property_marketing_context,
)
from modules.marketing_creative import (
    FORMATS,
    ORIGINS,
    STYLES,
    TONES,
    VARIANT_PRESETS,
    generate,
    generate_variants,
    normalize_format,
    normalize_origin,
    normalize_style,
    normalize_tone,
)
from modules.database.tenant import require_organization_id

QUALITY_ISSUES = (
    ("generic", "genérico"),
    ("repetitive", "repetitivo"),
    ("too_long", "demasiado largo"),
    ("too_short", "demasiado corto"),
    ("sounds_ai", "suena a IA"),
    ("weak_cta", "CTA flojo"),
    ("weak_attributes", "no usa buenos atributos"),
    ("too_salesy", "demasiado vendedor"),
    ("not_salesy", "poco vendedor"),
    ("invents_facts", "inventa datos"),
    ("wrong_tone", "tono incorrecto"),
)

SCORE_LABELS = {
    "excellent": "Excelente",
    "good": "Buena",
    "fair": "Regular",
    "poor": "Mala",
}

EVAL_CASES = (
    {
        "slug": "sale-apartment",
        "label": "Departamento venta",
        "purpose": "sale",
        "property_type": "apartment",
        "origin": "listing",
    },
    {
        "slug": "rent-apartment",
        "label": "Departamento alquiler",
        "purpose": "rental",
        "property_type": "apartment",
        "origin": "listing",
    },
    {
        "slug": "house",
        "label": "Casa",
        "property_type": "house",
        "origin": "listing",
    },
    {
        "slug": "amenities",
        "label": "Propiedad con amenities",
        "needs_amenities": True,
        "origin": "listing",
    },
    {
        "slug": "sparse",
        "label": "Propiedad con pocos datos",
        "sparse": True,
        "origin": "listing",
    },
    {
        "slug": "free",
        "label": "Origen libre",
        "origin": "free",
    },
    {
        "slug": "personal-brand",
        "label": "Marca personal",
        "origin": "personal_brand",
    },
)


def _property_label(property_data):
    if not property_data:
        return "Sin propiedad"
    return (
        property_data.get("address")
        or property_data.get("formatted_address")
        or property_data.get("title")
        or f"Propiedad #{property_data.get('id')}"
    )


def _amenity_score(property_data):
    features = property_data.get("features") or property_data.get("features_json") or []
    if isinstance(features, str):
        return 1 if features.strip() else 0
    if isinstance(features, dict):
        return len([value for value in features.values() if value])
    if isinstance(features, list):
        return len(features)
    chips = property_data.get("chips") or []
    return len(chips)


def _is_sparse(property_data):
    filled = 0
    for key in ("description", "rooms", "bedrooms", "bathrooms", "covered_m2", "listing_price"):
        if property_data.get(key) not in (None, ""):
            filled += 1
    return filled <= 2


def suggest_properties_for_case(properties, case):
    if not case or case.get("origin") in {"free", "personal_brand"}:
        return []
    matches = []
    for item in properties:
        purpose = (item.get("listing_purpose") or "").lower()
        ptype = (item.get("property_type") or "").lower()
        if case.get("purpose") and purpose != case["purpose"]:
            if not (case["purpose"] == "rental" and purpose in {"rental", "temporary_rental"}):
                continue
        if case.get("property_type") and ptype != case["property_type"]:
            continue
        if case.get("needs_amenities") and _amenity_score(item) < 2:
            continue
        if case.get("sparse") and not _is_sparse(item):
            continue
        matches.append(item)
    return matches[:8]


def _persist_generation(
    organization_id,
    user,
    generation,
    *,
    property_id=None,
    property_label=None,
    case_slug=None,
):
    return create_eval_run(
        organization_id,
        created_by_user_id=(user or {}).get("id"),
        property_id=property_id,
        property_label=property_label,
        case_slug=case_slug,
        origin=generation.get("origin"),
        fmt=generation.get("format"),
        style=generation.get("style"),
        tone=generation.get("tone"),
        notes=generation.get("notes"),
        variant_label=generation.get("variant_label"),
        group_id=generation.get("group_id"),
        prompt_version=generation.get("prompt_version"),
        model=generation.get("model"),
        provider=generation.get("provider"),
        input_summary=generation.get("input_summary"),
        creative_brief=generation.get("creative_brief"),
        output=generation.get("output"),
        rendered_text=generation.get("rendered_text"),
        tokens_input=generation.get("tokens_input"),
        tokens_output=generation.get("tokens_output"),
        duration_ms=generation.get("duration_ms"),
        error=generation.get("error"),
    )


def _load_context(organization_id, user, property_id, origin, notes):
    property_data = None
    if property_id:
        property_data = get_property_record(property_id, organization_id)
        if property_data is None:
            raise MarketingError("marketing_err_property_missing", 404)
        assert_marketing_access(user, property_data)
        return (
            build_property_marketing_context(property_data, include_agent=True),
            property_data,
        )
    if origin in {"free", "personal_brand"}:
        if not notes:
            raise MarketingError("marketing_eval_notes_required", 400)
        return (
            {
                "facts": {
                    "title": notes[:80],
                    "description": notes,
                    "chips": [],
                    "origin": origin,
                },
                "photos": [],
                "language": "es",
            },
            None,
        )
    raise MarketingError("marketing_err_property_missing", 400)


def run_eval_generation(
    organization_id,
    user,
    *,
    property_id=None,
    fmt="post",
    style="modern",
    tone="close",
    notes="",
    origin="listing",
    case_slug=None,
):
    organization_id = require_organization_id(organization_id)
    origin = normalize_origin(origin)
    context, property_data = _load_context(organization_id, user, property_id, origin, notes)
    generation = generate(
        context,
        fmt=normalize_format(fmt),
        style=normalize_style(style),
        tone=normalize_tone(tone),
        notes=notes,
        origin=origin,
    )
    return _persist_generation(
        organization_id,
        user,
        generation,
        property_id=(property_data or {}).get("id"),
        property_label=_property_label(property_data) if property_data else (notes[:80] or origin),
        case_slug=case_slug,
    )


def run_eval_variants(
    organization_id,
    user,
    *,
    property_id=None,
    fmt="post",
    notes="",
    origin="listing",
    case_slug=None,
):
    organization_id = require_organization_id(organization_id)
    origin = normalize_origin(origin)
    context, property_data = _load_context(organization_id, user, property_id, origin, notes)
    bundle = generate_variants(
        context,
        fmt=normalize_format(fmt),
        notes=notes,
        origin=origin,
    )
    runs = [
        _persist_generation(
            organization_id,
            user,
            item,
            property_id=(property_data or {}).get("id"),
            property_label=_property_label(property_data) if property_data else (notes[:80] or origin),
            case_slug=case_slug,
        )
        for item in bundle["items"]
    ]
    return {"group_id": bundle["group_id"], "runs": runs}


def prepare_eval_view(
    organization_id,
    user,
    *,
    run_id=None,
    group_id=None,
    compare_a=None,
    compare_b=None,
    case_slug=None,
):
    organization_id = require_organization_id(organization_id)
    properties = get_properties(organization_id)
    cases = []
    for case in EVAL_CASES:
        item = dict(case)
        item["matches"] = suggest_properties_for_case(properties, case)
        cases.append(item)
    selected = get_eval_run(run_id, organization_id) if run_id else None
    variants = list_eval_runs(organization_id, group_id=group_id, limit=6) if group_id else []
    if selected and selected.get("group_id") and not variants:
        variants = list_eval_runs(organization_id, group_id=selected["group_id"], limit=6)
    compare = None
    if compare_a and compare_b:
        left = get_eval_run(int(compare_a), organization_id)
        right = get_eval_run(int(compare_b), organization_id)
        if left and right:
            compare = {"a": left, "b": right}
    return {
        "formats": FORMATS,
        "styles": STYLES,
        "tones": TONES,
        "origins": ORIGINS,
        "variant_presets": VARIANT_PRESETS,
        "issues": QUALITY_ISSUES,
        "scores": [(key, SCORE_LABELS[key]) for key in SCORES],
        "cases": cases,
        "properties": properties,
        "runs": list_eval_runs(organization_id, limit=30),
        "selected": selected,
        "variants": variants,
        "compare": compare,
        "case_slug": case_slug or "",
        "prompt_version": selected.get("prompt_version") if selected else None,
    }
