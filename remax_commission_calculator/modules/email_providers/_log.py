"""Shared logging helpers for email providers."""

from __future__ import annotations


def normalize_recipient(email: str) -> str:
    return (email or "").strip().lower()


def mask_email_for_log(email: str) -> str:
    address = normalize_recipient(email)

    if "@" not in address:
        return address

    local, domain = address.split("@", 1)
    visible = local[:1] if len(local) <= 2 else local[:2]
    return f"{visible}***@{domain}"
