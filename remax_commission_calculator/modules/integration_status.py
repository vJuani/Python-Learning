"""Admin-facing integration flags. Booleans only; never secret values."""

from __future__ import annotations

import os


def _mail_configured() -> bool:
    from modules.email_providers.factory import get_email_backend

    backend = get_email_backend()
    if backend == "resend":
        return bool(os.environ.get("RESEND_API_KEY", "").strip())
    if backend == "smtp":
        return bool(
            os.environ.get("SMTP_HOST", "").strip()
            and os.environ.get("SMTP_PASSWORD", "")
        )
    return False


def _openai_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _arca_configured() -> bool:
    from modules.arca.config import ArcaEnvironmentError, get_arca_environment

    try:
        get_arca_environment()
    except ArcaEnvironmentError:
        return False
    return True


def describe_integrations() -> list[dict]:
    from modules.google_calendar import is_configured as google_configured
    from modules.web_push import vapid_configured

    return [
        {"key": "mail", "configured": _mail_configured()},
        {"key": "push", "configured": bool(vapid_configured())},
        {"key": "openai", "configured": _openai_configured()},
        {"key": "google_calendar", "configured": bool(google_configured())},
        {"key": "arca", "configured": _arca_configured()},
    ]
