from types import SimpleNamespace

from app.schemas.site import SiteProductSourceResponse
from app.services.catalog.product_query_service import ProductQueryService


def _listing(listing_id: int, url: str, *, show_images: bool = True):
    return SimpleNamespace(
        id=listing_id,
        source=SimpleNamespace(setting=SimpleNamespace(show_images=show_images)),
        images=[SimpleNamespace(position=1, url=url)],
    )


def test_variant_source_gallery_uses_its_own_listing():
    product = SimpleNamespace(gallery_images=[])
    first = _listing(1, "https://example.test/first.jpg")
    second = _listing(2, "https://example.test/second.jpg")

    first_images = ProductQueryService._gallery_state(product, first)["display_image_urls"]
    second_images = ProductQueryService._gallery_state(product, second)["display_image_urls"]

    assert first_images == ["https://example.test/first.jpg"]
    assert second_images == ["https://example.test/second.jpg"]
    assert SiteProductSourceResponse(id=1, name="source", image_urls=second_images).image_urls == second_images


def test_variant_gallery_respects_hidden_source_images():
    product = SimpleNamespace(gallery_images=[])
    listing = _listing(1, "https://example.test/private.jpg", show_images=False)

    assert ProductQueryService._gallery_state(product, listing)["display_image_urls"] == []
