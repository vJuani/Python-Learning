"""
Invoice provider abstraction (internal today, ARCA later).
"""

from __future__ import annotations

import io
import os
from abc import ABC, abstractmethod

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


PROVIDER_INTERNAL = "internal"
PROVIDER_ARCA = "arca"


def get_invoice_provider_name():
    name = (
        os.environ.get("INVOICE_PROVIDER")
        or PROVIDER_INTERNAL
    ).strip().lower()
    if name not in (PROVIDER_INTERNAL, PROVIDER_ARCA):
        return PROVIDER_INTERNAL
    return name


class InvoiceProvider(ABC):
    @abstractmethod
    def can_issue_fiscal(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def issue_invoice(self, invoice: dict):
        raise NotImplementedError

    @abstractmethod
    def generate_draft_pdf(self, invoice: dict) -> bytes:
        raise NotImplementedError


class InternalInvoiceProvider(InvoiceProvider):
    def can_issue_fiscal(self) -> bool:
        return False

    def issue_invoice(self, invoice: dict):
        raise RuntimeError(
            "invoice_err_fiscal_issue_unavailable"
        )

    def generate_draft_pdf(self, invoice: dict) -> bytes:
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(
            20 * mm,
            height - 25 * mm,
            "BORRADOR — NO FISCAL",
        )
        pdf.setFont("Helvetica", 10)
        pdf.drawString(
            20 * mm,
            height - 32 * mm,
            "DRAFT — NON-FISCAL / DOCUMENTO INTERNO",
        )

        y = height - 50 * mm
        lines = [
            (
                "Internal ID",
                invoice.get("invoice_number_internal")
                or "",
            ),
            ("Status", invoice.get("status") or ""),
            ("Issuer", invoice.get("issuer_name") or ""),
            (
                "Issuer tax ID",
                invoice.get("issuer_tax_id") or "",
            ),
            (
                "Recipient",
                invoice.get("recipient_name") or "",
            ),
            (
                "Recipient tax ID",
                invoice.get("recipient_tax_id") or "",
            ),
            (
                "Description",
                invoice.get("description") or "",
            ),
            (
                "Service",
                invoice.get("service_type") or "",
            ),
            (
                "Quantity",
                str(invoice.get("quantity") or 1),
            ),
            (
                "Amount",
                (
                    f"{invoice.get('currency', 'ARS')} "
                    f"{float(invoice.get('total_amount') or 0):,.2f}"
                ),
            ),
            (
                "Payment",
                invoice.get("payment_condition") or "",
            ),
            ("Date", invoice.get("issue_date") or ""),
            ("Provider", "internal"),
        ]

        pdf.setFont("Helvetica", 11)
        for label, value in lines:
            pdf.drawString(20 * mm, y, f"{label}: {value}")
            y -= 8 * mm
            if y < 30 * mm:
                pdf.showPage()
                y = height - 25 * mm

        pdf.setFont("Helvetica-Oblique", 9)
        pdf.drawString(
            20 * mm,
            20 * mm,
            "This document is not a fiscal invoice and has no CAE.",
        )
        pdf.save()
        return buffer.getvalue()


class ArcaInvoiceProvider(InvoiceProvider):
    """Placeholder for Facturador v2 — not enabled in v1."""

    def can_issue_fiscal(self) -> bool:
        return False

    def issue_invoice(self, invoice: dict):
        raise RuntimeError(
            "invoice_err_arca_not_configured"
        )

    def generate_draft_pdf(self, invoice: dict) -> bytes:
        return InternalInvoiceProvider().generate_draft_pdf(
            invoice
        )


def get_invoice_provider():
    name = get_invoice_provider_name()
    if name == PROVIDER_ARCA:
        return ArcaInvoiceProvider()
    return InternalInvoiceProvider()
