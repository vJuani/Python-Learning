"""
Private administrative documents for operations.

Files live under PRIVATE_UPLOAD_ROOT (outside static/).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from werkzeug.utils import secure_filename

from modules.config import get_private_upload_root
from modules.database.operation_documents_repository import (
    DOC_CATEGORIES,
    DOC_TYPE_LABEL_KEYS,
    DOC_TYPE_OTHER,
    STRUCTURED_DOC_TYPES,
    VALID_DOC_TYPES,
    allows_multiple_documents,
    delete_operation_document,
    get_operation_document,
    get_operation_document_by_type,
    list_operation_documents,
    upsert_operation_document,
)


MAX_DOCUMENT_BYTES = 10 * 1024 * 1024

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


def _documents_directory(organization_id, operation_id):
    root = get_private_upload_root()
    return (
        root
        / "organizations"
        / str(organization_id)
        / "operations"
        / str(operation_id)
        / "documents"
    )


def _legacy_documents_directory(organization_id, operation_id):
    root = get_private_upload_root()
    return (
        root
        / "organizations"
        / str(organization_id)
        / "operations"
        / str(operation_id)
        / "vat-docs"
    )


def absolute_document_path(document):
    root = get_private_upload_root().resolve()
    modern = (
        _documents_directory(
            document["organization_id"],
            document["operation_id"],
        )
        / document["stored_name"]
    ).resolve()

    try:
        modern.relative_to(root)
    except ValueError as error:
        raise PermissionError(
            "Document path escapes private upload root."
        ) from error

    if modern.is_file():
        return modern

    legacy = (
        _legacy_documents_directory(
            document["organization_id"],
            document["operation_id"],
        )
        / document["stored_name"]
    ).resolve()

    try:
        legacy.relative_to(root)
    except ValueError as error:
        raise PermissionError(
            "Document path escapes private upload root."
        ) from error

    return legacy


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


def validate_document_upload(file_storage):
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

    if size_bytes > MAX_DOCUMENT_BYTES:
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


def save_document_file(
    organization_id,
    operation_id,
    stored_name,
    file_storage,
):
    root = get_private_upload_root().resolve()
    directory = _documents_directory(
        organization_id,
        operation_id,
    )
    directory.mkdir(parents=True, exist_ok=True)
    absolute_path = (directory / stored_name).resolve()

    try:
        absolute_path.relative_to(root)
    except ValueError as error:
        raise PermissionError(
            "Refusing to write outside private upload root."
        ) from error

    file_storage.stream.seek(0)
    file_storage.save(str(absolute_path))

    return absolute_path


def delete_stored_file(organization_id, operation_id, stored_name):
    if (
        not stored_name
        or ".." in stored_name
        or "/" in stored_name
        or "\\" in stored_name
    ):
        return

    root = get_private_upload_root().resolve()

    for directory in (
        _documents_directory(organization_id, operation_id),
        _legacy_documents_directory(
            organization_id,
            operation_id,
        ),
    ):
        path = (directory / stored_name).resolve()

        try:
            path.relative_to(root)
        except ValueError:
            continue

        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass


def upload_or_replace_operation_document(
    *,
    organization_id,
    operation_id,
    doc_type,
    file_storage,
    uploaded_by_user_id,
):
    if not is_valid_doc_type(doc_type):
        return None, "err_vat_doc_type_invalid"

    parsed, error_key = validate_document_upload(file_storage)

    if error_key is not None:
        return None, error_key

    absolute_path = save_document_file(
        organization_id,
        operation_id,
        parsed["stored_name"],
        file_storage,
    )

    try:
        document, previous_stored_name = upsert_operation_document(
            organization_id,
            operation_id,
            doc_type,
            parsed["stored_name"],
            parsed["original_filename"],
            parsed["content_type"],
            parsed["size_bytes"],
            uploaded_by_user_id,
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
            previous_stored_name,
        )

    return document, None


def remove_operation_document(document_id, organization_id):
    document = delete_operation_document(
        document_id,
        organization_id,
    )

    if document is None:
        return None

    delete_stored_file(
        document["organization_id"],
        document["operation_id"],
        document["stored_name"],
    )

    return document


def group_documents_for_ui(documents):
    by_type = {}

    for document in documents:
        by_type.setdefault(document["doc_type"], []).append(
            document
        )

    grouped = []

    for category in DOC_CATEGORIES:
        slots = []

        for doc_type in category["doc_types"]:
            items = by_type.get(doc_type, [])
            slots.append(
                {
                    "doc_type": doc_type,
                    "label_key": DOC_TYPE_LABEL_KEYS[doc_type],
                    "allows_multiple": allows_multiple_documents(
                        doc_type
                    ),
                    "documents": items,
                    "document": (
                        items[0]
                        if items and not allows_multiple_documents(
                            doc_type
                        )
                        else None
                    ),
                }
            )

        grouped.append(
            {
                "key": category["key"],
                "label_key": category["label_key"],
                "slots": slots,
            }
        )

    return grouped


# Transition aliases for older imports.
MAX_VAT_DOCUMENT_BYTES = MAX_DOCUMENT_BYTES
validate_vat_upload = validate_document_upload
upload_or_replace_vat_document = upload_or_replace_operation_document
remove_vat_document = remove_operation_document
get_vat_document = get_operation_document
list_vat_documents_for_operation = list_operation_documents
documents_by_type_map = lambda documents: {
    document["doc_type"]: document
    for document in documents
    if not allows_multiple_documents(document["doc_type"])
}


__all__ = [
    "DOC_CATEGORIES",
    "DOC_TYPE_LABEL_KEYS",
    "DOC_TYPE_OTHER",
    "STRUCTURED_DOC_TYPES",
    "VALID_DOC_TYPES",
    "absolute_document_path",
    "allows_multiple_documents",
    "get_operation_document",
    "group_documents_for_ui",
    "is_valid_doc_type",
    "list_operation_documents",
    "remove_operation_document",
    "upload_or_replace_operation_document",
    "validate_document_upload",
]
