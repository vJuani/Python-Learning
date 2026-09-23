"""Text fit of the official property templates, measured on real DOM boxes in Chromium."""

from __future__ import annotations

import unittest

from tests.test_property_marketing import _fixture_context

from modules.marketing_render_html import _get_browser, _lock
from modules.property_marketing import (
    CTA_BUTTON_MAX_CHARS,
    TEMPLATE_CLEAN_GRID,
    TEMPLATE_LIFESTYLE_DARK,
    TEMPLATE_PREMIUM_HERO,
    build_property_marketing_view,
    normalize_button_cta,
    render_property_marketing_html,
)

ADDRESSES = (
    "Italia 1341",
    "Santamarina 1335",
    "Avenida del Libertador 15200",
    "Presidente Manuel Quintana 2847",
)
AGENTS = ("José Barreiro", "María de los Ángeles Fernández")
EMAILS = ("juanignacioruiz2001@gmail.com", "agente.inmobiliario@empresa-ejemplo.com.ar")
CTAS = ("COORDINÁ TU VISITA", "COORDINEMOS UNA VISITA.")
LONG_COPY = (
    "Diseño, confort y una ubicación privilegiada en Victoria, con luz natural todo el día "
    "y espacios amplios para disfrutar en familia."
)
BROKER_LINE = "Martillero responsable: Mauro Marvisi | CUCICBA 1762 / CMCPSI 5574"

