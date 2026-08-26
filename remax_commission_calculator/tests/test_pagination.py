"""Tests for list pagination helpers."""

from __future__ import annotations

import unittest

from modules.pagination import (
    DEFAULT_PER_PAGE,
    build_page_numbers,
    paginate_list,
    parse_page,
    parse_per_page,
)


class PaginationTests(unittest.TestCase):
    def test_default_page_size_is_fifteen(self):
        self.assertEqual(DEFAULT_PER_PAGE, 15)

    def test_parse_page_falls_back_safely(self):
        self.assertEqual(parse_page("3"), 3)
        self.assertEqual(parse_page("0"), 1)
        self.assertEqual(parse_page("abc"), 1)
        self.assertEqual(parse_page(None), 1)

    def test_parse_per_page_allows_known_sizes(self):
        self.assertEqual(parse_per_page("15"), 15)
        self.assertEqual(parse_per_page("30"), 30)
        self.assertEqual(parse_per_page("50"), 50)
        self.assertEqual(parse_per_page("99"), DEFAULT_PER_PAGE)
        self.assertEqual(parse_per_page("abc"), DEFAULT_PER_PAGE)
        self.assertEqual(parse_per_page(None), DEFAULT_PER_PAGE)

    def test_paginate_list_slices_and_summarizes(self):
        items = list(range(1, 24))
        page_one = paginate_list(items, page=1, per_page=15)

        self.assertEqual(page_one["items"], list(range(1, 16)))
        self.assertEqual(page_one["total"], 23)
        self.assertEqual(page_one["start_index"], 1)
        self.assertEqual(page_one["end_index"], 15)
        self.assertTrue(page_one["has_next"])
        self.assertFalse(page_one["has_prev"])

        page_two = paginate_list(items, page=2, per_page=15)
        self.assertEqual(page_two["items"], list(range(16, 24)))
        self.assertEqual(page_two["start_index"], 16)
        self.assertEqual(page_two["end_index"], 23)
        self.assertFalse(page_two["has_next"])
        self.assertTrue(page_two["has_prev"])

    def test_paginate_list_clamps_out_of_range_page(self):
        items = list(range(10))
        page = paginate_list(items, page=99, per_page=15)
        self.assertEqual(page["page"], 1)
        self.assertEqual(len(page["items"]), 10)

    def test_paginate_list_empty(self):
        page = paginate_list([], page=1, per_page=15)
        self.assertEqual(page["items"], [])
        self.assertEqual(page["total"], 0)
        self.assertEqual(page["start_index"], 0)
        self.assertEqual(page["end_index"], 0)

    def test_build_page_numbers_with_ellipsis(self):
        numbers = build_page_numbers(5, 12, window=1)
        self.assertEqual(numbers[0], 1)
        self.assertIsNone(numbers[1])
        self.assertIn(5, numbers)
        self.assertEqual(numbers[-1], 12)


if __name__ == "__main__":
    unittest.main()
