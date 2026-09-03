"""One-shot recurring agent charge job for Railway scheduled execution."""

from __future__ import annotations

import argparse
from datetime import date

from modules.config import load_dotenv_file
from modules.database import create_tables
from modules.recurring_agent_charges import (
    generate_all_active_organizations,
)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Generate due recurring agent-account charges.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview due charges without changing the database.",
    )
    parser.add_argument(
        "--as-of",
        default=date.today().isoformat(),
        help="Process charges due on or before YYYY-MM-DD.",
    )
    parser.add_argument(
        "--limit-per-organization",
        type=int,
        default=100,
        help="Maximum recurring configurations per organization.",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    load_dotenv_file()
    create_tables()
    results = generate_all_active_organizations(
        as_of=args.as_of,
        dry_run=args.dry_run,
        limit_per_organization=max(1, args.limit_per_organization),
    )
    total = 0
    for result in results:
        organization = result["organization"]
        rows = result["preview"] if args.dry_run else result["generated"]
        if not rows:
            continue
        print(
            f"Organization {organization['id']} "
            f"({organization['name']}):"
        )
        if args.dry_run:
            for item in rows:
                recurring = item["recurring_charge"]
                print(
                    "  - "
                    f"{recurring['agent_name']} · "
                    f"{item['description']} · "
                    f"{item['currency']} {item['gross_amount']:.2f}"
                )
        else:
            for movement in rows:
                print(
                    "  - generated "
                    f"movement={movement['id']} · "
                    f"{movement['description']} · "
                    f"{movement['currency']} "
                    f"{movement['gross_amount']:.2f}"
                )
        total += len(rows)
    action = "would generate" if args.dry_run else "generated"
    print(f"{total} charge(s) {action}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
