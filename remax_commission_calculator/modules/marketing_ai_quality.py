"""Quality gates for Marketing IA copy. Fail instead of copying fields."""

from __future__ import annotations

import re

from modules.marketing_ai_prompts import HYPE_BANNED


class QualityError(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _text(value):
    return str(value or "").strip()


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def assert_no_hype(parsed):
    blob = " ".join(_walk_strings(parsed)).casefold()
    for phrase in HYPE_BANNED:
        if phrase in blob:
            raise QualityError("hype_phrase")


def _distinct(*parts):
    norms = [_norm(part) for part in parts if _norm(part)]
    return len(norms) == len(set(norms))


def validate_brief(parsed):
    if not isinstance(parsed, dict):
        raise QualityError("brief_not_object")
    for key in (
        "audience",
        "primary_goal",
        "creative_angle",
        "main_hook",
        "cta_strategy",
    ):
        if not _text(parsed.get(key)):
            raise QualityError("brief_missing_field")
    facts = parsed.get("key_facts")
    if not isinstance(facts, list) or not facts:
        raise QualityError("brief_missing_facts")
    return parsed


def validate_post(parsed):
    if not isinstance(parsed, dict):
        raise QualityError("post_not_object")
    headline = _text(parsed.get("headline"))
    caption = _text(parsed.get("caption"))
    cta = _text(parsed.get("cta"))
    hashtags = parsed.get("hashtags")
    if not headline or not caption or not cta:
        raise QualityError("post_missing_field")
    if _norm(headline) == _norm(caption):
        raise QualityError("post_headline_equals_caption")
    if _norm(cta) == _norm(headline):
        raise QualityError("post_cta_equals_headline")
    if caption.casefold().startswith(headline.casefold()):
        raise QualityError("post_caption_repeats_headline")
    if not (12 <= len(caption) <= 900):
        raise QualityError("post_caption_length")
    if not (4 <= len(headline) <= 90):
        raise QualityError("post_headline_length")
    if not isinstance(hashtags, list) or not hashtags:
        raise QualityError("post_hashtags")
    assert_no_hype(parsed)
    return {
        "headline": headline,
        "caption": caption,
        "cta": cta,
        "hashtags": [str(tag).strip() for tag in hashtags if str(tag).strip()][:8],
    }


def validate_story(parsed):
    if not isinstance(parsed, dict):
        raise QualityError("story_not_object")
    frames = parsed.get("frames")
    if not isinstance(frames, list) or len(frames) < 3:
        raise QualityError("story_frames")
    cleaned = []
    seen = set()
    for index, frame in enumerate(frames[:5]):
        if not isinstance(frame, dict):
            raise QualityError("story_frame")
        headline = _text(frame.get("headline"))
        body = _text(frame.get("body"))
        cta = _text(frame.get("cta"))
        if not headline or not body:
            raise QualityError("story_frame_empty")
        signature = (_norm(headline), _norm(body))
        if signature in seen:
            raise QualityError("story_duplicate_frame")
        seen.add(signature)
        if index == len(frames[:5]) - 1 and not cta:
            raise QualityError("story_missing_final_cta")
        cleaned.append({"headline": headline, "body": body, "cta": cta})
    if not _distinct(*(item["headline"] for item in cleaned)):
        raise QualityError("story_same_headlines")
    assert_no_hype({"frames": cleaned})
    return {"frames": cleaned}


def validate_carousel(parsed):
    if not isinstance(parsed, dict):
        raise QualityError("carousel_not_object")
    hook = _text(parsed.get("hook"))
    final_cta = _text(parsed.get("final_cta"))
    slides = parsed.get("slides")
    if not hook or not final_cta:
        raise QualityError("carousel_missing_field")
    if not isinstance(slides, list) or not (3 <= len(slides) <= 5):
        raise QualityError("carousel_slides")
    cleaned = []
    seen = set()
    for index, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            raise QualityError("carousel_slide")
        title = _text(slide.get("title"))
        body = _text(slide.get("body"))
        if not title or not body:
            raise QualityError("carousel_slide_empty")
        signature = (_norm(title), _norm(body))
        if signature in seen:
            raise QualityError("carousel_duplicate_slide")
        seen.add(signature)
        if _norm(title) == _norm(hook) and index == 1 and _norm(body) == _norm(hook):
            raise QualityError("carousel_slide_copies_hook")
        cleaned.append({"slide": index, "title": title, "body": body})
    if _norm(final_cta) == _norm(hook):
        raise QualityError("carousel_cta_equals_hook")
    assert_no_hype({"hook": hook, "slides": cleaned, "final_cta": final_cta})
    return {"hook": hook, "slides": cleaned, "final_cta": final_cta}


def validate_whatsapp(parsed):
    if not isinstance(parsed, dict):
        raise QualityError("whatsapp_not_object")
    message = _text(parsed.get("message"))
    cta = _text(parsed.get("cta"))
    if not message or not cta:
        raise QualityError("whatsapp_missing_field")
    if "#" in message or "#" in cta:
        raise QualityError("whatsapp_hashtags")
    if _norm(message) == _norm(cta):
        raise QualityError("whatsapp_cta_copies_message")
    if not (24 <= len(message) <= 700):
        raise QualityError("whatsapp_length")
    if message.count("\n\n") >= 2:
        raise QualityError("whatsapp_looks_like_caption")
    assert_no_hype(parsed)
    return {"message": message, "cta": cta}


def validate_copy(parsed):
    if not isinstance(parsed, dict):
        raise QualityError("copy_not_object")
    headline = _text(parsed.get("headline"))
    body = _text(parsed.get("body"))
    cta = _text(parsed.get("cta"))
    if not headline or not body or not cta:
        raise QualityError("copy_missing_field")
    if _norm(headline) == _norm(body):
        raise QualityError("copy_headline_equals_body")
    if _norm(cta) == _norm(headline):
        raise QualityError("copy_cta_equals_headline")
    if not (12 <= len(body) <= 800):
        raise QualityError("copy_body_length")
    assert_no_hype(parsed)
    return {"headline": headline, "body": body, "cta": cta}


VALIDATORS = {
    "post": validate_post,
    "story": validate_story,
    "carousel": validate_carousel,
    "whatsapp": validate_whatsapp,
    "copy": validate_copy,
}


def validate_format_output(parsed, content_type):
    validator = VALIDATORS.get(content_type)
    if validator is None:
        raise QualityError("unknown_content_type")
    return validator(parsed)
