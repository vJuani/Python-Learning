"""JRH_MARKETING_VISUAL_SPEC — approved Creative AI direction of art.

The approved story is a STYLE REFERENCE, not a pixel template.
Property, agent and facts are always live data.
"""

from __future__ import annotations

from pathlib import Path

SPEC_ID = "JRH_MARKETING_VISUAL_SPEC"
SPEC_VERSION = "1.0"
QUALITY_TARGET = "approved_jrh_story"

STYLE_DIR = Path(__file__).resolve().parent.parent / "marketing_style_references"
APPROVED_STYLE_NAME = "jrh-approved-story.jpg"

PALETTE = {
    "navy": (10, 22, 51),
    "navy_deep": (6, 14, 32),
    "electric": (13, 71, 255),
    "white": (255, 255, 255),
    "muted": (176, 190, 214),
    "ink": (17, 28, 51),
}

HIERARCHY = (
    "property_hero",
    "headline_or_address",
    "secondary_photos",
    "price",
    "key_facts",
    "agent",
    "cta",
    "branding",
)

COPY_DENSITY = "very_low"
MAX_CREATIVE_WORDS = 8
MAX_SECONDARY_PHOTOS = 3
HERO_ATTENTION = (0.40, 0.60)
AGENT_ATTENTION = (0.20, 0.35)

SAFE_INSET = {
    "story": {"x": 56, "y": 72},
    "status": {"x": 56, "y": 96},
    "post": {"x": 48, "y": 56},
    "flyer": {"x": 52, "y": 60},
}

COMPOSITIONS = {
    "editorial_navy": {
        "theme": "dark",
        "hero": "upper_editorial",
        "thumbs": "pair_left",
        "agent": "large_lateral",
        "mood": "premium, aspirational, sophisticated",
        "brief": (
            "Editorial navy Story. Large real hero photo, two complementary "
            "secondary photos, large real agent cutout entering from the lower right. "
            "Very low copy. Price and address prominent. Navy + electric blue + white."
        ),
    },
    "white_architectural": {
        "theme": "light",
        "hero": "collage_lead",
        "thumbs": "asymmetric",
        "agent": "small_footer",
        "mood": "bright, architectural, clean",
        "brief": (
            "White architectural Story. Multi-photo collage with intentional air, "
            "blue accents, smaller real agent at the footer. Same JRH quality, "
            "different composition."
        ),
    },
    "photo_led_luxury": {
        "theme": "dark",
        "hero": "full_bleed",
        "thumbs": "none_or_one",
        "agent": "bottom_integrated",
        "mood": "photo-led luxury",
        "brief": (
            "Photo-led luxury Story. Full-bleed real hero, optional one secondary, "
            "real agent integrated along the bottom. Strong type, almost no copy."
        ),
    },
}

STYLE_REFERENCE_LABEL = (
    "This image is a VISUAL STYLE REFERENCE ONLY. "
    "Copy its level of polish, visual hierarchy, photographic prominence, "
    "agent integration and premium editorial character. "
    "Do not copy its property, person, text or exact layout."
)

AVOID = (
    "SaaS UI",
    "generic template",
    "tiny text",
    "empty canvas",
    "duplicate logos",
    "invented property imagery",
    "invented agent face",
    "giant HTML buttons",
    "dashboard cards",
    "microscopic labels",
    "giant unused navy shapes",
    "mechanical thumbnail grids",
    "agent as a floating sticker",
    "text clipped outside the safe area",
)


def approved_style_path():
    path = STYLE_DIR / APPROVED_STYLE_NAME
    return path if path.is_file() else None


def safe_inset(fmt):
    return dict(SAFE_INSET.get(fmt) or SAFE_INSET["story"])


def composition_spec(direction):
    if direction in COMPOSITIONS:
        return COMPOSITIONS[direction]
    aliases = {
        "property_hero": "photo_led_luxury",
        "luxury_minimal": "photo_led_luxury",
        "luxury_editorial": "editorial_navy",
        "clean_collage": "white_architectural",
        "bright_architectural": "white_architectural",
        "price_led": "white_architectural",
    }
    return COMPOSITIONS[aliases.get(direction) or "editorial_navy"]


def build_visual_brief(fmt, direction, *, show_agent_photo=True):
    spec = composition_spec(direction)
    agent = (
        "large real cutout, lower-right, 20-35% visual weight"
        if show_agent_photo and spec["agent"] == "large_lateral"
        else (
            "small real cutout at footer"
            if show_agent_photo and spec["agent"] == "small_footer"
            else (
                "real cutout integrated along the bottom"
                if show_agent_photo
                else "no agent portrait"
            )
        )
    )
    return {
        "format": f"{fmt} vertical",
        "quality_target": QUALITY_TARGET,
        "composition": spec["brief"],
        "agent": agent,
        "text_density": COPY_DENSITY,
        "price": "prominent",
        "branding": "navy + electric blue + white",
        "mood": spec["mood"],
        "avoid": list(AVOID),
        "theme": spec["theme"],
        "hero": spec["hero"],
        "thumbs": spec["thumbs"],
        "agent_slot": spec["agent"],
    }
