"""
Private VAT invoice documents for operations.

Files live under PRIVATE_UPLOAD_ROOT (outside static/).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from werkzeug.utils import secure_filename

from modules.config import get_private_upload_root
from modules.database.vat_documents_repository import (
    DOC_TYPE_AGENT_CLIENT,
    DOC_TYPE_MARTILLERO_CLIENT,
    VALID_DOC_TYPES,
    delete_vat_document,
    get_vat_document,
    get_vat_document_by_type,
    list_vat_documents_for_operation,
    upsert_vat_document,
)


MAX_VAT_DOCUMENT_BYTES = 10 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

ALLOWED_CONTENT_TYPES = set(ALLOWED_EXTENSIONS.values())


def is_valid_doc_type(doc_type):
    return doc_type in VALID_DOC_TYPES


def documents_by_type_map(documents):
    return {
        document["doc_type"]: document
        for document in documents
    }


def absolute_document_path(document):
    root = get_private_upload_root()
    directory = (
        root
        / "organizations"
        / str(document["organization_id"])
        / "operations"
        / str(document["operation_id"])
        / "vat-docs"
    )
    candidate = (directory / document["stored_name"]).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise PermissionError(
            "Document path escapes private upload root."
        ) from error

    return candidate


def _detect_content_type(header_bytes, extension):
    if header_bytes.startswith(b"%PDF"):
        return "application/pdf"

    if header_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    if header_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    if (
        len(header_bytes) >= 12
        and header_bytes.startswith(b"RIFF")
        and header_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"

    return ALLOWED_EXTENSIONS.get(extension)


def validate_vat_upload(file_storage):
    if file_storage is None or not file_storage.filename:
        return None, "err_vat_doc_required"

    original = secure_filename(file_storage.filename)

    if original == "":
        return None, "err_vat_doc_invalid_name"

    extension = Path(original).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        return None, "err_vat_doc_invalid_type"

    file_storage.stream.seek(0, 2)
    size_bytes = file_storage.stream.tell()
    file_storage.stream.seek(0)

    if size_bytes <= 0:
        return None, "err_vat_doc_empty"

    if size_bytes > MAX_VAT_DOCUMENT_BYTES:
        return None, "err_vat_doc_too_large"

    header = file_storage.stream.read(16)
    file_storage.stream.seek(0)

    detected = _detect_content_type(header, extension)
    expected = ALLOWED_EXTENSIONS[extension]

    if detected != expected:
        return None, "err_vat_doc_content_mismatch"

    browser_type = (file_storage.mimetype or "").lower()

    if (
        browser_type
        and browser_type not in ALLOWED_CONTENT_TYPES
        and browser_type != "application/octet-stream"
    ):
        return None, "err_vat_doc_invalid_type"

    stored_name = f"{uuid.uuid4().hex}{extension}"

    return {
        "original_filename": original,
        "stored_name": stored_name,
        "content_type": expected,
        "size_bytes": size_bytes,
        "extension": extension,
    }, None


def save_vat_document_file(
    organization_id,
    operation_id,
    stored_name,
    file_storage
):
    root = get_private_upload_root()
    directory = (
        root
        / "organizations"
        / str(organization_id)
        / "operations"
        / str(operation_id)
        / "vat-docs"
    )
    directory.mkdir(parents=True, exist_ok=True)

    absolute_path = (directory / stored_name).resolve()

    try:
        absolute_path.relative_to(root.resolve())
    except ValueError as error:
        raise PermissionError(
            "Refusing to write outside private upload root."
        ) from error

    file_storage.stream.seek(0)
    file_storage.save(str(absolute_path))

    return absolute_path


def delete_stored_file(organization_id, operation_id, stored_name):
    if not stored_name or ".." in stored_name or "/" in stored_name or "\\" in stored_name:
        return

    root = get_private_upload_root()
    path = (
        root
        / "organizations"
        / str(organization_id)
        / "operations"
        / str(operation_id)
        / "vat-docs"
        / stored_name
    ).resolve()

    try:
        path.relative_to(root.resolve())
    except ValueError:
        return

    if not path.is_file():
        return

    try:
        path.unlink()
    except OSError:
        # Best-effort cleanup (e.g. Windows file lock).
        # DB already points at the replacement file.
        pass


def upload_or_replace_vat_document(
    *,
    organization_id,
    operation_id,
    doc_type,
    file_storage,
    uploaded_by_user_id
):
    if not is_valid_doc_type(doc_type):
        return None, "err_vat_doc_type_invalid"

    parsed, error_key = validate_vat_upload(file_storage)

    if error_key is not None:
        return None, error_key

    absolute_path = save_vat_document_file(
        organization_id,
        operation_id,
        parsed["stored_name"],
        file_storage
    )

    try:
        document, previous_stored_name = upsert_vat_document(
            organization_id,
            operation_id,
            doc_type,
            parsed["stored_name"],
            parsed["original_filename"],
            parsed["content_type"],
            parsed["size_bytes"],
            uploaded_by_user_id
        )
    except Exception:
        if absolute_path.is_file():
            absolute_path.unlink()
        raise

    if (
        previous_stored_name
        and previous_stored_name != parsed["stored_name"]
    ):
        delete_stored_file(
            organization_id,
            operation_id,
            previous_stored_name
        )

    return document, None


def remove_vat_document(document_id, organization_id):
    document = delete_vat_document(
        document_id,
        organization_id
    )

    if document is None:
        return None

    delete_stored_file(
        document["organization_id"],
        document["operation_id"],
        document["stored_name"]
    )

    return document


__all__ = [
    "DOC_TYPE_AGENT_CLIENT",
    "DOC_TYPE_MARTILLERO_CLIENT",
    "VALID_DOC_TYPES",
    "absolute_document_path",
    "documents_by_type_map",
    "get_vat_document",
    "get_vat_document_by_type",
    "is_valid_doc_type",
    "list_vat_documents_for_operation",
    "remove_vat_document",
    "upload_or_replace_vat_document",
]
