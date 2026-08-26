"""Simple list pagination helpers."""

from __future__ import annotations


DEFAULT_PER_PAGE = 15


def parse_page(raw_value, default=1):
    try:
        page = int(raw_value)
    except (TypeError, ValueError):
        return default

    return page if page > 0 else default


def build_page_numbers(current_page, total_pages, window=2):
    """Return page numbers with None placeholders for ellipses."""
    if total_pages <= 1:
        return [1] if total_pages == 1 else []

    if total_pages <= 7:
        return list(range(1, total_pages + 1))

    pages = {1, total_pages, current_page}
    for offset in range(1, window + 1):
        pages.add(current_page - offset)
        pages.add(current_page + offset)

    ordered = sorted(
        page
        for page in pages
        if 1 <= page <= total_pages
    )

    result = []
    previous = None
    for page in ordered:
        if previous is not None and page - previous > 1:
            result.append(None)
        result.append(page)
        previous = page

    return result


def paginate_list(items, page=1, per_page=DEFAULT_PER_PAGE):
    """
    Slice ``items`` for the requested page.

    Returns a dict with the page slice and navigation metadata.
    """
    total = len(items)
    per_page = max(1, int(per_page))
    total_pages = (
        max(1, (total + per_page - 1) // per_page)
        if total
        else 1
    )
    page = parse_page(page, default=1)
    page = min(page, total_pages)

    start = (page - 1) * per_page
    end = min(start + per_page, total)

    return {
        "items": items[start:end],
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "start_index": (start + 1) if total else 0,
        "end_index": end,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < total_pages else None,
        "page_numbers": build_page_numbers(
            page,
            total_pages,
        ),
    }
