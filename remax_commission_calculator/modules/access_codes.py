import hashlib
import secrets


REGISTRATION_CODE_BYTES = 6
GUEST_TOKEN_BYTES = 24
EMAIL_TOKEN_BYTES = 32
EMAIL_CODE_DIGITS = 6


def generate_registration_code():
    return secrets.token_hex(
        REGISTRATION_CODE_BYTES
    ).upper()


def generate_guest_token():
    return secrets.token_urlsafe(
        GUEST_TOKEN_BYTES
    )


def generate_email_token():
    return secrets.token_urlsafe(
        EMAIL_TOKEN_BYTES
    )


def generate_email_verification_code():
    max_value = 10 ** EMAIL_CODE_DIGITS
    return f"{secrets.randbelow(max_value):0{EMAIL_CODE_DIGITS}d}"


def hash_access_secret(value):
    cleaned = (value or "").strip()

    if cleaned == "":
        return None

    digest = hashlib.sha256()
    digest.update(cleaned.encode("utf-8"))

    return digest.hexdigest()
