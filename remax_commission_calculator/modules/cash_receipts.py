"""
Private cash receipt / ticket storage under PRIVATE_UPLOAD_ROOT.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from io import BytesIO
from pathlib import Path

from werkzeug.utils import secure_filename

from modules.config import get_private_upload_root


logger = logging.getLogger(__name__)

MAX_RECEIPT_BYTES = 8 * 1024 * 1024
AI_MAX_EDGE_PX = 1600
AI_JPEG_QUALITY = 85

ALLOWED_EXTENSIONS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

ALLOWED_CONTENT_TYPES = set(ALLOWED_EXTENSIONS.values())

EXTENSION_FOR_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class CashReceiptError(Exception):
    def __init__(self, message_key):
        super().__init__(message_key)
        self.message_key = message_key


SCOPE_CASH = "cash"
SCOPE_AGENT_PAYMENTS = "agent_payments"

ALLOWED_SCOPES = (SCOPE_CASH, SCOPE_AGENT_PAYMENTS)


def _receipts_directory(
    organization_id,
    draft_id=None,
    scope=SCOPE_CASH,
):
    if scope not in ALLOWED_SCOPES:
        raise ValueError("invalid_receipt_scope")

    root = get_private_upload_root()
    path = (
        root
        / "organizations"
        / str(organization_id)
        / scope
        / "receipts"
    )

    if draft_id is not None:
        path = path / str(draft_id)

    return path


def absolute_receipt_path(relative_path, organization_id):
    root = get_private_upload_root().resolve()
    candidate = (root / relative_path).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise PermissionError(
            "Receipt path escapes private upload root."
        ) from error

    org_marker = (
        Path("organizations")
        / str(organization_id)
    ).as_posix()

    if org_marker not in candidate.as_posix().replace(
        "\\",
        "/",
    ):
        raise PermissionError(
            "Receipt path outside organization scope."
        )

    return candidate


def detect_image_content_type(header_bytes):
    if header_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    if header_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    if (
        len(header_bytes) >= 12
        and header_bytes[0:4] == b"RIFF"
        and header_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"

    return None


def inspect_image_bytes(raw_bytes):
    """Return Pillow open diagnostics without mutating bytes."""
    info = {
        "pillow_available": False,
        "pillow_ok": False,
        "width": None,
        "height": None,
        "format": None,
        "mode": None,
    }

    try:
        from PIL import Image
    except ImportError:
        return info

    info["pillow_available"] = True

    try:
        image = Image.open(BytesIO(raw_bytes))
        image.load()
        info["pillow_ok"] = True
        info["width"], info["height"] = image.size
        info["format"] = image.format
        info["mode"] = image.mode
    except Exception as error:
        info["pillow_error"] = type(error).__name__

    return info


def validate_receipt_upload(
    file_storage,
    *,
    require_magic_bytes=False,
):
    """
    ``require_magic_bytes`` refuses files whose content type could
    only be guessed from the filename extension. Cash AI keeps the
    lenient default; flows that must not accept a renamed
    non-image opt in.
    """
    if file_storage is None or not getattr(
        file_storage,
        "filename",
        None,
    ):
        raise CashReceiptError("cash_ai_err_file_required")

    client_filename = file_storage.filename or ""
    original = secure_filename(client_filename)
    extension = Path(original).suffix.lower()
    declared = (file_storage.mimetype or "").lower().strip()

    logger.info(
        "cash_ai stage=receipt_received filename=%r "
        "secure_filename=%r declared_mime=%r",
        client_filename,
        original,
        declared or None,
    )

    raw = file_storage.read()

    try:
        file_storage.stream.seek(0)
    except Exception:
        pass

    if not raw:
        raise CashReceiptError("cash_ai_err_file_empty")

    if len(raw) > MAX_RECEIPT_BYTES:
        raise CashReceiptError("cash_ai_err_file_too_large")

    detected = detect_image_content_type(raw[:32])

    if detected is None and require_magic_bytes:
        logger.warning(
            "cash_ai stage=receipt_validated_failed "
            "reason=no_magic_bytes size=%s ext=%r",
            len(raw),
            extension,
        )
        raise CashReceiptError("cash_ai_err_file_type")

    if detected is None and extension in ALLOWED_EXTENSIONS:
        # Fallback only when magic bytes are unknown.
        detected = ALLOWED_EXTENSIONS[extension]

    if detected not in ALLOWED_CONTENT_TYPES:
        logger.warning(
            "cash_ai stage=receipt_validated_failed "
            "reason=bad_magic size=%s ext=%r declared=%r",
            len(raw),
            extension,
            declared or None,
        )
        raise CashReceiptError("cash_ai_err_file_type")

    # Prefer magic-bytes MIME over browser-declared MIME.
    # Declared types like image/x-png must not block valid PNGs.
    if (
        declared
        and declared not in ALLOWED_CONTENT_TYPES
        and declared != "application/octet-stream"
    ):
        logger.info(
            "cash_ai stage=receipt_validated "
            "ignored_declared_mime=%r using_detected=%r",
            declared,
            detected,
        )

    if extension not in ALLOWED_EXTENSIONS:
        extension = EXTENSION_FOR_MIME[detected]
        if not original:
            original = f"receipt{extension}"
        elif not Path(original).suffix:
            original = f"{original}{extension}"

    if extension == ".jpeg":
        extension = ".jpg"

    digest = hashlib.sha256(raw).hexdigest()
    image_info = inspect_image_bytes(raw)

    logger.info(
        "cash_ai stage=receipt_validated size=%s "
        "detected_mime=%s sha256_prefix=%s pillow_ok=%s "
        "dims=%sx%s format=%s",
        len(raw),
        detected,
        digest[:12],
        image_info.get("pillow_ok"),
        image_info.get("width"),
        image_info.get("height"),
        image_info.get("format"),
    )

    return {
        "bytes": raw,
        "content_type": detected,
        "extension": extension,
        "original_filename": original or f"receipt{extension}",
        "sha256": digest,
        "size": len(raw),
        "image_info": image_info,
    }


def save_receipt_bytes(
    organization_id,
    *,
    payload,
    draft_id=None,
    scope=SCOPE_CASH,
):
    directory = _receipts_directory(
        organization_id,
        draft_id=draft_id,
        scope=scope,
    )
    directory.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}{payload['extension']}"
    absolute = directory / stored_name
    absolute.write_bytes(payload["bytes"])

    relative = absolute.relative_to(
        get_private_upload_root().resolve()
    ).as_posix()

    exists = absolute.is_file()
    size_on_disk = absolute.stat().st_size if exists else 0

    logger.info(
        "cash_ai stage=receipt_saved relative=%s "
        "exists=%s size_on_disk=%s",
        relative,
        exists,
        size_on_disk,
    )

    if not exists or size_on_disk <= 0:
        raise CashReceiptError("cash_ai_err_file_empty")

    return {
        "relative_path": relative,
        "stored_name": stored_name,
        "content_type": payload["content_type"],
        "original_filename": payload["original_filename"],
        "sha256": payload["sha256"],
        "size": payload["size"],
        "absolute_path": str(absolute),
    }


def prepare_image_for_ai(raw_bytes, content_type):
    """
    Optionally downscale large images before sending to the model.
    Original on disk is never mutated.
    Returns (bytes, mime) suitable for data-URL embedding — never a path.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.info(
            "cash_ai stage=receipt_read_for_ai "
            "pillow_missing passthrough_mime=%s size=%s",
            content_type,
            len(raw_bytes),
        )
        return raw_bytes, content_type

    try:
        image = Image.open(BytesIO(raw_bytes))
        image.load()
    except Exception as error:
        logger.warning(
            "cash_ai stage=receipt_read_for_ai "
            "pillow_open_failed error=%s passthrough",
            type(error).__name__,
        )
        return raw_bytes, content_type

    source_format = image.format
    width, height = image.size

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    elif image.mode == "L":
        image = image.convert("RGB")

    longest = max(width, height)

    if longest > AI_MAX_EDGE_PX:
        scale = AI_MAX_EDGE_PX / float(longest)
        image = image.resize(
            (
                max(1, int(width * scale)),
                max(1, int(height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )

    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=AI_JPEG_QUALITY,
        optimize=True,
    )
    prepared = buffer.getvalue()

    logger.info(
        "cash_ai stage=receipt_read_for_ai "
        "source_format=%s source_dims=%sx%s "
        "prepared_mime=image/jpeg prepared_size=%s",
        source_format,
        width,
        height,
        len(prepared),
    )

    return prepared, "image/jpeg"


def delete_receipt_file(relative_path, organization_id):
    if not relative_path:
        return

    path = absolute_receipt_path(
        relative_path,
        organization_id,
    )

    if path.is_file():
        path.unlink()
