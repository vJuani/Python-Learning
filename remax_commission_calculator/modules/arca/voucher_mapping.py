"""
Map fiscal profiles to AFIP voucher types (initial subset).
"""

from __future__ import annotations

# AFIP cbte tipo (WSFEv1)
VOUCHER_FACTURA_A = 1
VOUCHER_FACTURA_B = 6
VOUCHER_FACTURA_C = 11

CONCEPT_SERVICES = 2

DOC_TYPE_CUIT = 80
DOC_TYPE_DNI = 96

IVA_21 = 5


def resolve_voucher_type(
    *,
    issuer_tax_condition: str,
    recipient_tax_condition: str,
    explicit_type: int | str | None = None,
) -> int:
    if explicit_type is not None and str(explicit_type).strip():
        return int(explicit_type)

    issuer = (issuer_tax_condition or "").strip().lower()
    recipient = (recipient_tax_condition or "").strip().lower()

    if issuer == "monotributo":
        return VOUCHER_FACTURA_C

    if issuer == "responsable_inscripto":
        if recipient == "responsable_inscripto":
            return VOUCHER_FACTURA_A
        return VOUCHER_FACTURA_B

    if issuer in ("exento", "consumidor_final"):
        return VOUCHER_FACTURA_C

    return None


def fiscal_amounts(total_amount, currency, exchange_rate, voucher_type):
    """ARS amounts sent to WSFE.

    USD is converted with the invoice exchange rate first.
    Factura A and B treat the result as IVA incluido at 21%.
    Factura C does not discriminate IVA.
    """
    total_source = float(total_amount or 0)
    if (currency or "ARS").upper() == "USD":
        total_source = round(total_source * float(exchange_rate or 0), 2)
    return split_amounts_for_voucher(total_source, voucher_type)


def split_amounts_for_voucher(
    total_amount: float,
    voucher_type: int,
) -> tuple[float, float, float]:
    """Return (net, vat, total) for WSFE request."""
    total = round(float(total_amount), 2)
    if voucher_type == VOUCHER_FACTURA_A:
        net = round(total / 1.21, 2)
        vat = round(total - net, 2)
        return net, vat, total
    if voucher_type == VOUCHER_FACTURA_B:
        net = round(total / 1.21, 2)
        vat = round(total - net, 2)
        return net, vat, total
    # Factura C — no discriminated IVA
    return total, 0.0, total
