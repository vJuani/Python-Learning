"""
Private cash receipt / ticket storage under PRIVATE_UPLOAD_ROOT.
"""

from __future__ import annotations

import hashlib
import uuid
from io import BytesIO
from pathlib import Path

from werkzeug.utils import secure_filename

from modules.config import get_private_upload_root


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


class CashReceiptError(Exception):
    def __init__(self, message_key):
        super().__init__(message_key)
        self.message_key = message_key


def _receipts_directory(organization_id, draft_id=None):
    root = get_private_upload_root()
    path = (
        root
        / "organizations"
        / str(organization_id)
        / "cash"
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


def _detect_content_type(header_bytes, extension):
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

    return ALLOWED_EXTENSIONS.get(extension)


def validate_receipt_upload(file_storage):
    if file_storage is None or not getattr(
        file_storage,
        "filename",
        None,
    ):
        raise CashReceiptError("cash_ai_err_file_required")

    original = secure_filename(file_storage.filename or "")
    extension = Path(original).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise CashReceiptError("cash_ai_err_file_type")

    raw = file_storage.read()
    file_storage.stream.seek(0)

    if not raw:
        raise CashReceiptError("cash_ai_err_file_empty")

    if len(raw) > MAX_RECEIPT_BYTES:
        raise CashReceiptError("cash_ai_err_file_too_large")

    detected = _detect_content_type(raw[:32], extension)

    if detected not in ALLOWED_CONTENT_TYPES:
        raise CashReceiptError("cash_ai_err_file_type")

    declared = (file_storage.mimetype or "").lower()

    if (
        declared
        and declared not in ALLOWED_CONTENT_TYPES
        and declared != "application/octet-stream"
    ):
        raise CashReceiptError("cash_ai_err_file_type")

    digest = hashlib.sha256(raw).hexdigest()

    return {
        "bytes": raw,
        "content_type": detected,
        "extension": extension if extension != ".jpeg" else ".jpg",
        "original_filename": original or f"receipt{extension}",
        "sha256": digest,
        "size": len(raw),
    }


def save_receipt_bytes(
    organization_id,
    *,
    payload,
    draft_id=None,
):
    directory = _receipts_directory(
        organization_id,
        draft_id=draft_id,
    )
    directory.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}{payload['extension']}"
    absolute = directory / stored_name
    absolute.write_bytes(payload["bytes"])

    relative = absolute.relative_to(
        get_private_upload_root().resolve()
    ).as_posix()

    return {
        "relative_path": relative,
        "stored_name": stored_name,
        "content_type": payload["content_type"],
        "original_filename": payload["original_filename"],
        "sha256": payload["sha256"],
        "size": payload["size"],
    }


def prepare_image_for_ai(raw_bytes, content_type):
    """
    Optionally downscale large images before sending to the model.
    Original on disk is never mutated.
    """
    try:
        from PIL import Image
    except ImportError:
        return raw_bytes, content_type

    try:
        image = Image.open(BytesIO(raw_bytes))
        image.load()
    except Exception:
        return raw_bytes, content_type

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    elif image.mode == "L":
        image = image.convert("RGB")

    width, height = image.size
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
    return buffer.getvalue(), "image/jpeg"


def delete_receipt_file(relative_path, organization_id):
    if not relative_path:
        return

    path = absolute_receipt_path(
        relative_path,
        organization_id,
    )

    if path.is_file():
        path.unlink()
