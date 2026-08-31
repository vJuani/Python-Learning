"""
Resend HTTP API email provider (port 443).
"""

from __future__ import annotations

import logging
import os

import requests

from modules.email_providers._log import mask_email_for_log, normalize_recipient
from modules.email_providers.base import EmailDeliveryError, OutboundEmail


logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
REQUEST_TIMEOUT_SECONDS = 20


def _sanitize_response_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("name")
        if message:
            return str(message)[:200]

    text = (response.text or "").strip()
    if text:
        return text[:200]

    return f"http_status={response.status_code}"


class ResendApiEmailProvider:
    """Deliver email via Resend REST API (HTTPS)."""

    backend_name = "resend"

    def _config(self) -> dict:
        return {
            "api_key": os.environ.get("RESEND_API_KEY", "").strip(),
            "sender": os.environ.get("EMAIL_FROM", "").strip(),
        }

    def validate_config(self) -> None:
        cfg = self._config()

        if cfg["api_key"] == "":
            detail = "RESEND_API_KEY is empty"
            logger.error("email_config_invalid detail=%s", detail)
            raise EmailDeliveryError(
                "err_verify_email_not_configured",
                detail=detail,
            )

        if cfg["sender"] == "":
            detail = "EMAIL_FROM is required"
            logger.error("email_config_invalid detail=%s", detail)
            raise EmailDeliveryError(
                "err_verify_email_not_configured",
                detail=detail,
            )

    def send(self, message: OutboundEmail) -> None:
        self.validate_config()
        cfg = self._config()
        to_email = normalize_recipient(message.to)

        payload: dict = {
            "from": cfg["sender"],
            "to": [to_email],
            "subject": message.subject,
            "text": message.text_body,
        }

        if message.html_body:
            payload["html"] = message.html_body

        logger.info(
            "email_resend_request to=%s subject=%s",
            mask_email_for_log(to_email),
            message.subject,
        )

        try:
            response = requests.post(
                RESEND_API_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.exceptions.Timeout as exc:
            detail = "timed out"
            logger.error(
                "email_send_failed backend=resend status=timeout "
                "detail=%s",
                detail,
            )
            raise EmailDeliveryError(
                "err_verify_email_send_failed",
                detail=detail,
            ) from exc
        except requests.exceptions.RequestException as exc:
            detail = str(exc)[:200]
            logger.error(
                "email_send_failed backend=resend status=connection "
                "detail=%s",
                detail,
            )
            raise EmailDeliveryError(
                "err_verify_email_send_failed",
                detail=detail,
            ) from exc

        if response.status_code in (200, 201):
            logger.info("email_send_success backend=resend")
            return

        detail = _sanitize_response_detail(response)
        logger.error(
            "email_send_failed backend=resend status=%s detail=%s",
            response.status_code,
            detail,
        )
        raise EmailDeliveryError(
            "err_verify_email_send_failed",
            detail=detail,
        )
