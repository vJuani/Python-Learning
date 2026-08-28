"""
Secure certificate / private key loading for ARCA WSAA.

Never log or expose certificate or key contents.
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


def resolve_issuer_secret_ref(issuer_profile: dict) -> str:
    """Build secret ref from profile (agent:5 / issuer:3) or explicit ref."""
    explicit = (issuer_profile or {}).get(
        "arca_certificate_ref"
    ) or ""
    if explicit.strip():
        return explicit.strip()

    issuer_key = (issuer_profile or {}).get("issuer_key")
    if issuer_key:
        return str(issuer_key)

    agent_id = (issuer_profile or {}).get("agent_id")
    if agent_id is not None:
        return f"agent:{agent_id}"

    profile_id = (issuer_profile or {}).get("id")
    if profile_id is not None:
        return f"issuer:{profile_id}"

    raise ValueError("invoice_err_arca_credentials_missing")


def load_credentials(issuer_profile: dict) -> ArcaCredentials:
    """
    Load X.509 certificate and private key for an issuer.

    Resolution order:
    1. ARCA_SECRETS_DIR/{ref}/cert.pem + key.pem
    2. Env ARCA_CERT_{REF}_B64 / ARCA_KEY_{REF}_B64 (Railway-friendly)
    3. Global ARCA_CERT_PEM_B64 / ARCA_KEY_PEM_B64 (single-issuer dev)
    """
    ref = resolve_issuer_secret_ref(issuer_profile)
    safe_ref = ref.replace(":", "_").upper()

    root = _secrets_root()
    if root:
        cert_path = root / ref / "cert.pem"
        key_path = root / ref / "key.pem"
        if cert_path.is_file() and key_path.is_file():
            return ArcaCredentials(
                certificate_pem=cert_path.read_bytes(),
                private_key_pem=key_path.read_bytes(),
                passphrase=os.environ.get(
                    f"ARCA_KEY_PASSPHRASE_{safe_ref}"
                )
                or os.environ.get("ARCA_KEY_PASSPHRASE"),
            )

    cert_b64 = os.environ.get(f"ARCA_CERT_{safe_ref}_B64")
    key_b64 = os.environ.get(f"ARCA_KEY_{safe_ref}_B64")
    if cert_b64 and key_b64:
        return ArcaCredentials(
            certificate_pem=base64.b64decode(cert_b64),
            private_key_pem=base64.b64decode(key_b64),
            passphrase=os.environ.get(
                f"ARCA_KEY_PASSPHRASE_{safe_ref}"
            )
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
