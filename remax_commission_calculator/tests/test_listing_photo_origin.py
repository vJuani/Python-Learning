"""Listing photo origin guards: never feed Marketing IA output back as a listing photo."""

from __future__ import annotations

import unittest

from modules.listing_photo_origin import (
    eligible_listing_photos,
    is_generated_marketing_asset,
    is_original_listing_photo,
    photo_source_type,
    source_priority,
)
from modules.marketing_photo_selector import select_photos_for_item


class ListingPhotoOriginTests(unittest.TestCase):
    def test_rejects_campaign_and_html_render_paths(self):
        campaign = {
            "id": 1,
            "is_cover": True,
            "storage_key": "tmp/marketing_html_render/italia_1341_v3.png",
        }
        style_ref = {
            "id": 2,
            "original_url": "https://example.com/static/marketing_style_references/modern_commercial_v2_reference.png",
        }
        source_flag = {"id": 3, "source": "marketing", "storage_key": "hero.jpg"}
        self.assertTrue(is_generated_marketing_asset(campaign))
        self.assertTrue(is_generated_marketing_asset(style_ref))
        self.assertTrue(is_generated_marketing_asset(source_flag))
        self.assertEqual(photo_source_type(campaign), "generated_marketing_asset")

    def test_priority_original_over_thumbnail_over_generated(self):
        original = {
            "id": 10,
            "source": "manual",
            "storage_key": "organizations/1/properties/2/media/facade.jpg",
            "storage_strategy": "managed_copy",
            "is_cover": True,
        }
        remote = {
            "id": 11,
            "source": "redremax",
            "original_url": "https://redremax-images.s3.amazonaws.com/listings/living.jpg",
            "storage_strategy": "remote_reference",
        }
        cached = {
            "id": 12,
            "source": "redremax",
            "storage_key": "organizations/1/properties/2/media/cached.jpg",
            "storage_strategy": "managed_copy",
        }
        thumb = {
            "id": 13,
            "source": "redremax",
            "original_url": "https://redremax-images.s3.amazonaws.com/listings/thumb/kitchen.jpg",
            "url_kind": "thumbnail",
        }
        generated = {
            "id": 14,
            "is_cover": True,
            "storage_key": "tmp/marketing_html_render/_italia_assets/hero.jpg",
        }
        self.assertEqual(photo_source_type(original), "original_property_photo")
        self.assertEqual(photo_source_type(remote), "external_listing_original")
        self.assertEqual(photo_source_type(cached), "local_cached_original")
        self.assertEqual(photo_source_type(thumb), "thumbnail")
        self.assertGreater(source_priority(original), source_priority(remote))
        self.assertGreater(source_priority(remote), source_priority(cached))
        self.assertGreater(source_priority(cached), source_priority(thumb))
        self.assertTrue(is_original_listing_photo(original))
        self.assertTrue(is_original_listing_photo(remote))
        self.assertTrue(is_original_listing_photo(cached))
        self.assertFalse(is_original_listing_photo(thumb))
        self.assertFalse(is_original_listing_photo(generated))
        chosen = select_photos_for_item(
            [generated, thumb, cached, remote, original],
            fmt="post",
            index=0,
        )
        self.assertTrue(chosen)
        self.assertEqual(chosen[0]["id"], 10)
        self.assertFalse(any(is_generated_marketing_asset(item) for item in chosen))
        self.assertNotIn(14, [item["id"] for item in chosen])

    def test_eligible_drops_generated_keeps_listing(self):
        rows = [
            {"id": 1, "source": "campaign", "storage_key": "flyer.png", "is_cover": True},
            {"id": 2, "storage_key": "living.jpg", "label": "living"},
        ]
        eligible = eligible_listing_photos(rows)
        self.assertEqual([item["id"] for item in eligible], [2])

    def test_prefers_original_over_thumbnail_variant(self):
        from modules.listing_photo_origin import prefer_highest_resolution_media, source_variant

        thumb = {
            "id": 1,
            "external_media_id": "photo-a",
            "url_kind": "thumbnail",
            "width": 400,
            "height": 300,
            "original_url": "https://cdn.example/listings/thumb/a.jpg",
        }
        original = {
            "id": 2,
            "external_media_id": "photo-a",
            "url_kind": "original",
            "source": "manual",
            "storage_key": "organizations/1/properties/2/media/a.jpg",
            "storage_strategy": "managed_copy",
            "width": 2400,
            "height": 1800,
            "original_url": "https://cdn.example/listings/a.jpg",
        }
        chosen = prefer_highest_resolution_media([thumb, original])
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]["id"], 2)
        self.assertEqual(source_variant(chosen[0]), "original")


if __name__ == "__main__":
    unittest.main()
