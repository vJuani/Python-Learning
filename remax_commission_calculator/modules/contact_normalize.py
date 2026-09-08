"""Phone and email normalization for matching. Original values stay intact."""

from __future__ import annotations

import re


def normalize_email(value):
    text = (value or "").strip().lower()
    return text or None


def phone_digits(value):
    return re.sub(r"\D", "", value or "")


def normalize_phone(value, *, default_country=None):
    """
    Return a matching key without inventing a country.

    If the number already has 54..., keep it.
    If it is a 10-digit local AR mobile (11xxxxxxxx) and the caller
    explicitly passed default_country='AR', prefix 54.
    Otherwise store digits only.
    """
    digits = phone_digits(value)
    if len(digits) < 8:
        return None
    if digits.startswith("54"):
        return digits
    if digits.startswith("9") and len(digits) >= 11 and default_country == "AR":
        return "54" + digits
    if default_country == "AR" and len(digits) == 10 and digits.startswith("11"):
        return "54" + digits
    return digits


def phones_match(left, right):
    a = normalize_phone(left)
    b = normalize_phone(right)
    if not a or not b:
        return False
    if a == b:
        return True
    return a[-8:] == b[-8:] if len(a) >= 8 and len(b) >= 8 else False
