"""Persist and resolve the office marketing logo. Not agent contacts."""

from __future__ import annotations

import logging
import mimetypes
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from modules.config import get_private_upload_root
from modules.database.organization_settings_repository import get_organization_settings
from modules.database.tenant import require_organization_id

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
MAX_LOGO_BYTES = 2 * 1024 * 1024
SOURCE_UPLOAD = "upload"
SOURCE_URL = "url"
SOURCE_GENERAL = "general"
SOURCE_NONE = "none"


class MarketingLogoError(Exception):
    def __init__(self, message_key):
        super().__init__(message_key)
        self.message_key = message_key


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _clean(value):
    return " ".join(str(value or "").split())


def marketing_branding_dir(organization_id):
    organization_id = require_organization_id(organization_id)
    return (
        get_private_upload_root()
        / "organizations"
        / str(organization_id)
        / "branding"
    )


def marketing_logo_logical_key(organization_id, extension):
    organization_id = require_organization_id(organization_id)
    ext = str(extension or ".png").lower()
    if ext not in ALLOWED_EXTENSIONS:
        ext = ".png"
    return f"organizations/{organization_id}/branding/marketing_logo{ext}"


def resolve_stored_logo_file(candidate):
    """Resolve a stored logo to a real file. Never treats http(s) as a path."""
    value = _clean(candidate)
    if not value or value.lower().startswith(("http://", "https://", "c:\\fakepath")):
        return None
    raw = Path(value)
    if raw.is_file():
        return raw.resolve()
    private_root = get_private_upload_root().resolve()
    private = (private_root / value).resolve()
    try:
        private.relative_to(private_root)
    except ValueError:
        private = None
    if private and private.is_file():
        return private
    static_root = (BASE_DIR / "static").resolve()
    static_candidate = (static_root / value).resolve()
    try:
        static_candidate.relative_to(static_root)
    except ValueError:
        static_candidate = None
    if static_candidate and static_candidate.is_file():
        return static_candidate
    rooted = (BASE_DIR / value).resolve()
    if rooted.is_file():
        return rooted
    return None


def scan_organization_logo(organization_id):
    if not organization_id:
        return None
    folders = (
        get_private_upload_root() / "organizations" / str(organization_id),
        BASE_DIR / "static" / "uploads" / "organizations" / str(organization_id),
    )
    for folder in folders:
        for name in (
            "logo.png",
            "logo.svg",
            "logo.webp",
            "logo.jpg",
            "logo.jpeg",
            "logo.gif",
        ):
            candidate = folder / name
            if candidate.is_file():
                return candidate.resolve()
    return None


def normalize_marketing_logo_url(raw):
    value = _clean(raw)
    if not value:
        return ""
    folded = value.casefold()
    if "fakepath" in folded or folded.startswith("file:"):
        raise MarketingLogoError("settings_marketing_logo_url_invalid")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MarketingLogoError("settings_marketing_logo_url_invalid")
    return value


def _extension_from_filename(filename):
    name = Path(str(filename or "")).name.lower()
    suffix = Path(name).suffix
    return suffix if suffix in ALLOWED_EXTENSIONS else None


def save_marketing_logo_upload(organization_id, logo_file):
    """Save the marketing logo on the persistent private volume."""
    organization_id = require_organization_id(organization_id)
    if logo_file is None or not getattr(logo_file, "filename", None):
        raise MarketingLogoError("settings_marketing_logo_missing")
    extension = _extension_from_filename(logo_file.filename)
    if extension is None:
        raise MarketingLogoError("settings_logo_invalid_type")
    stream = getattr(logo_file, "stream", None)
    if stream is not None:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(0)
        if size > MAX_LOGO_BYTES:
            raise MarketingLogoError("settings_logo_too_large")
        payload = stream.read()
        stream.seek(0)
    else:
        payload = logo_file.read()
    if not payload:
        raise MarketingLogoError("settings_marketing_logo_missing")
    if len(payload) > MAX_LOGO_BYTES:
        raise MarketingLogoError("settings_logo_too_large")
    folder = marketing_branding_dir(organization_id)
    folder.mkdir(parents=True, exist_ok=True)
    for existing in folder.glob("marketing_logo.*"):
        if existing.is_file():
            existing.unlink()
    logical = marketing_logo_logical_key(organization_id, extension)
    absolute = get_private_upload_root() / logical
    absolute.write_bytes(payload)
    return logical


