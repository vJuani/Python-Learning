"""Media URL safety. Blocks SSRF, credentials, and private hosts. No fetch here."""

from __future__ import annotations

from urllib.parse import urlparse
import ipaddress


BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
}

ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}

MAX_MEDIA_BYTES = 8 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 8


def is_private_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    )


def is_safe_media_url(url, *, require_https=True):
    raw = str(url or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw)
    if parsed.scheme not in {"https", "http"}:
        return False
    if require_https and parsed.scheme != "https":
        return False
    if parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host in BLOCKED_HOSTS:
        return False
    if host.endswith(".local") or host.endswith(".internal"):
        return False
    if is_private_ip(host):
        return False
    if "token=" in raw.lower() or "signature=" in raw.lower():
        return False
    return True


def assert_safe_media_url(url, *, require_https=True):
    if not is_safe_media_url(url, require_https=require_https):
        raise ValueError("unsafe_media_url")


def is_allowed_image_type(content_type):
    raw = str(content_type or "").split(";", 1)[0].strip().lower()
    return raw in ALLOWED_IMAGE_TYPES
