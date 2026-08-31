"""
SMTP email provider — generic SMTP (SendGrid, Mailgun, etc.).
Not used for Resend; use EMAIL_BACKEND=resend for Resend HTTP API.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

from modules.email_providers._log import mask_email_for_log, normalize_recipient
from modules.email_providers.base import EmailDeliveryError, OutboundEmail


logger = logging.getLogger(__name__)


def _smtp_error_detail(exc: Exception) -> str:
    code = getattr(exc, "smtp_code", None)
    error = getattr(exc, "smtp_error", None)

    if isinstance(error, (bytes, bytearray)):
        error = error.decode("utf-8", errors="replace")

    if code is None and error is None:
        return str(exc)[:200]

    return f"smtp_code={code} smtp_error={error}"


class SmtpEmailProvider:
    """Deliver email via SMTP (TLS + auth)."""

    backend_name = "smtp"

    def _config(self) -> dict:
        host = os.environ.get("SMTP_HOST", "").strip()
        username = os.environ.get("SMTP_USERNAME", "").strip()
        password = os.environ.get("SMTP_PASSWORD", "")
        sender = os.environ.get("EMAIL_FROM", "").strip()

        if sender == "":
            sender = os.environ.get("SMTP_FROM", username).strip()

        port_raw = os.environ.get("SMTP_PORT", "").strip() or "587"
        use_tls_raw = os.environ.get("SMTP_USE_TLS", "").strip() or "1"

        return {
            "host": host,
            "port": int(port_raw),
            "username": username,
            "password": password,
            "sender": sender,
            "use_tls": use_tls_raw.lower()
            in ("1", "true", "yes", "on"),
        }

    def validate_config(self) -> None:
        cfg = self._config()

        if not cfg["host"] or not cfg["sender"]:
            detail = "SMTP_HOST and EMAIL_FROM are required"
            logger.error("email_config_invalid detail=%s", detail)
            raise EmailDeliveryError(
                "err_verify_email_not_configured",
                detail=detail,
            )

        if cfg["password"].strip() == "":
            detail = "SMTP_PASSWORD is empty"
            logger.error("email_config_invalid detail=%s", detail)
            raise EmailDeliveryError(
                "err_verify_email_not_configured",
                detail=detail,
            )

    def send(self, message: OutboundEmail) -> None:
        self.validate_config()
        cfg = self._config()
        to_email = normalize_recipient(message.to)

        email = EmailMessage()
        email["Subject"] = message.subject
        email["From"] = cfg["sender"]
        email["To"] = to_email
        email.set_content(message.text_body)

        if message.html_body:
            email.add_alternative(
                message.html_body,
                subtype="html",
            )

        logger.info(
            "email_smtp_connect host=%s port=%s tls=%s "
            "from=%s to=%s",
            cfg["host"],
            cfg["port"],
            cfg["use_tls"],
            cfg["sender"],
            mask_email_for_log(to_email),
        )

        try:
            with smtplib.SMTP(
                cfg["host"],
                cfg["port"],
                timeout=20,
            ) as smtp:
                if cfg["use_tls"]:
                    smtp.starttls()

                if cfg["username"]:
                    smtp.login(
                        cfg["username"],
                        cfg["password"],
                    )

                refused = smtp.send_message(email)
        except smtplib.SMTPAuthenticationError as exc:
            detail = _smtp_error_detail(exc)
            logger.error(
                "email_smtp_auth_failed detail=%s",
                detail,
            )
            raise EmailDeliveryError(
                "err_verify_email_send_failed",
                detail=detail,
            ) from exc
        except smtplib.SMTPSenderRefused as exc:
            detail = _smtp_error_detail(exc)
            logger.error(
                "email_smtp_sender_refused from=%s detail=%s",
                cfg["sender"],
                detail,
            )
            raise EmailDeliveryError(
                "err_verify_email_send_failed",
                detail=detail,
            ) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            detail = _smtp_error_detail(exc)
            logger.error(
                "email_smtp_recipient_refused to=%s detail=%s",
                mask_email_for_log(to_email),
                detail,
            )
            raise EmailDeliveryError(
                "err_verify_email_send_failed",
                detail=detail,
            ) from exc
        except smtplib.SMTPException as exc:
            detail = _smtp_error_detail(exc)
            logger.error(
                "email_smtp_failed detail=%s",
                detail,
            )
            raise EmailDeliveryError(
                "err_verify_email_send_failed",
                detail=detail,
            ) from exc
        except OSError as exc:
            detail = str(exc)[:200]
            logger.error(
                "email_smtp_connection_failed host=%s detail=%s",
                cfg["host"],
                detail,
            )
            raise EmailDeliveryError(
                "err_verify_email_send_failed",
                detail=detail,
            ) from exc

        if refused:
            detail = f"refused={refused}"
            logger.error(
                "email_smtp_recipient_refused to=%s detail=%s",
                mask_email_for_log(to_email),
                detail,
            )
            raise EmailDeliveryError(
                "err_verify_email_send_failed",
                detail=detail,
            )

        logger.info(
            "email_smtp_accepted from=%s to=%s subject=%s",
            cfg["sender"],
            mask_email_for_log(to_email),
            message.subject,
        )
