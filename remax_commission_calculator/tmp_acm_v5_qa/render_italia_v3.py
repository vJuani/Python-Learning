"""Render Italia 1341 V3 using reference photo crops for visual QA."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from modules.marketing_flyer_commercial_v3 import render_modern_commercial_v3
from modules.marketing_renderer import FORMAT_SIZES

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp_acm_v5_qa"
REF = ROOT / "static" / "branding" / "references" / "marketing_modern_reference.png"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ref = Image.open(REF).convert("RGB")
    # Hero from the property photo band of the reference
    hero = ref.crop((0, 70, min(1080, ref.width), min(700, ref.height))).resize((1600, 1000))
    # Synthetic varied secondaries (avoid reusing nested UI crops from the reference flyer)
    living = hero.crop((200, 200, 900, 700)).resize((1200, 800))
    cocina = hero.crop((400, 100, 1100, 600)).resize((1200, 800))
    jardin = hero.crop((100, 400, 1000, 900)).resize((1200, 800))
    photos = [hero, living, cocina, jardin]

    agent_img = Image.new("RGBA", (520, 780), (0, 0, 0, 0))
    ad = ImageDraw.Draw(agent_img)
    ad.ellipse((110, 20, 410, 320), fill=(52, 58, 72, 255))
    ad.rectangle((90, 280, 430, 760), fill=(52, 58, 72, 255))
    agent_path = OUT / "_agent_cutout_qa.png"
    agent_img.save(agent_path)

    facts = {
        "title": "Italia 1341",
        "neighborhood": "Martínez",
        "locality": "Martínez",
        "jurisdiction": "San Isidro",
        "purpose": "sale",
        "property_type": "house",
        "type_label": "Casa",
        "rooms": 4,
        "bedrooms": 3,
        "bathrooms": 2,
        "total_m2": 131,
        "chips": ["4 ambientes", "3 dormitorios", "2 baños", "131 m²"],
        "price_label": "USD 320.000",
        "benefit_line": "Espacios amplios y diseño para disfrutar todos los días.",
        "brand_name": "RE/MAX Data House",
        "wordmark_text": "RE/MAX Data House",
        "legal_footer_line": "Corredor Público Mauro Marvisi C.U.C.I.C.B.A 1762",
        "broker_footer_text": "Corredor Público Mauro Marvisi C.U.C.I.C.B.A 1762",
        "amenities": ["jardín"],
    }
    copy = {
        "headline": "Casa moderna en Martínez",
        "subheadline": "Espacios amplios y diseño para disfrutar todos los días.",
        "short_benefit": "Espacios amplios y diseño para disfrutar todos los días.",
        "cta": "Consultame para visitarla",
    }
    agent = {
        "name": "Jose Barreiro",
        "title": "Agente inmobiliario",
        "whatsapp": "+54 9 11 2513-1361",
        "instagram": "@josebarreiro_remax",
        "email": "jose@remaxdatahouse.com.ar",
        "photo_path": str(agent_path),
    }
    photo_rows = [
        {"id": 1, "label": "fachada", "is_cover": True},
        {"id": 2, "label": "living"},
        {"id": 3, "label": "cocina"},
        {"id": 4, "label": "jardin"},
    ]
    canvas = render_modern_commercial_v3(
        FORMAT_SIZES["post"],
        photos,
        facts,
        copy,
        agent,
        {
            "include_agent": True,
            "show_agent_photo": True,
            "show_price": True,
            "show_features": True,
        },
        language="es",
        photo_rows=photo_rows,
    )
    out = OUT / "italia_1341_modern_commercial_v3.png"
    canvas.save(out, format="PNG", optimize=True)
    print("wrote", out, canvas.size)

    side = Image.new("RGB", (1080 * 2 + 40, 1350), (24, 24, 24))
    side.paste(ref.resize((1080, 1350), Image.Resampling.LANCZOS), (0, 0))
    side.paste(canvas, (1120, 0))
    compare = OUT / "italia_1341_v3_vs_reference.png"
    side.save(compare)
    print("compare", compare)


if __name__ == "__main__":
    main()
