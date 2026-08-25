"""
Development-only helper: cancel a pending registration request.

Usage:
  python cancel_pending_registration.py --id 17
  python cancel_pending_registration.py --email user@example.com --org 1

Never deletes users. Only removes email_pending / pending_approval
requests and their verification tokens.
"""

import argparse
import sys

from modules.config import apply_config, is_deployed, load_dotenv_file
from modules.database import create_tables
from modules.registration import cancel_pending_registration_for_dev
from web_app import app


def main():
    load_dotenv_file()
    apply_config(app)
    create_tables()

    if is_deployed():
        print(
            "Refused: cancel_pending_registration is "
            "development-only "
            "(APP_ENV=staging or production)."
        )
        return 1

    parser = argparse.ArgumentParser(
        description=(
            "Cancel a pending registration request "
            "(development only)."
        )
    )
    parser.add_argument(
        "--id",
        type=int,
        help="registration_requests.id"
    )
    parser.add_argument(
        "--email",
        help="Email of the pending request"
    )
    parser.add_argument(
        "--org",
        type=int,
        default=1,
        help="organization_id when using --email (default: 1)"
    )
    args = parser.parse_args()

    if args.id is None and not args.email:
        parser.error("Provide --id or --email")

    deleted, error_key = cancel_pending_registration_for_dev(
        request_id=args.id,
        email=args.email,
        organization_id=args.org if args.email else None
    )

    if error_key is not None:
        print(f"Failed: {error_key}")
        return 1

    print(
        "Cancelled pending registration "
        f"id={deleted['id']} "
        f"email={deleted['email']} "
        f"was_status={deleted['status']} "
        f"organization_id={deleted['organization_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
