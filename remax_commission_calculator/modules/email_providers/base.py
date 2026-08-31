"""
Email provider abstraction for transactional mail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class EmailDeliveryError(Exception):
    """Raised when outbound email cannot be delivered."""

    def __init__(self, error_key, *, detail=None):
        super().__init__(error_key)
        self.error_key = error_key
        self.detail = (detail or "")[:300]


@dataclass(frozen=True)
class OutboundEmail:
    to: str
    subject: str
    text_body: str
    html_body: str | None = None


class EmailProvider(Protocol):
    """Send a single transactional email."""

    @property
    def backend_name(self) -> str:
        ...

    def send(self, message: OutboundEmail) -> None:
        ...
