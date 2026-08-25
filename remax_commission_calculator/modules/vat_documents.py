"""
Compatibility shim. Prefer modules.operation_documents.
"""

from modules.operation_documents import *  # noqa: F401,F403
from modules.operation_documents import (
    absolute_document_path,
    documents_by_type_map,
    get_vat_document,
    is_valid_doc_type,
    list_vat_documents_for_operation,
    remove_vat_document,
    upload_or_replace_vat_document,
    validate_vat_upload,
)
from modules.database.operation_documents_repository import (
    DOC_TYPE_AGENT_CLIENT,
    DOC_TYPE_MARTILLERO_CLIENT,
    VALID_DOC_TYPES,
)
