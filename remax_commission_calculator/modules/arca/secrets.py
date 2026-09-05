"""
Secure certificate / private key loading.

User issuance never reads these helpers. Isolated loaders exist only
for local scripts and explicit developer tests.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArcaCredentials:
    certificate_pem: bytes
    private_key_pem: bytes
    passphrase: str | None = None


def _secrets_root() -> Path:
    root = (
        os.environ.get("ARCA_SECRETS_DIR")
        or os.environ.get("ARCA_CERTS_DIR")
        or ""
    ).strip()
    if not root:
        return Path()
    return Path(root)


def load_isolated_dev_credentials(*, ref: str = "") -> ArcaCredentials:
    """Explicit script/test helper. Never called from user issue paths."""
    safe_ref = (ref or "").replace(":", "_").upper()
    root = _secrets_root()
    if root and ref:
        cert_path = root / ref / "cert.pem"
        key_path = root / ref / "key.pem"
        if cert_path.is_file() and key_path.is_file():
            return ArcaCredentials(
                certificate_pem=cert_path.read_bytes(),
                private_key_pem=key_path.read_bytes(),
                passphrase=os.environ.get(f"ARCA_KEY_PASSPHRASE_{safe_ref}")
                or os.environ.get("ARCA_KEY_PASSPHRASE"),
            )

    if safe_ref:
        cert_b64 = os.environ.get(f"ARCA_CERT_{safe_ref}_B64")
        key_b64 = os.environ.get(f"ARCA_KEY_{safe_ref}_B64")
        if cert_b64 and key_b64:
            return ArcaCredentials(
                certificate_pem=base64.b64decode(cert_b64),
                private_key_pem=base64.b64decode(key_b64),
                passphrase=os.environ.get(f"ARCA_KEY_PASSPHRASE_{safe_ref}")
                or os.environ.get("ARCA_KEY_PASSPHRASE"),
            )

    cert_b64 = os.environ.get("ARCA_CERT_PEM_B64")
    key_b64 = os.environ.get("ARCA_KEY_PEM_B64")
    if cert_b64 and key_b64:
        return ArcaCredentials(
            certificate_pem=base64.b64decode(cert_b64),
            private_key_pem=base64.b64decode(key_b64),
            passphrase=os.environ.get("ARCA_KEY_PASSPHRASE"),
        )

    raise FileNotFoundError("invoice_err_arca_credentials_missing")
