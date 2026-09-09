"""Centralized RedREMAX list filters. Organization sync is by office, never by agent."""

from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
LISTINGS_PATH = "/listings/api/listings"


@dataclass(frozen=True)
class RedRemaxSyncFilterConfig:
    associate_status: str = "active"
    combine_status: str = "Activas"
    exclude_status: tuple = (
        "expired",
        "completed",
        "canceled",
        "deleted",
        "draft",
    )
    order_by: str = "-created_on"
    with_clients: bool = False
    with_stats: bool = True
    avoid_units: bool = True
    page_size: int = DEFAULT_PAGE_SIZE

    def normalized_page_size(self):
        try:
            size = int(self.page_size)
        except (TypeError, ValueError):
            size = DEFAULT_PAGE_SIZE
        return max(1, min(size, MAX_PAGE_SIZE))

    def query_pairs(self, *, office_id, page=1, page_size=None, agent=None):
        """Build query pairs. `agent` is optional and NEVER used for org sync."""
        size = page_size if page_size is not None else self.normalized_page_size()
        pairs = [
            ("associateStatus", self.associate_status),
            ("combineStatus", self.combine_status),
        ]
        for status in self.exclude_status:
            pairs.append(("excludeStatus", status))
        pairs.extend(
            [
                ("orderby", self.order_by),
                ("page", str(int(page))),
                ("pagesize", str(int(size))),
                ("withClients", "true" if self.with_clients else "false"),
                ("withStats", "true" if self.with_stats else "false"),
                ("avoidUnits", "true" if self.avoid_units else "false"),
                ("byoffice", str(office_id)),
            ]
        )
        if agent:
            pairs.append(("agent", str(agent)))
        return pairs
