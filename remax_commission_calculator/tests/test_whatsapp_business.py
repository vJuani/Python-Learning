import unittest

from modules.database.tenant import TenantError
from modules.whatsapp_business import (
    WhatsAppBusinessUnavailable,
    can_send_document,
    send_document,
)


class WhatsAppBusinessContractTests(unittest.TestCase):
    def test_each_office_is_refused_until_it_connects(self):
        self.assertFalse(can_send_document(7))
        self.assertFalse(can_send_document(8))
        with self.assertRaises(WhatsAppBusinessUnavailable) as first:
            send_document(
                7,
                to_phone="+5491111111111",
                pdf_bytes=b"%PDF",
                filename="Ficha.pdf",
                caption="Hola",
            )
        with self.assertRaises(WhatsAppBusinessUnavailable) as second:
            send_document(
                8,
                to_phone="+5491122222222",
                pdf_bytes=b"%PDF",
                filename="Ficha.pdf",
                caption="Hola",
            )
        self.assertEqual(first.exception.organization_id, 7)
        self.assertEqual(second.exception.organization_id, 8)

    def test_a_missing_office_is_rejected(self):
        with self.assertRaises(TenantError):
            can_send_document(None)
        with self.assertRaises(TenantError):
            send_document(
                None,
                to_phone="+5491111111111",
                pdf_bytes=b"%PDF",
                filename="Ficha.pdf",
                caption="Hola",
            )


if __name__ == "__main__":
    unittest.main()
