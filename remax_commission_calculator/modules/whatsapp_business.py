"""Per-office WhatsApp Business document delivery.

Web Share cannot send a PDF and a caption together. A later integration
will send both through each office's own WhatsApp Business number.
This module does not store credentials and does not call the Cloud API.
"""

from __future__ import annotations

from modules.database.tenant import require_organization_id


class WhatsAppBusinessUnavailable(Exception):
    """The office has not connected its own WhatsApp Business number."""

    def __init__(self, organization_id):
        self.organization_id = organization_id
        super().__init__("whatsapp_business_not_connected")


def can_send_document(organization_id):
    """True only after this office connects WhatsApp Business."""
    require_organization_id(organization_id)
    return False


def send_document(
    organization_id,
    *,
    to_phone,
    pdf_bytes,
    filename,
    caption,
):
    """Send one PDF and its caption from this office's number.

    There is no platform-wide sender. The call is refused until that
    office has its own WhatsApp Business connection.
    """
    organization_id = require_organization_id(organization_id)
    del to_phone, pdf_bytes, filename, caption
    raise WhatsAppBusinessUnavailable(organization_id)
