"""
High-level ARCA client orchestrating WSAA + WSFEv1.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from modules.arca.config import WSAA_SERVICE_WSFE, get_arca_environment
from modules.arca.secrets import ArcaCredentials, load_credentials
from modules.arca.validation import validate_fiscal_issue
from modules.arca.voucher_mapping import (
    CONCEPT_SERVICES,
    DOC_TYPE_CUIT,
    IVA_21,
    split_amounts_for_voucher,
)
from modules.arca.wsaa import TicketAcceso, authenticate_wsaa
from modules.arca.wsfev1 import (
    CaeIssueResult,
    get_last_authorized_voucher,
    request_cae,
)

logger = logging.getLogger(__name__)


class ArcaTransport(Protocol):
    def wsaa_login(self, cms_b64: str) -> str: ...

    def wsfe_call(
        self,
        action: str,
        envelope: str,
        ticket: TicketAcceso,
        cuit: str,
    ) -> str: ...


class ArcaClient:
    def __init__(
        self,
        *,
        transport: ArcaTransport | None = None,
        cache_getter=None,
        cache_setter=None,
    ):
        self.transport = transport
        self.cache_getter = cache_getter
        self.cache_setter = cache_setter

    def _cuit(self, issuer_profile: dict, invoice: dict) -> str:
        raw = (
            issuer_profile.get("tax_id")
            or invoice.get("issuer_tax_id")
            or ""
        )
        return re.sub(r"\D", "", str(raw))

    def authenticate(
        self,
        issuer_profile: dict,
        invoice: dict,
    ) -> TicketAcceso:
        credentials = load_credentials(issuer_profile)
        return authenticate_wsaa(
            credentials,
            cuit=self._cuit(issuer_profile, invoice),
            service=WSAA_SERVICE_WSFE,
            transport=self.transport,
            cache_getter=self.cache_getter,
            cache_setter=self.cache_setter,
        )

    def issue_invoice(
        self,
        invoice: dict,
        issuer_profile: dict,
        *,
        next_voucher_number: int | None = None,
    ) -> CaeIssueResult:
        validation = validate_fiscal_issue(
            invoice,
            issuer_profile,
        )
        if not validation.is_valid:
            return CaeIssueResult(
                success=False,
                errors=[validation.error_key or "invalid"],
            )

        ticket = self.authenticate(issuer_profile, invoice)
        cuit = self._cuit(issuer_profile, invoice)
        pv = validation.point_of_sale
        cbte_tipo = validation.voucher_type

        if next_voucher_number is None:
            last = get_last_authorized_voucher(
                ticket=ticket,
                cuit=cuit,
                point_of_sale=pv,
                voucher_type=cbte_tipo,
                transport=self.transport,
            )
            next_voucher_number = last + 1

        net, vat, total = split_amounts_for_voucher(
            float(invoice.get("total_amount") or 0),
            cbte_tipo,
        )

        doc_number = re.sub(
            r"\D",
            "",
            str(invoice.get("recipient_tax_id") or ""),
        )

        return request_cae(
            ticket=ticket,
            cuit=cuit,
            point_of_sale=pv,
            voucher_type=cbte_tipo,
            voucher_number=next_voucher_number,
            concept=CONCEPT_SERVICES,
            doc_type=DOC_TYPE_CUIT,
            doc_number=int(doc_number),
            voucher_date=invoice.get("issue_date"),
            total_amount=total,
            net_amount=net,
            vat_amount=vat,
            iva_id=IVA_21 if vat > 0 else None,
            iva_base=net if vat > 0 else None,
            transport=self.transport,
        )
