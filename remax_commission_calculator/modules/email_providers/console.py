"""
Console email provider — development and automated tests only.
"""

from __future__ import annotations

import logging
from pathlib import Path

from modules.config import BASE_DIR
from modules.email_providers._log import mask_email_for_log, normalize_recipient
from modules.email_providers.base import EmailDeliveryError, OutboundEmail


logger = logging.getLogger(__name__)


def _append_inbox(to_email: str, subject: str, body: str) -> None:
    inbox_path = BASE_DIR / "tmp_email_inbox.txt"
    flat_body = " ".join(str(body).splitlines()).strip()

    with open(inbox_path, "a", encoding="utf-8") as inbox:
        inbox.write(f"{to_email}\t{subject}\t{flat_body}\n")


class ConsoleEmailProvider:
    """Print emails to stdout and tmp_email_inbox.txt."""

    backend_name = "console"

    def send(self, message: OutboundEmail) -> None:
        to_email = normalize_recipient(message.to)
        logger.info(
            "email_send_console to=%s subject=%s",
            mask_email_for_log(to_email),
            message.subject,
        )
        print()
        print("=== EMAIL (console backend) ===")
        print(f"To: {to_email}")
        print(f"Subject: {message.subject}")
        print("--- text/plain ---")
        print(message.text_body)

        if message.html_body:
            print("--- text/html ---")
            print(
                f"(html length={len(message.html_body)} chars)"
            )

        print("================================")
        print()
        _append_inbox(to_email, message.subject, message.text_body)


class MockEmailProvider(ConsoleEmailProvider):
    """Alias used by tests; identical to console (no network)."""

    backend_name = "mock"
