"""Optional public-page media fallback. Disabled in beta. No scraping or login."""

from __future__ import annotations


class PublicListingMediaProvider:
    """Do not use as the primary photo source. RedREMAX photos[] wins."""

    enabled = False

    def is_enabled(self):
        return bool(self.enabled)

    def list_media(self, property_row):
        """Beta: always empty. A 403/block must stop, not be evaded."""
        return []
