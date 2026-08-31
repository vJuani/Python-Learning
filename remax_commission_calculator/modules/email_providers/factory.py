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
from modules.email_providers.smtp import SmtpEmailProvider


logger = logging.getLogger(__name__)

_PROVIDER: EmailProvider | None = None

CONSOLE_BACKENDS = frozenset({"console", "mock"})
SMTP_BACKENDS = frozenset({"smtp", "resend"})


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
                "Set EMAIL_BACKEND=smtp or EMAIL_BACKEND=resend."
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

    if backend in SMTP_BACKENDS:
        provider = SmtpEmailProvider(preset=backend)
        if is_deployed():
            provider.validate_config()
        _PROVIDER = provider
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
