"""Image generation abstraction. Finished ads with real visual references."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import time
import urllib.error
import urllib.request

from PIL import Image, ImageDraw

from modules.marketing_renderer import NAVY, WHITE, fit_contain_safe, fit_cover, paste_rounded
from modules.marketing_visual_spec import (
    EDITORIAL_PREMIUM,
    LUXURY_MINIMAL,
    MODERN_COMMERCIAL,
    format_from_size,
    normalize_style,
    safe_rect,
)

logger = logging.getLogger(__name__)

OPENAI_GENERATIONS_URL = "https://api.openai.com/v1/images/generations"
OPENAI_EDITS_URL = "https://api.openai.com/v1/images/edits"


def sha256_bytes(payload):
    return hashlib.sha256(payload or b"").hexdigest()


def _image_size(payload):
    try:
        image = Image.open(io.BytesIO(payload))
        image.load()
        return image.size
    except Exception:
        return None


def log_marketing_pipeline(audit):
    payload = dict(audit or {})
    payload.pop("raw_bytes", None)
    payload.pop("final_bytes", None)
    logger.info(
        "marketing_pipeline provider=%s model=%s endpoint=%s input_images=%s "
        "input_roles=%s api_size=%s raw_size=%s final_size=%s "
        "legacy_compositor=%s post_process=%s fallback=%s hashes_match=%s",
        payload.get("provider"),
        payload.get("model"),
        payload.get("endpoint"),
        payload.get("input_image_count"),
        payload.get("input_roles"),
        payload.get("api_size"),
        payload.get("raw_size"),
        payload.get("final_size"),
        payload.get("legacy_compositor"),
        payload.get("post_process"),
        payload.get("fallback"),
        payload.get("hashes_match"),
    )
    return payload


def get_marketing_image_model():
    return (
        os.environ.get("OPENAI_IMAGE_MODEL")
        or os.environ.get("MARKETING_IMAGE_MODEL")
        or "gpt-image-1"
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


def _fit_output(raw, size, fmt=None):
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    fmt = fmt or format_from_size(size)
    fitted = fit_contain_safe(image, size, fmt, fill=NAVY)
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
    last_audit = None

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
        style = normalize_style(visual_direction)
        fmt = format_from_size(size)
        left, top, right, bottom = safe_rect(fmt, size)
        inner_w = max(1, right - left)
        inner_h = max(1, bottom - top)
        fill = WHITE if style == MODERN_COMMERCIAL else NAVY
        canvas = Image.new("RGBA", size, (*fill, 255))
        draw = ImageDraw.Draw(canvas)
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
        draw.text((left + 8, top + 8), "JRH One", fill=(255, 255, 255) if fill == NAVY else NAVY)
        if style == LUXURY_MINIMAL and photos:
            hero_h = int(inner_h * 0.78)
            canvas.paste(fit_cover(photos[0], inner_w, hero_h), (left, top + 36))
        elif style == MODERN_COMMERCIAL and photos:
            hero_w = int(inner_w * 0.72)
            hero_h = int(inner_h * 0.52)
            canvas.paste(fit_cover(photos[0], hero_w, hero_h), (left, top + 48))
            if len(photos) > 1:
                extra = int(inner_w * 0.24)
                canvas.paste(
                    fit_cover(photos[1], extra, int(hero_h * 0.46)),
                    (left + hero_w + 16, top + 48),
                )
        elif photos:
            hero_h = int(inner_h * 0.50)
            canvas.paste(fit_cover(photos[0], inner_w, hero_h), (left, top + 56))
            if len(photos) > 1:
                thumb_w = int(inner_w * 0.30)
                thumb_h = int(inner_h * 0.16)
                canvas.paste(fit_cover(photos[1], thumb_w, thumb_h), (left, top + 72 + hero_h))
        ink = (255, 255, 255) if fill == NAVY else NAVY
        draw.text((left + 8, bottom - 64), "Consultame", fill=ink)
        if agent is not None:
            agent_w = min(int(width * 0.18), int(inner_w * 0.28))
            agent_h = int(agent_w * 1.2)
            if top + agent_h + 54 > bottom:
                agent_w = max(72, int(agent_w * 0.75))
                agent_h = int(agent_w * 1.2)
            paste_rounded(
                canvas,
                agent,
                (right - agent_w, bottom - agent_h - 36),
                (agent_w, agent_h),
                radius=22,
            )
            draw.text((right - agent_w, bottom - 28), "Agente", fill=ink)
        image = canvas.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        raw = buffer.getvalue()
        MockMarketingImageProvider.last_audit = {
            "provider": "mock",
            "model": "mock",
            "endpoint": "mock",
            "input_image_count": len(refs),
            "input_roles": [item.get("role") for item in refs],
            "api_size": f"{width}x{height}",
            "raw_size": (width, height),
            "final_size": (width, height),
            "legacy_compositor": True,
            "legacy_compositor_fn": "MockMarketingImageProvider.generate_creative",
            "post_process": "none",
            "fallback": True,
            "hashes_match": True,
            "raw_sha256": sha256_bytes(raw),
            "final_sha256": sha256_bytes(raw),
        }
        log_marketing_pipeline(MockMarketingImageProvider.last_audit)
        return raw

    def generate_with_audit(
        self,
        *,
        prompt,
        size,
        visual_direction=None,
        references=None,
        apply_fit=True,
    ):
        raw = self.generate_creative(
            prompt=prompt,
            size=size,
            visual_direction=visual_direction,
            references=references,
        )
        audit = dict(MockMarketingImageProvider.last_audit or {})
        audit["raw_bytes"] = raw
        audit["final_bytes"] = raw
        audit["post_process"] = "none"
        return audit


class OpenAIMarketingImageProvider(MarketingImageProvider):
    last_audit = None

    def generate_background(self, *, prompt, size, visual_direction):
        return self.generate_creative(
            prompt=prompt,
            size=size,
            visual_direction=visual_direction,
            references=None,
        )

    def generate_creative(self, *, prompt, size, visual_direction=None, references=None):
        result = self.generate_with_audit(
            prompt=prompt,
            size=size,
            visual_direction=visual_direction,
            references=references,
            apply_fit=True,
        )
        return result["final_bytes"]

    def generate_with_audit(
        self,
        *,
        prompt,
        size,
        visual_direction=None,
        references=None,
        apply_fit=True,
    ):
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise MarketingImageError("missing_openai_api_key")
        refs = list(references or [])
        model = get_marketing_image_model()
        width, height = size
        api_size = _api_size(width, height)
        endpoint = OPENAI_EDITS_URL if refs else OPENAI_GENERATIONS_URL
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
        final = _fit_output(raw, size, format_from_size(size)) if apply_fit else raw
        audit = {
            "provider": "openai",
            "model": model,
            "endpoint": endpoint,
            "input_image_count": min(len(refs), 5),
            "input_roles": [item.get("role") for item in refs[:5]],
            "api_size": api_size,
            "requested_size": size,
            "raw_size": _image_size(raw),
            "final_size": _image_size(final),
            "legacy_compositor": False,
            "legacy_compositor_fn": None,
            "post_process": "fit_contain_safe" if apply_fit else "none",
            "post_process_fn": (
                "modules.marketing_image_provider._fit_output"
                if apply_fit
                else None
            ),
            "fallback": False,
            "raw_sha256": sha256_bytes(raw),
            "final_sha256": sha256_bytes(final),
            "hashes_match": sha256_bytes(raw) == sha256_bytes(final),
            "raw_bytes": raw,
            "final_bytes": final,
        }
        OpenAIMarketingImageProvider.last_audit = {
            key: value for key, value in audit.items() if key not in {"raw_bytes", "final_bytes"}
        }
        log_marketing_pipeline(audit)
        return audit

    def _generate(self, api_key, model, prompt, api_size):
        payload = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": api_size,
            "quality": os.environ.get("MARKETING_IMAGE_QUALITY", "high"),
        }
        request = urllib.request.Request(
            OPENAI_GENERATIONS_URL,
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
                    OPENAI_EDITS_URL,
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


def generate_with_audit(*, prompt, size, visual_direction=None, references=None, apply_fit=True):
    provider = get_marketing_image_provider()
    if hasattr(provider, "generate_with_audit"):
        return provider.generate_with_audit(
            prompt=prompt,
            size=size,
            visual_direction=visual_direction,
            references=references,
            apply_fit=apply_fit,
        )
    raw = provider.generate_creative(
        prompt=prompt,
        size=size,
        visual_direction=visual_direction,
        references=references,
    )
    return {
        "raw_bytes": raw,
        "final_bytes": raw,
        "provider": get_marketing_image_provider_name(),
        "model": get_marketing_image_model(),
        "endpoint": "unknown",
        "legacy_compositor": False,
        "post_process": "none",
        "fallback": get_marketing_image_provider_name() != "openai",
        "hashes_match": True,
        "raw_sha256": sha256_bytes(raw),
        "final_sha256": sha256_bytes(raw),
    }


def finished_ad_prompt(art, fmt, *, references=None, options=None, used_directions=None):
    from modules.marketing_visual_spec import AVOID, STYLE_REFERENCE_LABEL, safe_area_prompt

    direction = (art or {}).get("visual_direction") or "editorial_premium"
    brief = (art or {}).get("visual_brief") or (art or {}).get("layout") or {}
    labels = "\n".join(item.get("label") or "" for item in (references or []) if item.get("label"))
    avoid = ", ".join(sorted(set(list(used_directions or []) + list(AVOID))))
    agent_line = (
        "The REAL AGENT portrait must stay that exact person. "
        "Keep head, shoulders and the name fully inside the safe area."
        if any(item.get("role") == "agent" for item in (references or []))
        else "Do not include any agent portrait or invented person."
    )
    return (
        "You are an award-winning art director for premium real-estate advertising. "
        "This is the finished ad. Editorial, clean, modern, elegant. "
        "Do not copy the style reference layout, collage, curve or edge type. "
        f"{STYLE_REFERENCE_LABEL} "
        f"Format: {fmt} vertical ad. Direction: {direction}. "
        f"Brief: {brief}. "
        f"{safe_area_prompt(fmt)} "
        "REAL property photos must dominate. Do not invent another listing. "
        f"{agent_line} "
        "Short copy only. No decorative slogans, no vertical captions. "
        f"Avoid: {avoid}. "
        f"Reference map:\n{labels}"
    )


def background_prompt(art, fmt):
    return finished_ad_prompt(art, fmt)
