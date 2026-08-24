import unittest
from decimal import Decimal

from modules.vat_billing_calculator import (
    MODE_COMMISSION_PLUS_VAT,
    MODE_MINIMUM_VAT,
    TIP_BUYER,
    TIP_SELLER,
    agent_invoice_from_martillero_net,
    build_calculator_result,
    build_client_invoices,
    commission_plus_vat,
    minimum_vat,
    parse_calculator_inputs,
    round_to_nearest_50,
    tip_commission,
    truncate_money_2,
)


class TipCommissionTests(unittest.TestCase):
    def test_tip_commission(self):
        self.assertEqual(
            tip_commission(200_000, 3),
            Decimal("6000")
        )


class MinimumVatTests(unittest.TestCase):
    def test_minimum_vat_chain(self):
        result = minimum_vat(1_000)

        self.assertEqual(result["commission"], Decimal("1000"))
        self.assertEqual(result["base_60"], Decimal("600"))
        self.assertEqual(result["base_55"], Decimal("330"))
        self.assertEqual(result["iva_exact"], Decimal("69.3"))
        self.assertEqual(result["iva_suggested"], Decimal("50"))

    def test_minimum_vat_from_operation_tip(self):
        commission = tip_commission(100_000, 4)
        result = minimum_vat(commission)

        self.assertEqual(commission, Decimal("4000"))
        self.assertEqual(result["base_60"], Decimal("2400"))
        self.assertEqual(result["base_55"], Decimal("1320"))
        self.assertEqual(result["iva_exact"], Decimal("277.2"))
        self.assertEqual(result["iva_suggested"], Decimal("300"))


class CommissionPlusVatTests(unittest.TestCase):
    def test_commission_plus_vat(self):
        result = commission_plus_vat(1_000)

        self.assertEqual(result["commission"], Decimal("1000"))
        self.assertEqual(result["iva"], Decimal("210"))
        self.assertEqual(result["total"], Decimal("1210"))


class RoundingTests(unittest.TestCase):
    def test_round_to_nearest_50_examples(self):
        cases = (
            ("207.90", "200"),
            ("225", "200"),
            ("230", "250"),
            ("245", "250"),
            ("265", "250"),
            ("280", "300"),
            ("285", "300"),
        )

        for amount, expected in cases:
            with self.subTest(amount=amount):
                self.assertEqual(
                    round_to_nearest_50(amount),
                    Decimal(expected)
                )


class TruncateMoneyTests(unittest.TestCase):
    def test_truncate_examples(self):
        self.assertEqual(
            truncate_money_2("1447619.0476"),
            Decimal("1447619.04")
        )
        self.assertEqual(
            truncate_money_2("1184415.5844"),
            Decimal("1184415.58")
        )
        self.assertEqual(
            truncate_money_2("304000"),
            Decimal("304000.00")
        )


class ClientInvoiceTests(unittest.TestCase):
    def test_example_usd_200_fx_1520(self):
        invoices = build_client_invoices("200", "1520")

        self.assertEqual(
            invoices["iva_ars"],
            Decimal("304000.00")
        )
        self.assertEqual(
            invoices["martillero_net"],
            Decimal("1447619.04")
        )
        self.assertEqual(
            invoices["agent_net"],
            Decimal("1184415.58")
        )

    def test_agent_55_45_precision(self):
        raw = agent_invoice_from_martillero_net(
            Decimal("1447619.047619047619")
        )
        self.assertEqual(
            truncate_money_2(raw),
            Decimal("1184415.58")
        )


class BuildResultTests(unittest.TestCase):
    def test_build_minimum_with_override_and_fx(self):
        parsed, errors = parse_calculator_inputs({
            "operation_amount": "100000",
            "buyer_rate": "4",
            "seller_rate": "3",
            "tip": TIP_BUYER,
            "mode": MODE_MINIMUM_VAT,
            "vat_usd": "200",
            "exchange_rate": "1520",
        })

        self.assertEqual(errors, [])
        result = build_calculator_result(parsed)

        self.assertEqual(result["tip_commission"], Decimal("4000"))
        self.assertEqual(result["vat_usd_auto"], Decimal("300"))
        self.assertEqual(result["vat_usd"], Decimal("200"))
        self.assertEqual(
            result["billing"]["iva_ars"],
            Decimal("304000.00")
        )
        self.assertEqual(
            result["billing"]["martillero_net"],
            Decimal("1447619.04")
        )
        self.assertEqual(
            result["billing"]["agent_net"],
            Decimal("1184415.58")
        )
        # Internal steps still available for tests.
        self.assertEqual(
            result["minimum_vat"]["base_60"],
            Decimal("2400")
        )

    def test_build_seller_commission_plus_vat(self):
        parsed, errors = parse_calculator_inputs({
            "operation_amount": "200000",
            "buyer_rate": "4",
            "seller_rate": "3",
            "tip": TIP_SELLER,
            "mode": MODE_COMMISSION_PLUS_VAT,
        })

        self.assertEqual(errors, [])
        result = build_calculator_result(parsed)

        self.assertEqual(result["tip_commission"], Decimal("6000"))
        self.assertEqual(result["vat_usd"], Decimal("1260"))
        self.assertEqual(
            result["commission_plus_vat"]["total"],
            Decimal("7260")
        )


if __name__ == "__main__":
    unittest.main()
