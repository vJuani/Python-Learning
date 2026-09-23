"""QA only: renders the hardest text-fit case for the 3 templates into tmp/property_marketing_qa/text_fit/."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.test_property_marketing_text_fit import _context  # noqa: E402
from modules.property_marketing import (  # noqa: E402
    TEMPLATE_CLEAN_GRID,
    TEMPLATE_LIFESTYLE_DARK,
    TEMPLATE_PREMIUM_HERO,
    render_property_marketing,
)

OUT = Path(__file__).resolve().parent / "text_fit"
OUT.mkdir(exist_ok=True)
packed = _context(
    "Presidente Manuel Quintana 2847",
    "María de los Ángeles Fernández",
    "agente.inmobiliario@empresa-ejemplo.com.ar",
    "COORDINEMOS UNA VISITA.",
)
for template in (TEMPLATE_PREMIUM_HERO, TEMPLATE_CLEAN_GRID, TEMPLATE_LIFESTYLE_DARK):
    options = dict(packed["options"], layout_template=template)
    result = render_property_marketing(packed, "post", options=options, art=packed["art"], language="es")
    (OUT / f"stress_{template}.png").write_bytes(result["png_bytes"])
    print(template, "ok")