def delete_marketing_logo_file(organization_id, logical_path=None):
    organization_id = require_organization_id(organization_id)
    folder = marketing_branding_dir(organization_id)
    if folder.is_dir():
        for existing in folder.glob("marketing_logo.*"):
            if existing.is_file():
                existing.unlink()
    resolved = resolve_stored_logo_file(logical_path)
    if resolved and resolved.is_file() and resolved.parent == folder:
        resolved.unlink()


def _materialize_url_logo(organization_id, url):
    folder = marketing_branding_dir(organization_id)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "url_cache"
    try:
        request = Request(url, headers={"User-Agent": "JRH-One-Logo/1.0"})
        with urlopen(request, timeout=8) as response:
            payload = response.read(MAX_LOGO_BYTES + 1)
            content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0]
    except Exception:
        logger.warning(
            "marketing_branding organization_id=%s logo_source=url logo_resolved=false logo_type=url",
            organization_id,
        )
        return None
    if not payload or len(payload) > MAX_LOGO_BYTES:
        return None
    suffix = mimetypes.guess_extension(content_type or "") or Path(urlparse(url).path).suffix
    suffix = (suffix or ".png").lower()
    if suffix == ".jpe":
        suffix = ".jpg"
    if suffix not in ALLOWED_EXTENSIONS:
        suffix = ".png"
    path = target.with_suffix(suffix)
    for stale in folder.glob("url_cache.*"):
        if stale.is_file() and stale != path:
            stale.unlink()
    path.write_bytes(payload)
    return path


def _logo_type(path=None, url=""):
    if path:
        suffix = Path(path).suffix.lower().lstrip(".")
        return suffix or "file"
    if url:
        return "url"
    return "none"


def get_organization_marketing_branding(
    organization,
    *,
    language="es",
    materialize_url=False,
):
    """Single office-branding source for settings, Marketing, and brochures."""
    from modules.marketing_branding import (
        build_legal_footer_line,
        marketing_legal_is_configured,
        office_wordmark,
        usable_brand_name,
    )
    from modules.database.organizations_repository import get_organization_by_id

    if isinstance(organization, dict):
        settings = dict(organization)
        organization_id = settings.get("organization_id")
    else:
        organization_id = organization
        settings = get_organization_settings(organization_id) or {}
    if organization_id not in (None, ""):
        organization_id = require_organization_id(organization_id)
        if not settings.get("organization_id"):
            settings["organization_id"] = organization_id
    org_row = get_organization_by_id(organization_id) if organization_id else {}
    brand_name = usable_brand_name(
        settings.get("marketing_brand_name"),
        settings.get("legal_office_name"),
        settings.get("display_name"),
        (org_row or {}).get("name"),
    )
    stored_path = _clean(settings.get("marketing_logo_path"))
    try:
        stored_url = normalize_marketing_logo_url(settings.get("marketing_logo_url"))
    except MarketingLogoError:
        stored_url = ""
    preferred = _clean(settings.get("marketing_logo_source")).lower()
    upload_file = resolve_stored_logo_file(stored_path)
    url_as_file = resolve_stored_logo_file(settings.get("marketing_logo_url"))
    general_file = resolve_stored_logo_file(settings.get("logo_path")) or scan_organization_logo(
        organization_id
    )

    source = SOURCE_NONE
    logo_path = None
    logo_url = stored_url or None
    if preferred == SOURCE_UPLOAD and upload_file:
        source = SOURCE_UPLOAD
        logo_path = upload_file
    elif preferred == SOURCE_URL and stored_url:
        source = SOURCE_URL
        if materialize_url:
            logo_path = _materialize_url_logo(organization_id, stored_url)
    elif upload_file:
        source = SOURCE_UPLOAD
        logo_path = upload_file
    elif stored_url:
        source = SOURCE_URL
        if materialize_url:
            logo_path = _materialize_url_logo(organization_id, stored_url)
    elif url_as_file:
        source = SOURCE_UPLOAD
        logo_path = url_as_file
    elif general_file:
        source = SOURCE_GENERAL
        logo_path = general_file

    resolved = bool(logo_path and Path(logo_path).is_file()) or (
        source == SOURCE_URL and bool(stored_url)
    )
    logger.info(
        "marketing_branding organization_id=%s logo_source=%s logo_resolved=%s logo_type=%s",
        organization_id,
        source,
        str(resolved).lower(),
        _logo_type(logo_path, stored_url),
    )
    legal_name = _clean(settings.get("legal_broker_name"))
    legal_license = _clean(settings.get("legal_broker_license"))
    return {
        "organization_id": organization_id,
        "brand_name": brand_name,
        "logo_source": source,
        "logo_path": str(logo_path) if logo_path else None,
        "logo_url": stored_url or None,
        "logo_logical_path": stored_path or None,
        "logo_updated_at": _clean(settings.get("marketing_logo_updated_at")) or None,
        "legal_broker_name": legal_name,
        "legal_broker_license": legal_license,
        "legal_footer_line": _clean(settings.get("legal_footer_line"))
        or build_legal_footer_line(settings, language=language),
        "wordmark_text": office_wordmark(
            brand_name, has_logo=bool(logo_path), logo_path=str(logo_path or "")
        ),
        "has_logo": bool(logo_path) or (source == SOURCE_URL and bool(stored_url)),
        "configured": marketing_legal_is_configured(settings),
    }


