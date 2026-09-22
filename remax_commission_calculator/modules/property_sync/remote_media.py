"""On-demand remote image fetch for PDF/renderer. Not a gallery downloader."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.request import Request, urlopen

from modules.property_sync.redremax.photos import is_allowed_redremax_photo_url
from modules.property_sync.security import (
    FETCH_TIMEOUT_SECONDS,
    MAX_MEDIA_BYTES,
    is_allowed_image_type,
)

logger = logging.getLogger(__name__)


def _disk_cache_path(cache_dir, url):
    digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:24]
    return Path(cache_dir) / f"{digest}.bin"


def fetch_allowed_image_bytes(url, *, cache=None, cache_dir=None):
    """Fetch one allowlisted HTTPS image. Failures return None."""
    raw = str(url or "").strip()
    if not raw:
        return None
    if cache is not None and raw in cache:
        return cache[raw]
    disk_path = None
    if cache_dir:
        folder = Path(cache_dir)
        folder.mkdir(parents=True, exist_ok=True)
        disk_path = _disk_cache_path(folder, raw)
        if disk_path.is_file():
            payload = disk_path.read_bytes()
            if cache is not None:
                cache[raw] = payload
            return payload
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
    if payload and disk_path is not None:
        disk_path.write_bytes(payload)
    if cache is not None:
        cache[raw] = payload
    return payload
