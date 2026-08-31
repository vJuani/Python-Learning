"""
Resolve the active EmailProvider from environment.
"""

from __future__ import annotations

import logging
import os

from modules.config import is_deployed
from modules.email_providers.base import EmailDeliveryError, EmailProvider
from modules.email_providers.console import (
    ConsoleEmailProvider,
    MockEmailProvider,
)
from modules.email_providers.resend_api import ResendApiEmailProvider
from modules.email_providers.smtp import SmtpEmailProvider


logger = logging.getLogger(__name__)

_PROVIDER: EmailProvider | None = None

CONSOLE_BACKENDS = frozenset({"console", "mock"})
RESEND_BACKEND = "resend"
SMTP_BACKEND = "smtp"


def get_email_backend() -> str:
    return os.environ.get(
        "EMAIL_BACKEND",
        "console",
    ).strip().lower()


def is_console_email_backend() -> bool:
    return get_email_backend() in CONSOLE_BACKENDS


def reset_email_provider_cache() -> None:
    global _PROVIDER
    _PROVIDER = None


def get_email_provider() -> EmailProvider:
    global _PROVIDER

    if _PROVIDER is not None:
        return _PROVIDER

    backend = get_email_backend()

    if backend in CONSOLE_BACKENDS:
        if is_deployed():
            detail = (
                f"EMAIL_BACKEND={backend} is not allowed when "
                "APP_ENV is staging/production. "
                "Set EMAIL_BACKEND=resend or EMAIL_BACKEND=smtp."
            )
            logger.error(
                "email_provider_blocked detail=%s",
                detail,
            )
            raise EmailDeliveryError(
                "err_verify_email_not_configured",
                detail=detail,
            )
        _PROVIDER = (
            MockEmailProvider()
            if backend == "mock"
            else ConsoleEmailProvider()
        )
        return _PROVIDER

    if backend == RESEND_BACKEND:
        provider = ResendApiEmailProvider()
        if is_deployed():
            provider.validate_config()
        _PROVIDER = provider
        logger.info(
            "email_provider_selected backend=%s provider=%s",
            backend,
            type(provider).__name__,
        )
        return _PROVIDER

    if backend == SMTP_BACKEND:
        provider = SmtpEmailProvider()
        if is_deployed():
            provider.validate_config()
        _PROVIDER = provider
        logger.info(
            "email_provider_selected backend=%s provider=%s",
            backend,
            type(provider).__name__,
        )
        return _PROVIDER

    detail = (
        f"Unknown EMAIL_BACKEND={backend!r}. "
        "Use console, mock, smtp, or resend."
    )
    logger.error("email_provider_invalid detail=%s", detail)
    raise EmailDeliveryError(
        "err_verify_email_not_configured",
        detail=detail,
    )
