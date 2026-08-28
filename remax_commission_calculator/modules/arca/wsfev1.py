"""
WSFEv1 — último comprobante y solicitud de CAE.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from modules.arca.config import get_wsfe_url
from modules.arca.wsaa import TicketAcceso

logger = logging.getLogger(__name__)

NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS_WSFE = "http://ar.gov.afip.dif.FEV1/"


@dataclass(frozen=True)
class CaeIssueResult:
    success: bool
    cae: str | None = None
    cae_expiration: str | None = None
    voucher_number: int | None = None
    point_of_sale: int | None = None
    voucher_type: int | None = None
    errors: list[str] | None = None
    observations: list[str] | None = None
    raw_reference: str | None = None


def _cuit_digits(cuit: str) -> str:
    return re.sub(r"\D", "", str(cuit or ""))


def _soap_envelope(action: str, inner_xml: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="{NS_SOAP}" xmlns:ar="{NS_WSFE}">
  <soap:Body>
    <ar:{action}>
      {inner_xml}
    </ar:{action}>
  </soap:Body>
</soap:Envelope>"""


def _auth_xml(ticket: TicketAcceso, cuit: str) -> str:
    return f"""<ar:Auth>
      <ar:Token>{ticket.token}</ar:Token>
      <ar:Sign>{ticket.sign}</ar:Sign>
      <ar:Cuit>{_cuit_digits(cuit)}</ar:Cuit>
    </ar:Auth>"""


def _extract_result_xml(body: str, result_tag: str) -> str:
    marker = f"{result_tag}Result"
    if marker not in body:
        raise ValueError("invoice_err_arca_wsfe_failed")
    start = body.index(f"<{marker}>") + len(f"<{marker}>")
    end = body.index(f"</{marker}>")
    return body[start:end]


