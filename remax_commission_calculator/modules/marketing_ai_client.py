"""OpenAI client for Marketing IA copy. Independent from Cash AI.

Chat Completions + strict json_schema. Creative temperature. No API keys in logs.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request


logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_COPY_TEMPERATURE = 0.8
DEFAULT_BRIEF_TEMPERATURE = 0.4
DEFAULT_TIMEOUT = 60
MAX_ATTEMPTS = 3

RETRY_CODES = {
    "openai_timeout",
    "openai_http_429",
    "openai_http_500",
    "openai_http_502",
    "openai_http_503",
    "openai_http_529",
}


class MarketingAIClientError(Exception):
    def __init__(self, code, *, details=None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


def get_marketing_ai_model():
    return (os.environ.get("MARKETING_AI_MODEL") or "").strip() or DEFAULT_MODEL


def get_copy_temperature():
    raw = (os.environ.get("MARKETING_AI_TEMPERATURE") or "").strip()
    if not raw:
        return DEFAULT_COPY_TEMPERATURE
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_COPY_TEMPERATURE
    return max(0.2, min(1.2, value))


def get_brief_temperature():
    return min(get_copy_temperature(), DEFAULT_BRIEF_TEMPERATURE + 0.15)


def _sanitize_error_body(raw_text):
    text = (raw_text or "")[:400]
    return text.replace("sk-", "sk-***")


def _is_timeout_error(error):
    if isinstance(error, (TimeoutError, socket.timeout)):
        return True
    reason = getattr(error, "reason", None)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    name = type(error).__name__.lower()
    message = str(error).lower()
    return "timeout" in name or "timed out" in message or "timeout" in message


def _usage_from_body(body):
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0}
    try:
        input_tokens = int(usage.get("prompt_tokens") or 0)
    except (TypeError, ValueError):
        input_tokens = 0
    try:
        output_tokens = int(usage.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        output_tokens = 0
    return {
        "input_tokens": max(0, input_tokens),
        "output_tokens": max(0, output_tokens),
    }


def _post_once(*, payload, timeout):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise MarketingAIClientError(
            "missing_openai_api_key",
            details={"openai_api_key_present": False},
        )
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
            request_id = response.headers.get("x-request-id") or response.headers.get(
                "X-Request-Id"
            )
    except urllib.error.HTTPError as error:
        detail = _sanitize_error_body(error.read().decode("utf-8", errors="ignore"))
        request_id = error.headers.get("x-request-id") if error.headers else None
        logger.error(
            "marketing_ai http_status=%s request_id=%s detail=%s",
            error.code,
            request_id,
            detail,
        )
        raise MarketingAIClientError(
            f"openai_http_{error.code}",
            details={"http_status": error.code, "request_id": request_id},
        ) from error
    except Exception as error:
        code = "openai_timeout" if _is_timeout_error(error) else "openai_request_failed"
        logger.error("marketing_ai request_failed type=%s", type(error).__name__)
        raise MarketingAIClientError(
            code,
            details={"error_type": type(error).__name__},
        ) from error
    try:
        body = json.loads(raw_body)
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        usage = _usage_from_body(body)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        logger.error("marketing_ai invalid_response request_id=%s", request_id)
        raise MarketingAIClientError(
            "openai_invalid_response",
            details={"error_type": type(error).__name__, "request_id": request_id},
        ) from error
    if not isinstance(parsed, dict):
        raise MarketingAIClientError("openai_invalid_response")
    return parsed, usage


def request_marketing_text(
    *,
    instructions,
    user_payload,
    model=None,
    temperature=None,
    max_tokens=700,
    timeout=DEFAULT_TIMEOUT,
):
    """Plain conversational reply. Never logs secrets or full listing text."""
    model = model or get_marketing_ai_model()
    if temperature is None:
        temperature = get_copy_temperature()
    user_text = (
        user_payload if isinstance(user_payload, str) else json.dumps(user_payload, ensure_ascii=False)
    )
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": int(max_tokens),
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_text},
        ],
    }
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise MarketingAIClientError(
            "missing_openai_api_key",
            details={"openai_api_key_present": False},
        )
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
    except Exception as error:
        code = "openai_timeout" if _is_timeout_error(error) else "openai_request_failed"
        if isinstance(error, urllib.error.HTTPError):
            code = f"openai_http_{error.code}"
        raise MarketingAIClientError(code) from error
    text = (content or "").strip()
    if not text:
        raise MarketingAIClientError("openai_invalid_response")
    return text


def request_marketing_json(
    *,
    instructions,
    user_payload,
    schema,
    schema_name,
    model=None,
    temperature=None,
    max_tokens=700,
    timeout=DEFAULT_TIMEOUT,
):
    """Single structured Marketing call. Never logs secrets or full listing text."""
    model = model or get_marketing_ai_model()
    if temperature is None:
        temperature = get_copy_temperature()
    user_text = (
        user_payload if isinstance(user_payload, str) else json.dumps(user_payload, ensure_ascii=False)
    )
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": int(max_tokens),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        },
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_text},
        ],
    }
    logger.info(
        "marketing_ai request schema=%s model=%s temperature=%s max_tokens=%s payload_bytes=%s",
        schema_name,
        model,
        temperature,
        max_tokens,
        len(json.dumps(payload).encode("utf-8")),
    )
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            parsed, usage = _post_once(payload=payload, timeout=timeout)
            logger.info(
                "marketing_ai ok schema=%s attempt=%s input_tokens=%s output_tokens=%s",
                schema_name,
                attempt,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
            )
            return parsed, usage
        except MarketingAIClientError as error:
            last_error = error
            if error.code not in RETRY_CODES or attempt >= MAX_ATTEMPTS:
                raise
            time.sleep(0.35 * attempt)
    raise last_error
