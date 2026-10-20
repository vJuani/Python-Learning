"""Closed dialogs must not keep a painted layer after close()."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS_DIR = ROOT / "static" / "css"

# Classes that live on the <dialog> element itself.
DIALOG_ROOT_CLASSES = {
    "aa-dialog",
    "aa-payment-modal",
    "aa-charge-modal",
    "op-credit-modal",
    "visit-close",
    "mkt-zoom",
    "mkt-picker",
    "pd-modal",
    "public-lightbox",
    "acm-v5-modal",
    "property-brochure-dialog",
    "mkt-create-dialog",
}

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_DISPLAY = re.compile(r"(?:^|;)\s*display\s*:\s*([^;!}]+)", re.IGNORECASE)


def _subject(selector: str) -> str:
    parts = re.split(r"\s*[>+~]\s*|\s+", selector.strip())
    return parts[-1] if parts else ""


def _is_dialog_subject(subject: str) -> bool:
    base = subject.split("::")[0]
    if re.match(r"dialog(?![\w-])", base):
        return True
    classes = set(re.findall(r"\.([A-Za-z_][\w-]*)", base.split(":")[0]))
    return bool(classes & DIALOG_ROOT_CLASSES)


def _rules(css: str):
    text = _COMMENT.sub("", css)
    for chunk in text.split("}"):
        if "{" not in chunk:
            continue
        selector, body = chunk.split("{", 1)
        yield selector.strip(), body


def unsafe_dialog_display_rules() -> list[str]:
    found = []
    for path in sorted(CSS_DIR.glob("*.css")):
        css = path.read_text(encoding="utf-8")
        for selector, body in _rules(css):
            match = _DISPLAY.search(body)
            if not match:
                continue
            value = match.group(1).strip().lower()
            if value == "none":
                continue
            for part in selector.split(","):
                part = part.strip()
                if not part or "[open]" in part:
                    continue
                if _is_dialog_subject(_subject(part)):
                    found.append(f"{path.name}: {part} {{ display: {value} }}")
    return found


class DialogCloseCssTests(unittest.TestCase):
    def test_closed_dialogs_are_forced_hidden(self):
        style = (CSS_DIR / "style.css").read_text(encoding="utf-8")
        self.assertIn("dialog:not([open])", style)
        self.assertIn("display: none !important", style)

    def test_open_dialog_display_is_gated(self):
        unsafe = unsafe_dialog_display_rules()
        self.assertEqual(unsafe, [])