def wsfe_request(
    action: str,
    inner_xml: str,
    *,
    ticket: TicketAcceso,
    cuit: str,
    transport=None,
) -> str:
    envelope = _soap_envelope(action, inner_xml)
    if transport is not None:
        return transport.wsfe_call(
            action,
            envelope,
            ticket,
            cuit,
        )

    import urllib.request

    url = get_wsfe_url()
    request = urllib.request.Request(
        url,
        data=envelope.encode("utf-8"),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f"{NS_WSFE}{action}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read().decode("utf-8", errors="replace")


def get_last_authorized_voucher(
    *,
    ticket: TicketAcceso,
    cuit: str,
    point_of_sale: int,
    voucher_type: int,
    transport=None,
) -> int:
    logger.info("arca_last_voucher")
    auth = _auth_xml(ticket, cuit)
    inner = f"""{auth}
      <ar:PtoVta>{int(point_of_sale)}</ar:PtoVta>
      <ar:CbteTipo>{int(voucher_type)}</ar:CbteTipo>"""

    body = wsfe_request(
        "FECompUltimoAutorizado",
        inner,
        ticket=ticket,
        cuit=cuit,
        transport=transport,
    )
    result_xml = _extract_result_xml(
        body,
        "FECompUltimoAutorizado",
    )
    root = ET.fromstring(f"<root>{result_xml}</root>")
    cbte = root.findtext(".//CbteNro") or root.findtext(
        ".//{http://ar.gov.afip.dif.FEV1/}CbteNro"
    )
    if cbte is None:
        errors = root.findall(".//Err")
        if errors:
            msg = errors[0].findtext("Msg") or "wsfe_error"
            raise ValueError(f"invoice_err_arca_wsfe_failed:{msg}")
        return 0
    return int(cbte)


def request_cae(
    *,
    ticket: TicketAcceso,
    cuit: str,
    point_of_sale: int,
    voucher_type: int,
    voucher_number: int,
    concept: int,
    doc_type: int,
    doc_number: int,
    voucher_date: str,
    total_amount: float,
    net_amount: float,
    vat_amount: float,
    currency: str = "PES",
    exchange_rate: float = 1.0,
    iva_id: int | None = None,
    iva_base: float | None = None,
    transport=None,
) -> CaeIssueResult:
    logger.info("arca_issue_start")
    auth = _auth_xml(ticket, cuit)

    iva_block = ""
    if iva_id is not None and vat_amount > 0:
        iva_block = f"""
        <ar:Iva>
          <ar:AlicIva>
            <ar:Id>{iva_id}</ar:Id>
            <ar:BaseImp>{iva_base:.2f}</ar:BaseImp>
            <ar:Importe>{vat_amount:.2f}</ar:Importe>
          </ar:AlicIva>
        </ar:Iva>"""

    date_yyyymmdd = voucher_date.replace("-", "")[:8]

    inner = f"""{auth}
      <ar:FeCAEReq>
        <ar:FeCabReq>
          <ar:CantReg>1</ar:CantReg>
          <ar:PtoVta>{int(point_of_sale)}</ar:PtoVta>
          <ar:CbteTipo>{int(voucher_type)}</ar:CbteTipo>
        </ar:FeCabReq>
        <ar:FeDetReq>
          <ar:FECAEDetRequest>
            <ar:Concepto>{concept}</ar:Concepto>
            <ar:DocTipo>{doc_type}</ar:DocTipo>
            <ar:DocNro>{int(doc_number)}</ar:DocNro>
            <ar:CbteDesde>{int(voucher_number)}</ar:CbteDesde>
            <ar:CbteHasta>{int(voucher_number)}</ar:CbteHasta>
            <ar:CbteFch>{date_yyyymmdd}</ar:CbteFch>
            <ar:ImpTotal>{total_amount:.2f}</ar:ImpTotal>
            <ar:ImpTotConc>0</ar:ImpTotConc>
            <ar:ImpNeto>{net_amount:.2f}</ar:ImpNeto>
            <ar:ImpOpEx>0</ar:ImpOpEx>
            <ar:ImpTrib>0</ar:ImpTrib>
            <ar:ImpIVA>{vat_amount:.2f}</ar:ImpIVA>
            <ar:MonId>{currency}</ar:MonId>
            <ar:MonCotiz>{exchange_rate:.6f}</ar:MonCotiz>
            {iva_block}
          </ar:FECAEDetRequest>
        </ar:FeDetReq>
      </ar:FeCAEReq>"""

    body = wsfe_request(
        "FECAESolicitar",
        inner,
        ticket=ticket,
        cuit=cuit,
        transport=transport,
    )
    result_xml = _extract_result_xml(body, "FECAESolicitar")
    root = ET.fromstring(f"<root>{result_xml}</root>")

    det = root.find(".//FeDetResp/FECAEDetResponse")
    if det is None:
        det = root.find(
            ".//{http://ar.gov.afip.dif.FEV1/}FeDetResp"
            "/{http://ar.gov.afip.dif.FEV1/}FECAEDetResponse"
        )

    if det is None:
        logger.error("arca_issue_failed: no detail response")
        return CaeIssueResult(
            success=False,
            errors=["invoice_err_arca_wsfe_failed"],
        )

    result = (det.findtext("Resultado") or "").strip()
    if result == "A":
        cae = (det.findtext("CAE") or "").strip()
        cae_vto = (det.findtext("CAEFchVto") or "").strip()
        cbte = det.findtext("CbteDesde")
        logger.info("arca_issue_success")
        return CaeIssueResult(
            success=True,
            cae=cae,
            cae_expiration=_format_cae_expiration(cae_vto),
            voucher_number=int(cbte) if cbte else voucher_number,
            point_of_sale=point_of_sale,
            voucher_type=voucher_type,
            raw_reference=f"wsfe:{point_of_sale}-{voucher_type}-{cbte}",
        )

    errors = []
    for err in det.findall(".//Obs") + root.findall(".//Errors/Err"):
        code = err.findtext("Code") or err.findtext("code")
        msg = err.findtext("Msg") or err.findtext("msg") or ""
        errors.append(f"{code}:{msg}".strip(":"))

    logger.error("arca_issue_failed: %s", errors[:3])
    return CaeIssueResult(
        success=False,
        errors=errors or ["invoice_err_arca_issue_rejected"],
        voucher_number=voucher_number,
        point_of_sale=point_of_sale,
        voucher_type=voucher_type,
    )


def _format_cae_expiration(yymmdd: str) -> str:
    if len(yymmdd) == 8:
        return f"{yymmdd[0:4]}-{yymmdd[4:6]}-{yymmdd[6:8]}"
    return yymmdd