# Every combination of address, agent, email and CTA is covered at least once per template.
CASES = [
    (ADDRESSES[i % 4], AGENTS[i % 2], EMAILS[(i // 2) % 2], CTAS[(i + i // 2) % 2])
    for i in range(4)
] + [("Presidente Manuel Quintana 2847", AGENTS[1], EMAILS[1], "COORDINEMOS UNA VISITA.")]

TEXT_SELECTORS = {
    "address": ".address",
    "copy": ".copy",
    "zone": ".zone",
    "price": ".price",
    "cta": ".cta",
    "cta_label": ".cta em",
    "agent_name": ".agent__meta h2",
    "agent_title": ".agent__meta p",
    "contacts": ".agent-contact",
    "legal": ".legal",
    "brand": ".brand",
    "aside": ".aside",
    "quote": ".quote",
}
BOX_SELECTORS = {
    "facts": ".fact",
    "thumbs": ".thumb",
    "hero": ".hero",
    "agent_photo": ".agent__photo",
    "cta_button": ".cta",
}

MEASURE_JS = """
async ({text, boxes}) => {
  await document.fonts.ready;
  window.__propertyTextFit();
  const poster = document.getElementById('poster');
  const origin = poster.getBoundingClientRect();
  const rel = (r) => ({left: r.left - origin.left, top: r.top - origin.top,
                       right: r.right - origin.left, bottom: r.bottom - origin.top});
  const union = (rects) => {
    const list = rects.filter((r) => r.width > 0.5 && r.height > 0.5);
    if (!list.length) return null;
    return rel({left: Math.min(...list.map((r) => r.left)), top: Math.min(...list.map((r) => r.top)),
                right: Math.max(...list.map((r) => r.right)), bottom: Math.max(...list.map((r) => r.bottom))});
  };
  const lines = (el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const style = getComputedStyle(el);
    const lh = style.lineHeight === 'normal' ? parseFloat(style.fontSize) * 1.2 : parseFloat(style.lineHeight);
    const centers = [];
    Array.from(range.getClientRects()).filter((r) => r.width > 0.5).forEach((r) => {
      const mid = (r.top + r.bottom) / 2;
      if (!centers.some((c) => Math.abs(c - mid) < lh * 0.5)) centers.push(mid);
    });
    return centers.length;
  };
  const out = {textFit: poster.dataset.textFit, width: origin.width, height: origin.height,
               text: {}, boxes: {}, ellipsis: [], clipped: []};
  for (const [name, selector] of Object.entries(text)) {
    const el = poster.querySelector(selector);
    if (!el || el.getBoundingClientRect().height === 0) continue;
    const range = document.createRange();
    range.selectNodeContents(el);
    out.text[name] = {box: union(Array.from(range.getClientRects())), element: rel(el.getBoundingClientRect()),
                      lines: lines(el), content: el.textContent.trim(),
                      fit: el.dataset.fitResult || null, fontSize: parseFloat(getComputedStyle(el).fontSize)};
  }
  for (const [name, selector] of Object.entries(boxes)) {
    const els = Array.from(poster.querySelectorAll(selector));
    if (els.length) out.boxes[name] = union(els.map((el) => el.getBoundingClientRect()));
  }
  poster.querySelectorAll('*').forEach((el) => {
    const style = getComputedStyle(el);
    if (style.textOverflow === 'ellipsis') out.ellipsis.push(el.className || el.tagName);
    const clips = style.overflow === 'hidden' || style.overflowX === 'hidden';
    if (clips && el.scrollWidth > el.clientWidth + 1 && el.querySelector('img') === null) {
      out.clipped.push(el.className || el.tagName);
    }
  });
  out.stacks = Array.from(poster.querySelectorAll('[data-stack]')).map((el) => el.dataset.stackResult);
  return out;
}
"""


def _context(address, agent_name, email, cta):
    packed = _fixture_context(email=email, instagram="@joselbarreiro_remax")
    packed["facts"]["title"] = address
    packed["facts"]["neighborhood"] = "Victoria"
    packed["facts"]["locality"] = "San Fernando"
    packed["facts"]["price_label"] = "USD 1.250.000"
    packed["facts"]["legal_footer_line"] = BROKER_LINE
    packed["agent"]["name"] = agent_name
    packed["art"] = {"cta": cta, "subheadline": LONG_COPY}
    return packed


def _measure(packed, template):
    options = dict(packed["options"])
    options["layout_template"] = template
    view = build_property_marketing_view(packed, options=options, art=packed["art"], language="es")
    html = render_property_marketing_html(view)
    width, height = view["canvas_size"]
    browser = _get_browser()
    with _lock:
        context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1)
        try:
            page = context.new_page()
            page.route("**/*", lambda route: route.abort() if route.request.url.startswith(("http://", "https://")) else route.continue_())
            page.set_content(html, wait_until="load")
            data = page.evaluate(MEASURE_JS, {"text": TEXT_SELECTORS, "boxes": BOX_SELECTORS})
        finally:
            context.close()
    data["view"] = view
    return data


def _overlap(a, b, tolerance=1.0):
    if not a or not b:
        return False
    return (
        min(a["right"], b["right"]) - max(a["left"], b["left"]) > tolerance
        and min(a["bottom"], b["bottom"]) - max(a["top"], b["top"]) > tolerance
    )


NON_OVERLAP = {
    TEMPLATE_PREMIUM_HERO: [
        ("address", "facts"), ("copy", "facts"), ("address", "zone"), ("address", "brand"),
        ("copy", "price"), ("facts", "price"), ("cta", "agent_name"), ("cta", "contacts"),
        ("agent_name", "contacts"), ("contacts", "legal"), ("cta", "aside"), ("legal", "aside"),
        ("contacts", "aside"),
    ],
    TEMPLATE_CLEAN_GRID: [
        ("address", "thumbs"), ("copy", "thumbs"), ("zone", "thumbs"), ("copy", "facts"),
        ("price", "facts"), ("agent_name", "contacts"), ("agent_title", "contacts"),
        ("contacts", "cta"), ("cta", "legal"), ("contacts", "legal"), ("agent_name", "cta"),
    ],
    TEMPLATE_LIFESTYLE_DARK: [
        ("address", "copy"), ("copy", "facts"), ("facts", "price"), ("price", "cta"),
        ("cta", "agent_photo"), ("cta", "agent_name"), ("cta", "quote"), ("price", "quote"),
        ("agent_name", "contacts"), ("contacts", "legal"), ("agent_name", "legal"), ("zone", "address"),
    ],
}
COPY_MAX_LINES = {TEMPLATE_PREMIUM_HERO: 3, TEMPLATE_CLEAN_GRID: 3, TEMPLATE_LIFESTYLE_DARK: 3}


class ButtonCtaNormalizationTests(unittest.TestCase):
    def test_short_cta_is_kept(self):
        self.assertEqual(normalize_button_cta("Coordiná tu visita"), "Coordiná tu visita")
        self.assertEqual(normalize_button_cta("Escribime ahora"), "Escribime ahora")

    def test_long_cta_falls_back(self):
        self.assertEqual(len("COORDINEMOS UNA VISITA."), CTA_BUTTON_MAX_CHARS + 1)
        self.assertEqual(normalize_button_cta("COORDINEMOS UNA VISITA."), "Coordiná tu visita")
        long_cta = "Escribime y coordinamos una visita esta semana"
        self.assertGreater(len(long_cta), CTA_BUTTON_MAX_CHARS)
        self.assertEqual(normalize_button_cta(long_cta), "Coordiná tu visita")
        self.assertEqual(normalize_button_cta(long_cta, "en"), "Book a visit")
        self.assertEqual(normalize_button_cta(""), "Coordiná tu visita")


class TemplateTextFitTests(unittest.TestCase):
    maxDiff = None

    def _check(self, template, case):
        address, agent_name, email, cta = case
        data = _measure(_context(address, agent_name, email, cta), template)
        label = f"{template} {case}"
        width, height = data["width"], data["height"]
        self.assertEqual(data["textFit"], "done", label)
        self.assertEqual(data["ellipsis"], [], label)
        self.assertEqual(data["clipped"], [], label)
        self.assertNotIn("overflow", data["stacks"], label)
        self.assertIn("address", data["text"], label)
        self.assertEqual(data["text"]["address"]["content"].upper(), address.upper(), label)
        self.assertIn(email, data["text"]["contacts"]["content"], label)
        self.assertNotIn("wbr", data["text"]["contacts"]["content"], label)

        self.assertEqual(data["text"]["cta_label"]["content"].upper(), normalize_button_cta(cta).upper(), label)

        for name, info in data["text"].items():
            # the handwritten quote is rotated on purpose: only its glyphs must stay inside
            for key in ("box",) if name == "quote" else ("box", "element"):
                box = info[key]
                if not box:
                    continue
                with self.subTest(template=template, case=case, element=name, box=key):
                    self.assertGreaterEqual(box["left"], -0.5, name)
                    self.assertGreaterEqual(box["top"], -0.5, name)
                    self.assertLessEqual(box["right"], width + 0.5, name)
                    self.assertLessEqual(box["bottom"], height + 0.5, name)

        boxes = {name: info["box"] for name, info in data["text"].items()}
        boxes.update({name: box for name, box in data["boxes"].items() if name not in boxes})
        for first, second in NON_OVERLAP[template]:
            with self.subTest(template=template, case=case, pair=(first, second)):
                self.assertFalse(_overlap(boxes.get(first), boxes.get(second)), (first, second, boxes.get(first), boxes.get(second)))

        self.assertLessEqual(data["text"]["address"]["lines"], 2, label)
        self.assertLessEqual(data["text"]["copy"]["lines"], COPY_MAX_LINES[template], label)
        self.assertLessEqual(data["text"]["cta_label"]["lines"], 2, label)
        self.assertLessEqual(data["text"]["agent_name"]["lines"], 2, label)

        button = data["boxes"]["cta_button"]
        label_box = data["text"]["cta_label"]["box"]
        self.assertGreaterEqual(label_box["left"], button["left"] - 0.5, label)
        self.assertLessEqual(label_box["right"], button["right"] + 0.5, label)
        self.assertGreaterEqual(label_box["top"], button["top"] - 0.5, label)
        self.assertLessEqual(label_box["bottom"], button["bottom"] + 0.5, label)
        return data

    def test_premium_hero(self):
        for case in CASES:
            data = self._check(TEMPLATE_PREMIUM_HERO, case)
            address = data["text"]["address"]["box"]
            center = (address["left"] + address["right"]) / 2
            self.assertAlmostEqual(center, data["width"] / 2, delta=data["width"] * 0.02, msg=case)

    def test_clean_grid(self):
        for case in CASES:
            data = self._check(TEMPLATE_CLEAN_GRID, case)
            card = data["boxes"]["hero"]
            for name in ("address", "zone", "copy"):
                box = data["text"][name]["box"]
                self.assertLessEqual(box["right"], card["right"] + 0.5, (name, case))
                self.assertLessEqual(box["bottom"], card["bottom"] + 0.5, (name, case))
            thumbs = data["boxes"].get("thumbs")
            if thumbs:
                self.assertLessEqual(data["text"]["address"]["box"]["right"], thumbs["left"], case)

    def test_lifestyle_dark(self):
        for case in CASES:
            data = self._check(TEMPLATE_LIFESTYLE_DARK, case)
            order = [data["text"][name]["box"] for name in ("zone", "address", "copy")]
            order += [data["boxes"]["facts"], data["text"]["price"]["box"], data["boxes"]["cta_button"]]
            for upper, lower in zip(order, order[1:]):
                self.assertLessEqual(upper["bottom"], lower["top"] + 1, case)

    def test_santamarina_single_line_when_possible(self):
        data = _measure(_context("Santamarina 1335", AGENTS[0], EMAILS[0], CTAS[1]), TEMPLATE_PREMIUM_HERO)
        self.assertEqual(data["text"]["address"]["lines"], 1)
        self.assertEqual(data["text"]["address"]["content"], "Santamarina 1335")


if __name__ == "__main__":
    unittest.main()
