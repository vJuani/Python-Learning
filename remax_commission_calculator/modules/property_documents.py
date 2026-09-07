"""
Private property documentation: upload, normalize, archive and download.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from werkzeug.utils import secure_filename

from modules.config import get_private_upload_root
from modules.database.property_documents_repository import (
    DOCUMENT_TYPE_LABEL_KEYS,
    DOCUMENT_TYPE_OTHER,
    DOCUMENT_TYPES,
    STATUS_ACTIVE,
    STATUS_ERROR,
    add_property_document_file,
    archive_property_document as archive_document_row,
    create_property_document,
    get_property_document,
    get_property_document_file,
    list_property_document_files,
    list_property_documents,
    update_property_document_fields,
)
from modules.database.properties_repository import get_property_record
from modules.pdf_document_normalize import (
    DocumentNormalizeError,
    normalize_document_to_pdf,
)
from modules.property_media_access import (
    PropertyMediaError,
    require_property_media_access,
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
IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}


def is_valid_property_doc_type(doc_type):
    return doc_type in DOCUMENT_TYPES


def _documents_directory(organization_id, property_id, document_id):
    root = get_private_upload_root()
    return (
        root
        / "organizations"
        / str(organization_id)
        / "properties"
        / str(property_id)
        / "documents"
        / str(document_id)
    )


def _resolve_under_private_root(path):
    root = get_private_upload_root().resolve()
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise PermissionError(
            "Document path escapes private upload root."
        ) from error
    return resolved


def absolute_property_file_path(file_row, document):
    directory = _documents_directory(
        document["organization_id"],
        document["property_id"],
        document["id"],
    )
    return _resolve_under_private_root(directory / file_row["stored_name"])


def absolute_normalized_pdf_path(document):
    stored_name = document.get("normalized_pdf_stored_name")
    if not stored_name:
        return None
    directory = _documents_directory(
        document["organization_id"],
        document["property_id"],
        document["id"],
    )
    return _resolve_under_private_root(directory / stored_name)


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


def validate_property_document_upload(file_storage):
    if file_storage is None or not file_storage.filename:
        return None, "property_doc_err_required"

    original = secure_filename(file_storage.filename)
    if original == "":
        return None, "property_doc_err_invalid_name"

    extension = Path(original).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        return None, "property_doc_err_invalid_type"

    file_storage.stream.seek(0, 2)
    size_bytes = file_storage.stream.tell()
    file_storage.stream.seek(0)

    if size_bytes <= 0:
        return None, "property_doc_err_empty"

    if size_bytes > MAX_DOCUMENT_BYTES:
        return None, "property_doc_err_too_large"

    header = file_storage.stream.read(16)
    file_storage.stream.seek(0)
    detected = _detect_content_type(header, extension)
    expected = ALLOWED_EXTENSIONS[extension]

    if detected != expected:
        return None, "property_doc_err_content_mismatch"

    browser_type = (file_storage.mimetype or "").lower()
    if (
        browser_type
        and browser_type not in ALLOWED_CONTENT_TYPES
        and browser_type != "application/octet-stream"
    ):
        return None, "property_doc_err_invalid_type"

    return {
        "original_filename": original,
        "stored_name": f"{uuid.uuid4().hex}{extension}",
        "content_type": expected,
        "size_bytes": size_bytes,
        "extension": extension,
    }, None


def _load_scoped_property(
    property_id,
    organization_id,
    requesting_user,
    *,
    is_guest=False,
    write=False,
):
    if not property_id:
        raise PropertyMediaError("property_brochure_not_found", 404)

    property_data = get_property_record(property_id, organization_id)
    require_property_media_access(
        requesting_user,
        property_data,
        is_guest=is_guest,
        write=write,
    )
    return property_data


def _save_file_bytes(directory, stored_name, file_storage):
    directory.mkdir(parents=True, exist_ok=True)
    target = _resolve_under_private_root(directory / stored_name)
    file_storage.stream.seek(0)
    target.write_bytes(file_storage.stream.read())
    file_storage.stream.seek(0)
    return target


def create_property_document_with_files(
    *,
    property_id,
    organization_id,
    requesting_user,
    document_type,
    title,
    description=None,
    file_storages=None,
    is_guest=False,
):
    _load_scoped_property(
        property_id,
        organization_id,
        requesting_user,
        is_guest=is_guest,
        write=True,
    )

    cleaned_type = (document_type or "").strip() or DOCUMENT_TYPE_OTHER
    if not is_valid_property_doc_type(cleaned_type):
        cleaned_type = DOCUMENT_TYPE_OTHER

    cleaned_title = (title or "").strip()
    if not cleaned_title:
        return None, "property_doc_err_title_required"

    cleaned_description = (description or "").strip() or None
    incoming = [item for item in (file_storages or []) if item is not None]
    if not incoming:
        return None, "property_doc_err_required"

    validated = []
    for file_storage in incoming:
        payload, error_key = validate_property_document_upload(file_storage)
        if error_key:
            return None, error_key
        validated.append((file_storage, payload))

    mime_types = {item[1]["content_type"] for item in validated}
    has_pdf = "application/pdf" in mime_types
    has_image = bool(mime_types & IMAGE_CONTENT_TYPES)
    if has_pdf and has_image:
        return None, "property_doc_err_mixed_types"

    document = create_property_document(
        organization_id=organization_id,
        property_id=property_id,
        document_type=cleaned_type,
        title=cleaned_title,
        description=cleaned_description,
        created_by_user_id=requesting_user["id"],
    )
    directory = _documents_directory(
        organization_id,
        property_id,
        document["id"],
    )

    saved_files = []
    try:
        for index, (file_storage, payload) in enumerate(validated):
            _save_file_bytes(directory, payload["stored_name"], file_storage)
            saved_files.append(
                add_property_document_file(
                    organization_id=organization_id,
                    document_id=document["id"],
                    original_filename=payload["original_filename"],
                    stored_name=payload["stored_name"],
                    mime_type=payload["content_type"],
                    file_size=payload["size_bytes"],
                    sort_order=index,
                    is_original=True,
                )
            )
    except OSError:
        update_property_document_fields(
            document["id"],
            organization_id,
            status=STATUS_ERROR,
            normalize_error="property_doc_err_save",
        )
        return get_document_bundle(document["id"], organization_id), (
            "property_doc_err_save"
        )

    document = _apply_normalization(document, saved_files)
    return get_document_bundle(document["id"], organization_id), None


def _apply_normalization(document, files):
    originals = [item for item in files if item.get("is_original")]
    image_files = [
        item for item in originals if item["mime_type"] in IMAGE_CONTENT_TYPES
    ]
    pdf_files = [
        item for item in originals if item["mime_type"] == "application/pdf"
    ]

    if pdf_files and not image_files:
        return document

    if not image_files:
        return document

    stored_name = f"{uuid.uuid4().hex}.pdf"
    output = _documents_directory(
        document["organization_id"],
        document["property_id"],
        document["id"],
    ) / stored_name
    image_paths = [
        absolute_property_file_path(item, document) for item in image_files
    ]

    try:
        result = normalize_document_to_pdf(image_paths, output)
    except DocumentNormalizeError as error:
        return update_property_document_fields(
            document["id"],
            document["organization_id"],
            status=STATUS_ERROR,
            normalize_error=error.message_key,
        )

    return update_property_document_fields(
        document["id"],
        document["organization_id"],
        status=STATUS_ACTIVE,
        normalized_pdf_stored_name=stored_name,
        normalized_pdf_size=result["size_bytes"],
        normalize_error=None,
    )


def get_document_bundle(document_id, organization_id):
    document = get_property_document(document_id, organization_id)
    if document is None:
        return None
    files = list_property_document_files(document_id, organization_id)
    return {
        **document,
        "files": files,
        "has_normalized_pdf": bool(document.get("normalized_pdf_stored_name")),
        "original_count": len([item for item in files if item.get("is_original")]),
    }


def list_documents_for_property(
    property_id,
    organization_id,
    requesting_user,
    *,
    is_guest=False,
    include_archived=False,
):
    _load_scoped_property(
        property_id,
        organization_id,
        requesting_user,
        is_guest=is_guest,
    )
    statuses = None if include_archived else (STATUS_ACTIVE, STATUS_ERROR)
    documents = list_property_documents(
        organization_id,
        property_id,
        statuses=statuses,
    )
    return [
        get_document_bundle(item["id"], organization_id)
        for item in documents
    ]


def archive_property_document_safe(
    *,
    document_id,
    organization_id,
    requesting_user,
    is_guest=False,
):
    document = get_property_document(document_id, organization_id)
    if document is None:
        raise PropertyMediaError("property_doc_err_not_found", 404)

    _load_scoped_property(
        document["property_id"],
        organization_id,
        requesting_user,
        is_guest=is_guest,
        write=True,
    )
    archived = archive_document_row(
        document_id,
        organization_id,
        requesting_user["id"],
    )
    return get_document_bundle(archived["id"], organization_id)


def load_downloadable_normalized_pdf(
    *,
    document_id,
    organization_id,
    requesting_user,
    is_guest=False,
):
    document = get_property_document(document_id, organization_id)
    if document is None:
        raise PropertyMediaError("property_doc_err_not_found", 404)
    if document.get("status") == STATUS_ARCHIVED:
        raise PropertyMediaError("property_doc_err_archived", 404)

    _load_scoped_property(
        document["property_id"],
        organization_id,
        requesting_user,
        is_guest=is_guest,
    )

    normalized = absolute_normalized_pdf_path(document)
    if normalized is not None and normalized.is_file():
        return {
            "path": normalized,
            "mimetype": "application/pdf",
            "download_name": _safe_download_name(document["title"], ".pdf"),
        }

    files = list_property_document_files(document_id, organization_id)
    pdf_files = [
        item for item in files if item["mime_type"] == "application/pdf"
    ]
    if len(pdf_files) == 1:
        path = absolute_property_file_path(pdf_files[0], document)
        if path.is_file():
            return {
                "path": path,
                "mimetype": "application/pdf",
                "download_name": pdf_files[0]["original_filename"],
            }

    raise PropertyMediaError("property_doc_err_pdf_missing", 404)


def load_downloadable_original_file(
    *,
    file_id,
    organization_id,
    requesting_user,
    is_guest=False,
):
    file_row = get_property_document_file(file_id, organization_id)
    if file_row is None:
        raise PropertyMediaError("property_doc_err_not_found", 404)

    document = get_property_document(file_row["document_id"], organization_id)
    if document is None:
        raise PropertyMediaError("property_doc_err_not_found", 404)
    if document.get("status") == STATUS_ARCHIVED:
        raise PropertyMediaError("property_doc_err_archived", 404)

    _load_scoped_property(
        document["property_id"],
        organization_id,
        requesting_user,
        is_guest=is_guest,
    )

    path = absolute_property_file_path(file_row, document)
    if not path.is_file():
        raise PropertyMediaError("property_doc_err_not_found", 404)

    return {
        "path": path,
        "mimetype": file_row["mime_type"],
        "download_name": file_row["original_filename"],
        "document": document,
        "file": file_row,
    }


def _safe_download_name(title, suffix):
    cleaned = secure_filename(title or "documento") or "documento"
    if not cleaned.lower().endswith(suffix):
        cleaned = f"{cleaned}{suffix}"
    return cleaned


def document_type_choices():
    return [
        {
            "value": key,
            "label_key": DOCUMENT_TYPE_LABEL_KEYS[key],
        }
        for key in DOCUMENT_TYPES
    ]
