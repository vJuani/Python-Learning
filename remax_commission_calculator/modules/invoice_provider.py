"""
Invoice provider abstraction (internal today, ARCA later).
"""

from __future__ import annotations

import io
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


PROVIDER_INTERNAL = "internal"
PROVIDER_ARCA = "arca"

ARCA_STATUS_NOT_CONFIGURED = "not_configured"
ARCA_STATUS_CONFIGURING = "configuring"
ARCA_STATUS_CONNECTED = "connected"
ARCA_STATUS_ERROR = "error"


@dataclass
class IssuerConfigurationStatus:
    is_ready: bool
    connection_status: str = ARCA_STATUS_NOT_CONFIGURED
    message_key: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class FiscalIssueResult:
    success: bool
    cae: str | None = None
    cae_expiration: str | None = None
    voucher_number: str | None = None
    point_of_sale: str | None = None
    voucher_type: str | None = None
    provider_reference: str | None = None
    error_key: str | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)


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
    def validate_issuer_configuration(
        self,
        issuer_profile: dict,
    ) -> IssuerConfigurationStatus:
        raise NotImplementedError

    @abstractmethod
    def authenticate(self, issuer_profile: dict) -> bool:
        raise NotImplementedError

    @abstractmethod
    def issue_invoice(
        self,
        invoice: dict,
        *,
        issuer_profile: dict | None = None,
    ) -> FiscalIssueResult:
        raise NotImplementedError

    @abstractmethod
    def get_last_voucher(
        self,
        issuer_profile: dict,
        *,
        point_of_sale: str,
        voucher_type: str,
    ) -> int | None:
        raise NotImplementedError

    @abstractmethod
    def fetch_invoice_status(
        self,
        provider_reference: str,
        *,
        issuer_profile: dict | None = None,
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    def fetch_pdf(
        self,
        provider_reference: str,
        *,
        issuer_profile: dict | None = None,
    ) -> bytes | None:
        raise NotImplementedError

    @abstractmethod
    def generate_draft_pdf(self, invoice: dict) -> bytes:
        raise NotImplementedError

    def generate_fiscal_pdf(self, invoice: dict) -> bytes:
        return self.generate_draft_pdf(invoice)


class InternalInvoiceProvider(InvoiceProvider):
    def can_issue_fiscal(self) -> bool:
        return False

    def validate_issuer_configuration(
        self,
        issuer_profile: dict,
    ) -> IssuerConfigurationStatus:
        return IssuerConfigurationStatus(
            is_ready=True,
            connection_status=ARCA_STATUS_NOT_CONFIGURED,
            message_key="billing_arca_internal_only",
        )

    def authenticate(self, issuer_profile: dict) -> bool:
        return False

    def issue_invoice(
        self,
        invoice: dict,
        *,
        issuer_profile: dict | None = None,
    ) -> FiscalIssueResult:
        return FiscalIssueResult(
            success=False,
            error_key="invoice_err_fiscal_issue_unavailable",
        )

    def get_last_voucher(
        self,
        issuer_profile: dict,
        *,
        point_of_sale: str,
        voucher_type: str,
    ) -> int | None:
        return None

    def fetch_invoice_status(
        self,
        provider_reference: str,
        *,
        issuer_profile: dict | None = None,
    ) -> dict:
        return {"status": "internal"}

    def fetch_pdf(
        self,
        provider_reference: str,
        *,
        issuer_profile: dict | None = None,
    ) -> bytes | None:
        return None

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
    """WSAA + WSFEv1 — homologation only."""

    def __init__(self, *, transport=None):
        from modules.arca.client import ArcaClient
        from modules.arca.config import is_arca_fiscal_enabled
        from modules.database.arca_repository import (
            get_cached_ta,
            store_cached_ta,
        )

        self._enabled = is_arca_fiscal_enabled()
        self._client = ArcaClient(
            transport=transport,
            cache_getter=get_cached_ta,
            cache_setter=store_cached_ta,
        )

    def can_issue_fiscal(self) -> bool:
        return self._enabled

    def validate_issuer_configuration(
        self,
        issuer_profile: dict,
    ) -> IssuerConfigurationStatus:
        from modules.arca.validation import validate_fiscal_issue

        dummy_invoice = {
            "status": "ready_to_issue",
            "currency": "ARS",
            "total_amount": 1,
            "issue_date": "2026-01-01",
            "side": "buyer",
            "recipient_name": "Test",
            "recipient_tax_id": "20-30000000-3",
            "recipient_tax_condition": "consumidor_final",
        }
        result = validate_fiscal_issue(
            dummy_invoice,
            issuer_profile,
        )
        status = (
            issuer_profile or {}
        ).get("arca_connection_status") or ARCA_STATUS_NOT_CONFIGURED
        if result.is_valid:
            return IssuerConfigurationStatus(
                is_ready=True,
                connection_status=ARCA_STATUS_CONNECTED,
                message_key="billing_arca_ready",
                details={
                    "point_of_sale": issuer_profile.get(
                        "arca_point_of_sale"
                    ),
                },
            )
        return IssuerConfigurationStatus(
            is_ready=False,
            connection_status=status,
            message_key=result.error_key
            or "billing_arca_not_configured",
        )

    def authenticate(self, issuer_profile: dict) -> bool:
        try:
            self._client.authenticate(
                issuer_profile,
                {"issuer_tax_id": issuer_profile.get("tax_id")},
            )
            return True
        except Exception:
            return False

    def issue_invoice(
        self,
        invoice: dict,
        *,
        issuer_profile: dict | None = None,
    ) -> FiscalIssueResult:
        if issuer_profile is None:
            return FiscalIssueResult(
                success=False,
                error_key="invoice_err_arca_not_configured",
            )
        wsfe = self._client.issue_invoice(
            invoice,
            issuer_profile,
        )
        if not wsfe.success:
            return FiscalIssueResult(
                success=False,
                error_key=(wsfe.errors or ["invoice_err_arca_issue_rejected"])[0],
            )
        return FiscalIssueResult(
            success=True,
            cae=wsfe.cae,
            cae_expiration=wsfe.cae_expiration,
            voucher_number=str(wsfe.voucher_number),
            point_of_sale=str(wsfe.point_of_sale),
            voucher_type=str(wsfe.voucher_type),
            provider_reference=wsfe.raw_reference,
            snapshot={
                "cae": wsfe.cae,
                "voucher_number": wsfe.voucher_number,
            },
        )

    def get_last_voucher(
        self,
        issuer_profile: dict,
        *,
        point_of_sale: str,
        voucher_type: str,
    ) -> int | None:
        ticket = self._client.authenticate(
            issuer_profile,
            {"issuer_tax_id": issuer_profile.get("tax_id")},
        )
        from modules.arca.wsfev1 import get_last_authorized_voucher
        import re

        cuit = re.sub(
            r"\D",
            "",
            str(issuer_profile.get("tax_id") or ""),
        )
        return get_last_authorized_voucher(
            ticket=ticket,
            cuit=cuit,
            point_of_sale=int(point_of_sale),
            voucher_type=int(voucher_type),
            transport=self._client.transport,
        )

    def fetch_invoice_status(
        self,
        provider_reference: str,
        *,
        issuer_profile: dict | None = None,
    ) -> dict:
        return {"status": "issued", "reference": provider_reference}

    def fetch_pdf(
        self,
        provider_reference: str,
        *,
        issuer_profile: dict | None = None,
    ) -> bytes | None:
        return None

    def generate_draft_pdf(self, invoice: dict) -> bytes:
        return InternalInvoiceProvider().generate_draft_pdf(
            invoice
        )

    def generate_fiscal_pdf(self, invoice: dict) -> bytes:
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(
            20 * mm,
            height - 25 * mm,
            "HOMOLOGACIÓN — FACTURA DE PRUEBA",
        )
        pdf.setFont("Helvetica", 10)
        pdf.drawString(
            20 * mm,
            height - 32 * mm,
            "TEST ENVIRONMENT — NOT VALID FOR PRODUCTION",
        )
        y = height - 50 * mm
        lines = [
            ("Nº", invoice.get("external_invoice_number") or ""),
            ("Punto de venta", invoice.get("point_of_sale") or ""),
            ("CAE", invoice.get("cae") or ""),
            ("Vto CAE", invoice.get("cae_expiration") or ""),
            ("Emisor", invoice.get("issuer_name") or ""),
            ("CUIT emisor", invoice.get("issuer_tax_id") or ""),
            ("Receptor", invoice.get("recipient_name") or ""),
            ("CUIT receptor", invoice.get("recipient_tax_id") or ""),
            (
                "Importe",
                f"{invoice.get('currency', 'ARS')} "
                f"{float(invoice.get('total_amount') or 0):,.2f}",
            ),
            ("Fecha", invoice.get("issue_date") or ""),
            ("Ambiente", invoice.get("fiscal_environment") or "homologation"),
        ]
        pdf.setFont("Helvetica", 11)
        for label, value in lines:
            pdf.drawString(20 * mm, y, f"{label}: {value}")
            y -= 8 * mm
        pdf.save()
        return buffer.getvalue()


class MockArcaInvoiceProvider(ArcaInvoiceProvider):
    """Test double — never calls external ARCA services."""

    def can_issue_fiscal(self) -> bool:
        return True

    def issue_invoice(
        self,
        invoice: dict,
        *,
        issuer_profile: dict | None = None,
    ) -> FiscalIssueResult:
        return FiscalIssueResult(
            success=True,
            cae="MOCK-CAE-12345678",
            cae_expiration="2026-12-31",
            voucher_number="00000042",
            point_of_sale=(
                (issuer_profile or {}).get("arca_point_of_sale")
                or "0005"
            ),
            voucher_type="11",
            provider_reference="mock-arca-ref",
            snapshot={
                "issuer_name": invoice.get("issuer_name"),
                "issuer_tax_id": invoice.get("issuer_tax_id"),
                "issuer_tax_condition": invoice.get(
                    "issuer_tax_condition"
                ),
                "issuer_address": invoice.get("issuer_address"),
                "recipient_name": invoice.get("recipient_name"),
                "recipient_tax_id": invoice.get(
                    "recipient_tax_id"
                ),
                "recipient_tax_condition": invoice.get(
                    "recipient_tax_condition"
                ),
                "recipient_address": invoice.get(
                    "recipient_address"
                ),
                "point_of_sale": (
                    (issuer_profile or {}).get("arca_point_of_sale")
                    or "0005"
                ),
                "voucher_type": "11",
                "voucher_number": "00000042",
                "cae": "MOCK-CAE-12345678",
                "cae_expiration": "2026-12-31",
                "total_amount": invoice.get("total_amount"),
                "currency": invoice.get("currency"),
                "issue_date": invoice.get("issue_date"),
            },
        )


def get_invoice_provider(*, mock_arca=False):
    name = get_invoice_provider_name()
    if mock_arca:
        return MockArcaInvoiceProvider()
    if name == PROVIDER_ARCA:
        return ArcaInvoiceProvider()
    return InternalInvoiceProvider()
