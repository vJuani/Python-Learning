"""Image generation abstraction. Backgrounds only — never the listing."""

from __future__ import annotations

import io
import logging
import os
import urllib.error
import urllib.request

from PIL import Image, ImageDraw

from modules.marketing_renderer import ELECTRIC, NAVY, WHITE

logger = logging.getLogger(__name__)


def get_marketing_image_model():
    return (
        os.environ.get("MARKETING_IMAGE_MODEL")
        or os.environ.get("OPENAI_IMAGE_MODEL")
        or "gpt-image-2"
    ).strip()


def get_marketing_image_provider_name():
    explicit = (os.environ.get("MARKETING_IMAGE_PROVIDER") or "").strip().lower()
    if explicit:
        return explicit
    if os.environ.get("JRH_AI_PROVIDER", "").strip().lower() in {"mock", "test", "rules"}:
        return "mock"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai"
    return "unavailable"


def is_explicit_mock_provider():
    name = get_marketing_image_provider_name()
    return name == "mock"


def _size_for_api(width, height):
    def _snap(value):
        return max(16, int(round(value / 16) * 16))

    return f"{_snap(width)}x{_snap(height)}"


def _paint_direction(size, direction):
    width, height = size
    image = Image.new("RGB", size, WHITE)
    draw = ImageDraw.Draw(image)
    if direction in {"editorial_dark", "photo_lifestyle"} and "dark" in (direction or ""):
        image.paste(NAVY, (0, 0, width, height))
        draw.ellipse((-width * 0.2, height * 0.55, width * 1.1, height * 1.3), fill=ELECTRIC)
        return image
    if direction == "editorial_dark":
        image.paste(NAVY, (0, 0, width, height))
        draw.rectangle((0, int(height * 0.62), width, height), fill=(8, 16, 40))
        draw.rectangle((0, int(height * 0.62), _snap_line(width), int(height * 0.62) + 6), fill=ELECTRIC)
        return image
    if direction == "bold_grid":
        draw.rectangle((0, 0, width, int(height * 0.1)), fill=NAVY)
        draw.polygon([(width, 0), (width, int(height * 0.28)), (int(width * 0.55), 0)], fill=ELECTRIC)
        return image
    if direction == "modern_sales":
        draw.rectangle((0, 0, int(width * 0.38), height), fill=NAVY)
        draw.ellipse((int(width * 0.7), int(height * 0.7), width + 80, height + 80), fill=ELECTRIC)
        return image
    draw.rectangle((0, 0, width, int(height * 0.09)), fill=NAVY)
    draw.pieslice((-80, -80, int(width * 0.55), int(height * 0.22)), 0, 180, fill=ELECTRIC)
    draw.rectangle((0, int(height * 0.92), width, height), fill=NAVY)
    return image


def _snap_line(width):
    return max(40, int(width * 0.28))


class MarketingImageError(Exception):
    pass


class MarketingImageProvider:
    def generate_background(self, *, prompt, size, visual_direction):
        raise NotImplementedError


class MockMarketingImageProvider(MarketingImageProvider):
    def generate_background(self, *, prompt, size, visual_direction):
        image = _paint_direction(size, visual_direction or "")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


class OpenAIImageProvider(MarketingImageProvider):
    def generate_background(self, *, prompt, size, visual_direction):
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise MarketingImageError("missing_openai_api_key")
        width, height = size
        payload = {
            "model": get_marketing_image_model(),
            "prompt": prompt,
            "n": 1,
            "size": _size_for_api(width, height),
            "quality": os.environ.get("MARKETING_IMAGE_QUALITY", "medium"),
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/images/generations",
            data=__import__("json").dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last_error = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    body = __import__("json").loads(response.read().decode("utf-8"))
                last_error = None
                break
            except urllib.error.HTTPError as error:
                last_error = MarketingImageError(f"openai_http_{error.code}")
                logger.info("marketing_image openai_http_%s attempt_%s", error.code, attempt)
                if error.code in {429, 500, 502, 503} and attempt == 0:
                    __import__("time").sleep(2)
                    continue
                raise last_error from error
            except Exception as error:
                raise MarketingImageError("openai_request_failed") from error
        if last_error:
            raise last_error
        b64 = ((body.get("data") or [{}])[0] or {}).get("b64_json")
        if not b64:
            raise MarketingImageError("openai_empty_image")
        raw = __import__("base64").b64decode(b64)
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        if image.size != size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


def get_marketing_image_provider():
    name = get_marketing_image_provider_name()
    if name == "openai":
        return OpenAIImageProvider()
    if name == "mock":
        return MockMarketingImageProvider()
    raise MarketingImageError("image_provider_unavailable")


def background_prompt(art, fmt):
    style = (art or {}).get("background_style") or "premium navy and electric blue"
    direction = (art or {}).get("visual_direction") or "premium"
    return (
        f"Premium real-estate GRAPHIC BACKGROUND only for a {fmt} creative. "
        f"Direction: {direction}. Style: {style}. "
        "Navy #0A1633, electric blue #0D47FF, white. Abstract shapes, curves, "
        "editorial composition. NO photographs, NO interiors, NO buildings, "
        "NO people, NO faces, NO logos, NO text, NO numbers, NO watermarks, "
        "NO floor plans. Leave open areas for real photos and type."
    )
