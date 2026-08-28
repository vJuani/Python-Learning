#!/usr/bin/env python3
"""
Manual ARCA homologation smoke test (WSAA auth + last voucher).

Requires:
  INVOICE_PROVIDER=arca
  ARCA_ENV=homologation
  ARCA_SECRETS_DIR=/path/to/secrets  (with {ref}/cert.pem + key.pem)
    OR ARCA_CERT_{REF}_B64 + ARCA_KEY_{REF}_B64

Usage:
  python scripts/manual_arca_homologation.py \\
    --cuit 20-30000000-3 \\
    --ref agent:1 \\
    --point-of-sale 5 \\
    --voucher-type 11
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("INVOICE_PROVIDER", "arca")
os.environ.setdefault("ARCA_ENV", "homologation")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ARCA homologation manual smoke test"
    )
    parser.add_argument("--cuit", required=True)
    parser.add_argument(
        "--ref",
        required=True,
        help="Certificate ref (e.g. agent:5 or issuer:3)",
    )
    parser.add_argument(
        "--point-of-sale",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--voucher-type",
        type=int,
        default=11,
        help="AFIP voucher type (11=Factura C, etc.)",
    )
    args = parser.parse_args()

    from modules.arca.client import ArcaClient
    from modules.arca.config import get_arca_environment
    from modules.arca.wsfev1 import get_last_authorized_voucher

    profile = {
        "tax_id": args.cuit,
        "arca_connection_status": "connected",
        "arca_point_of_sale": str(args.point_of_sale),
        "arca_certificate_ref": args.ref,
        "issuer_key": args.ref,
    }

    env = get_arca_environment()
    print(f"Environment: {env}")
    print("Step 1: WSAA authentication...")
    client = ArcaClient()
    ticket = client.authenticate(
        profile,
        {"issuer_tax_id": args.cuit},
    )
    print(f"  OK — token expires {ticket.expires_at.isoformat()}")

    cuit_digits = "".join(
        ch for ch in args.cuit if ch.isdigit()
    )
    print("Step 2: FECompUltimoAutorizado...")
    last = get_last_authorized_voucher(
        ticket=ticket,
        cuit=cuit_digits,
        point_of_sale=args.point_of_sale,
        voucher_type=args.voucher_type,
    )
    print(f"  Last authorized voucher: {last}")
    print(f"  Next would be: {last + 1}")
    print("Done. Use the app UI to issue a test invoice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
