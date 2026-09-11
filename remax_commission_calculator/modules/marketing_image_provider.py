"""Image generation abstraction. Finished ads with real visual references."""

from __future__ import annotations

import io
import json
import logging
import os
import time
import urllib.error
import urllib.request

from PIL import Image, ImageDraw

from modules.marketing_renderer import NAVY, WHITE, fit_cover, paste_rounded

logger = logging.getLogger(__name__)


def get_marketing_image_model():
    return (
        os.environ.get("MARKETING_IMAGE_MODEL")
        or os.environ.get("OPENAI_IMAGE_MODEL")
        or "gpt-image-2.5-sunburst"
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
    return get_marketing_image_provider_name() == "mock"


def _api_size(width, height):
    ratio = float(height) / float(width or 1)
    if ratio >= 1.2:
        return "1024x1536"
    if ratio <= 0.85:
        return "1536x1024"
    return "1024x1024"


def _fit_output(raw, size):
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    fitted = fit_cover(image, size[0], size[1])
    out = io.BytesIO()
    fitted.save(out, format="PNG")
    return out.getvalue()


class MarketingImageError(Exception):
    pass


class MarketingImageProvider:
    def generate_background(self, *, prompt, size, visual_direction):
        raise NotImplementedError

    def generate_creative(self, *, prompt, size, visual_direction=None, references=None):
        return self.generate_background(
            prompt=prompt,
            size=size,
            visual_direction=visual_direction,
        )


class MockMarketingImageProvider(MarketingImageProvider):
    last_call = None

    def generate_background(self, *, prompt, size, visual_direction):
        return self.generate_creative(
            prompt=prompt,
            size=size,
            visual_direction=visual_direction,
            references=None,
        )

    def generate_creative(self, *, prompt, size, visual_direction=None, references=None):
        refs = list(references or [])
        MockMarketingImageProvider.last_call = {
            "prompt": prompt,
            "size": size,
            "visual_direction": visual_direction,
            "reference_roles": [item.get("role") for item in refs],
            "agent_photo_sent_to_provider": any(item.get("role") == "agent" for item in refs),
            "property_refs": sum(1 for item in refs if str(item.get("role") or "").startswith("property")),
            "model": "mock",
        }
        width, height = size
        canvas = Image.new("RGBA", size, (*WHITE, 255))
        draw = ImageDraw.Draw(canvas)
        direction = visual_direction or ""
        if direction in {"property_hero", "luxury_minimal", "luxury_editorial", "editorial_dark", "photo_lifestyle"}:
            draw.rectangle((0, 0, width, height), fill=(*NAVY, 255))
        photos = []
        agent = None
        for item in refs:
            try:
                opened = Image.open(io.BytesIO(item["bytes"]))
                opened.load()
            except Exception:
                continue
            if item.get("role") == "agent":
                agent = opened
            elif str(item.get("role") or "").startswith("property"):
                photos.append(opened.convert("RGB"))
        if direction in {"property_hero", "luxury_minimal", "luxury_editorial", "editorial_dark"} and photos:
            canvas.paste(fit_cover(photos[0], width, int(height * 0.82)), (0, 0))
            draw.rectangle((0, int(height * 0.78), width, height), fill=(*NAVY, 255))
        elif direction in {"clean_collage", "bright_architectural", "bright_geometric", "bold_grid"} and photos:
            canvas.paste(fit_cover(photos[0], int(width * 0.62), int(height * 0.58)), (int(width * 0.04), int(height * 0.08)))
            if len(photos) > 1:
                canvas.paste(fit_cover(photos[1], int(width * 0.30), int(height * 0.27)), (int(width * 0.68), int(height * 0.08)))
            if len(photos) > 2:
                canvas.paste(fit_cover(photos[2], int(width * 0.30), int(height * 0.27)), (int(width * 0.68), int(height * 0.38)))
        elif photos:
            canvas.paste(fit_cover(photos[0], width, int(height * 0.62)), (0, 0))
            if len(photos) > 1:
                canvas.paste(fit_cover(photos[1], int(width * 0.46), int(height * 0.18)), (int(width * 0.04), int(height * 0.66)))
        if agent is not None:
            paste_rounded(
                canvas,
                agent,
                (int(width * 0.68), int(height * 0.66)),
                (int(width * 0.24), int(width * 0.30)),
                radius=26,
            )
        image = canvas.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


class OpenAIMarketingImageProvider(MarketingImageProvider):
    def generate_background(self, *, prompt, size, visual_direction):
        return self.generate_creative(
            prompt=prompt,
            size=size,
            visual_direction=visual_direction,
            references=None,
        )

    def generate_creative(self, *, prompt, size, visual_direction=None, references=None):
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise MarketingImageError("missing_openai_api_key")
        refs = list(references or [])
        model = get_marketing_image_model()
        width, height = size
        api_size = _api_size(width, height)
        logger.info(
            "marketing_image provider=openai model=%s refs=%s agent_photo_sent_to_provider=%s",
            model,
            len(refs),
            any(item.get("role") == "agent" for item in refs),
        )
        if refs:
            raw = self._edits(api_key, model, prompt, api_size, refs)
        else:
            raw = self._generate(api_key, model, prompt, api_size)
        return _fit_output(raw, size)

    def _generate(self, api_key, model, prompt, api_size):
        payload = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": api_size,
            "quality": os.environ.get("MARKETING_IMAGE_QUALITY", "high"),
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/images/generations",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return self._read_image(request)

    def _edits(self, api_key, model, prompt, api_size, refs):
        try:
            import requests
        except ImportError as error:
            raise MarketingImageError("openai_requests_missing") from error
        files = [
            ("image[]", (item.get("name") or f"ref-{index}.png", item["bytes"], item.get("mime") or "image/png"))
            for index, item in enumerate(refs[:5])
        ]
        data = {
            "model": model,
            "prompt": prompt,
            "n": "1",
            "size": api_size,
            "quality": os.environ.get("MARKETING_IMAGE_QUALITY", "high"),
        }
        last_error = None
        for attempt in range(2):
            try:
                response = requests.post(
                    "https://api.openai.com/v1/images/edits",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=data,
                    files=files,
                    timeout=180,
                )
                if response.status_code in {429, 500, 502, 503} and attempt == 0:
                    time.sleep(2)
                    last_error = MarketingImageError(f"openai_http_{response.status_code}")
                    continue
                if response.status_code >= 400:
                    raise MarketingImageError(f"openai_http_{response.status_code}")
                body = response.json()
                return self._decode_body(body)
            except MarketingImageError:
                raise
            except Exception as error:
                raise MarketingImageError("openai_request_failed") from error
        raise last_error or MarketingImageError("openai_request_failed")

    def _read_image(self, request):
        last_error = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    body = json.loads(response.read().decode("utf-8"))
                return self._decode_body(body)
            except urllib.error.HTTPError as error:
                last_error = MarketingImageError(f"openai_http_{error.code}")
                logger.info("marketing_image openai_http_%s attempt_%s", error.code, attempt)
                if error.code in {429, 500, 502, 503} and attempt == 0:
                    time.sleep(2)
                    continue
                raise last_error from error
            except MarketingImageError:
                raise
            except Exception as error:
                raise MarketingImageError("openai_request_failed") from error
        raise last_error or MarketingImageError("openai_request_failed")

    def _decode_body(self, body):
        b64 = ((body.get("data") or [{}])[0] or {}).get("b64_json")
        if not b64:
            raise MarketingImageError("openai_empty_image")
        import base64

        return base64.b64decode(b64)


OpenAIImageProvider = OpenAIMarketingImageProvider


def get_marketing_image_provider():
    name = get_marketing_image_provider_name()
    if name == "openai":
        return OpenAIMarketingImageProvider()
    if name == "mock":
        return MockMarketingImageProvider()
    raise MarketingImageError("image_provider_unavailable")


def finished_ad_prompt(art, fmt, *, references=None, options=None, used_directions=None):
    from modules.marketing_visual_spec import STYLE_REFERENCE_LABEL, AVOID

    direction = (art or {}).get("visual_direction") or "editorial_navy"
    brief = (art or {}).get("visual_brief") or (art or {}).get("layout") or {}
    labels = "\n".join(item.get("label") or "" for item in (references or []) if item.get("label"))
    avoid = ", ".join(sorted(set(list(used_directions or []) + list(AVOID))))
    agent_line = (
        "The REAL AGENT portrait must stay that exact person. Reserve space for the final cutout."
        if any(item.get("role") == "agent" for item in (references or []))
        else "Do not include any agent portrait or invented person."
    )
    return (
        "You are an award-winning art director for premium real-estate advertising. "
        "Match the APPROVED JRH STYLE reference in polish, hierarchy, photographic "
        "prominence and agent integration. Do not copy its property, person, text or exact layout. "
        f"{STYLE_REFERENCE_LABEL} "
        f"Format: {fmt} 9:16-aware vertical ad. Direction: {direction}. "
        f"Brief: {brief}. "
        "REAL property photos must dominate. Do not invent another listing. "
        f"{agent_line} "
        "Do not render addresses, prices, names or the JRH logo — the compositor adds those. "
        "Very low copy. One short decorative headline is allowed. "
        f"Avoid: {avoid}. "
        f"Reference map:\n{labels}"
    )


def background_prompt(art, fmt):
    return finished_ad_prompt(art, fmt)
