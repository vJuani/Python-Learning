"""JRH One mortgage catalog.

The stored value is a stable id. Display names can change, and new banks can
be appended here, without rewriting old reservation rows.
"""

from __future__ import annotations


MORTGAGE_BANKS = (
    ("nacion", "Banco Nación"),
    ("provincia", "Banco Provincia"),
    ("ciudad", "Banco Ciudad"),
    ("hipotecario", "Banco Hipotecario"),
    ("bbva", "BBVA"),
    ("santander", "Santander"),
    ("galicia", "Galicia"),
    ("icbc", "ICBC"),
    ("patagonia", "Banco Patagonia"),
    ("supervielle", "Supervielle"),
)

_BY_ID = dict(MORTGAGE_BANKS)


def mortgage_banks():
    return [{"id": bank_id, "name": name} for bank_id, name in MORTGAGE_BANKS]


def is_mortgage_bank(bank_id):
    return str(bank_id or "").strip() in _BY_ID


def bank_label(bank_id):
    key = str(bank_id or "").strip()
    if not key:
        return ""
    return _BY_ID.get(key, key)
