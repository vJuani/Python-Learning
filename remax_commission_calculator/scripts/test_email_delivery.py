#!/usr/bin/env python3
"""
Send a one-off test email using the configured EmailProvider.

Usage (Railway shell or local with production-like env):
  EMAIL_BACKEND=resend
  RESEND_API_KEY=re_...
  EMAIL_FROM="JRH One <noreply@jrhone.com>"
  python scripts/test_email_delivery.py --to you@example.com

Never run against production users without --to pointing to your inbox.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.config import load_dotenv_file

load_dotenv_file()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a test transactional email"
    )
    parser.add_argument(
        "--to",
        required=True,
        help="Recipient inbox (use your own email)",
    )
    args = parser.parse_args()

    recipient = os.environ.get(
        "TEST_EMAIL_TO",
        args.to,
    ).strip().lower()

    from modules.email_providers import reset_email_provider_cache
    from modules.email_delivery import send_transactional_email

    reset_email_provider_cache()

    backend = os.environ.get("EMAIL_BACKEND", "console")
    print(f"Backend: {backend}")
    print(f"Sending test email to: {recipient}")

    send_transactional_email(
        recipient,
        "Commission Calculator — test email",
        (
            "This is a test message from scripts/test_email_delivery.py\n"
            "If you received this, Resend HTTP API is configured correctly."
        ),
        html_body=(
            "<p>This is a <strong>test</strong> from "
            "<code>test_email_delivery.py</code>.</p>"
        ),
    )

    print("OK — check your inbox (and spam folder).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
