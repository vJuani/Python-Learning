"""Operation invoice drafts bill the entered gross amount.

VAT is discriminated only when ARCA issues Factura A or B.
"""

from __future__ import annotations

import unittest

from modules.arca.voucher_mapping import (
    VOUCHER_FACTURA_A,
    VOUCHER_FACTURA_C,
    fiscal_amounts,
)
from modules.invoicing import operation_invoice_draft_amounts
from modules.vat_billing_calculator import commission_plus_vat


class InvoiceVatModelTests(unittest.TestCase):
    def test_draft_stores_the_entered_amount_and_zero_vat(self):
        amounts = operation_invoice_draft_amounts(121)
        self.assertEqual(amounts["total_amount"], 121.0)
        self.assertEqual(amounts["subtotal"], 121.0)
        self.assertEqual(amounts["vat_amount"], 0.0)

    def test_buyer_and_seller_use_the_same_draft_rule(self):
        self.assertEqual(
            operation_invoice_draft_amounts(250),
            operation_invoice_draft_amounts(250),
        )

    def test_full_or_partial_commission_does_not_add_vat(self):
        full_amount = operation_invoice_draft_amounts(1000)
        partial_amount = operation_invoice_draft_amounts(450)
        self.assertEqual(full_amount["vat_amount"], 0.0)
        self.assertEqual(partial_amount["vat_amount"], 0.0)
        self.assertEqual(partial_amount["total_amount"], 450.0)

    def test_factura_a_splits_included_21_percent(self):
        net, vat, total = fiscal_amounts(121, "ARS", None, VOUCHER_FACTURA_A)
        self.assertEqual(total, 121.0)
        self.assertEqual(net, 100.0)
        self.assertEqual(vat, 21.0)

    def test_factura_c_sends_no_vat(self):
        net, vat, total = fiscal_amounts(121, "ARS", None, VOUCHER_FACTURA_C)
        self.assertEqual((net, vat, total), (121.0, 0.0, 121.0))

    def test_usd_is_converted_before_the_vat_split(self):
        net, vat, total = fiscal_amounts(100, "USD", 1000, VOUCHER_FACTURA_A)
        self.assertEqual(total, 100000.0)
        self.assertEqual(vat, round(100000 - round(100000 / 1.21, 2), 2))
        self.assertGreater(vat, 0)

    def test_separate_vat_exists_only_in_the_commission_helper(self):
        separate = commission_plus_vat(100)
        self.assertEqual(separate["iva"], 21)
        self.assertEqual(separate["total"], 121)
        draft = operation_invoice_draft_amounts(100)
        self.assertEqual(draft["vat_amount"], 0.0)
        self.assertNotEqual(draft["total_amount"], float(separate["total"]))
