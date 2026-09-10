"""On-demand remote image fetch for PDF only. Not a gallery downloader."""

from __future__ import annotations

import logging
from urllib.request import Request, urlopen

from modules.property_sync.redremax.photos import is_allowed_redremax_photo_url
from modules.property_sync.security import (
    FETCH_TIMEOUT_SECONDS,
    MAX_MEDIA_BYTES,
    is_allowed_image_type,
)

logger = logging.getLogger(__name__)


def fetch_allowed_image_bytes(url, *, cache=None):
    """Fetch one allowlisted HTTPS image. Failures return None."""
    raw = str(url or "").strip()
    if not raw:
        return None
    if cache is not None and raw in cache:
        return cache[raw]
    if not is_allowed_redremax_photo_url(raw):
        if cache is not None:
            cache[raw] = None
        return None
    payload = None
    try:
        request = Request(raw, method="GET", headers={"Accept": "image/*"})
        with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            content_type = str(response.headers.get("Content-Type") or "")
            if not is_allowed_image_type(content_type):
                payload = None
            else:
                payload = response.read(MAX_MEDIA_BYTES + 1)
                if payload and len(payload) > MAX_MEDIA_BYTES:
                    payload = None
    except Exception:
        logger.info("remote image fetch skipped url_host_allowed=True")
        payload = None
    if cache is not None:
        cache[raw] = payload
    return payload