def apply_marketing_logo_form(organization_id, form, files, current):
    """Apply upload / URL / remove. Returns fields to persist."""
    current = current or {}
    remove = (form.get("remove_marketing_logo") or "") == "yes"
    source_choice = _clean(form.get("marketing_logo_source")).lower()
    upload = files.get("marketing_logo_file") if files is not None else None
    has_upload = bool(upload is not None and getattr(upload, "filename", None))
    raw_url = form.get("marketing_logo_url")
    url_submitted = raw_url is not None
    fields = {
        "marketing_logo_path": current.get("marketing_logo_path") or None,
        "marketing_logo_url": current.get("marketing_logo_url") or None,
        "marketing_logo_source": current.get("marketing_logo_source") or None,
        "marketing_logo_updated_at": current.get("marketing_logo_updated_at") or None,
    }
    if remove:
        delete_marketing_logo_file(organization_id, fields["marketing_logo_path"])
        return {
            "marketing_logo_path": None,
            "marketing_logo_url": None,
            "marketing_logo_source": SOURCE_NONE,
            "marketing_logo_updated_at": _now_iso(),
        }
    if has_upload:
        logical = save_marketing_logo_upload(organization_id, upload)
        fields["marketing_logo_path"] = logical
        fields["marketing_logo_source"] = SOURCE_UPLOAD
        fields["marketing_logo_updated_at"] = _now_iso()
        if url_submitted:
            try:
                fields["marketing_logo_url"] = normalize_marketing_logo_url(raw_url) or None
            except MarketingLogoError:
                fields["marketing_logo_url"] = current.get("marketing_logo_url") or None
        return fields
    url_value = _clean(raw_url) if url_submitted else ""
    url_changed = url_submitted and url_value != _clean(current.get("marketing_logo_url"))
    if source_choice == SOURCE_URL or (url_changed and url_value):
        url = normalize_marketing_logo_url(
            url_value or current.get("marketing_logo_url")
        )
        if not url:
            raise MarketingLogoError("settings_marketing_logo_url_invalid")
        fields["marketing_logo_url"] = url
        fields["marketing_logo_source"] = SOURCE_URL
        fields["marketing_logo_updated_at"] = _now_iso()
        return fields
    if url_submitted and not url_value and fields["marketing_logo_source"] == SOURCE_URL:
        fields["marketing_logo_url"] = None
        if fields["marketing_logo_path"]:
            fields["marketing_logo_source"] = SOURCE_UPLOAD
        else:
            fields["marketing_logo_source"] = SOURCE_NONE
        fields["marketing_logo_updated_at"] = _now_iso()
    return fields
