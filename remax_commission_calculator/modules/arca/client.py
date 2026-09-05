"""
High-level ARCA client orchestrating WSAA + WSFEv1.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from modules.arca.config import WSAA_SERVICE_WSFE, get_arca_environment
from modules.arca.connections import load_credentials as load_connection_credentials
from modules.arca.connections import ta_cache_key
from modules.arca.secrets import ArcaCredentials
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
        *,
        connection=None,
        credentials: ArcaCredentials | None = None,
        user_id=None,
        organization_id=None,
    ) -> TicketAcceso:
        if credentials is None:
            if connection is None:
                raise ValueError("invoice_err_arca_not_linked")
            credentials = load_connection_credentials(connection)
        cache_key = None
        if organization_id is not None and user_id is not None:
            cache_key = ta_cache_key(organization_id, user_id)
        elif connection is not None:
            cache_key = ta_cache_key(
                connection.get("organization_id"),
                connection.get("user_id"),
                environment=connection.get("environment"),
            )
        return authenticate_wsaa(
            credentials,
            cuit=self._cuit(issuer_profile, invoice),
            service=WSAA_SERVICE_WSFE,
            transport=self.transport,
            cache_getter=self.cache_getter,
            cache_setter=self.cache_setter,
            cache_key=cache_key,
        )

    def issue_invoice(
        self,
        invoice: dict,
        issuer_profile: dict,
        *,
        connection=None,
        credentials: ArcaCredentials | None = None,
        user_id=None,
        organization_id=None,
        next_voucher_number: int | None = None,
    ) -> CaeIssueResult:
        validation = validate_fiscal_issue(
            invoice,
            issuer_profile,
            connection=connection,
        )
        if not validation.is_valid:
            return CaeIssueResult(
                success=False,
                errors=[validation.error_key or "invalid"],
            )

        ticket = self.authenticate(
            issuer_profile,
            invoice,
            connection=connection,
            credentials=credentials,
            user_id=user_id,
            organization_id=organization_id,
        )
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

        total_source = float(invoice.get("total_amount") or 0)
        if (invoice.get("currency") or "ARS").upper() == "USD":
            total_source = round(
                total_source * float(invoice.get("exchange_rate") or 0),
                2,
            )
        net, vat, total = split_amounts_for_voucher(
            total_source,
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
