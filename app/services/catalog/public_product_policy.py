from __future__ import annotations

from sqlalchemy import and_, case, func, literal, not_, select

from app.models import Product, ProductListing, ProductListingGalleryImage, ProductListingImage, SourceSetting


def effective_public_orderability_expression():
    """Return the public listing state for a query joined to primary listing and source settings."""
    primary_listing_has_gallery_scope = select(ProductListingGalleryImage.id).where(
        ProductListingGalleryImage.product_id == Product.id,
        ProductListingGalleryImage.listing_id == Product.primary_listing_id,
    ).exists()
    primary_listing_has_visible_gallery_image = select(ProductListingGalleryImage.id).where(
        ProductListingGalleryImage.product_id == Product.id,
        ProductListingGalleryImage.listing_id == Product.primary_listing_id,
        ProductListingGalleryImage.is_hidden.is_(False),
        (
            ProductListingGalleryImage.image_asset_id.is_not(None)
            | (
                ProductListingGalleryImage.listing_image_id.is_not(None)
                & func.coalesce(SourceSetting.show_images, True).is_(True)
            )
        ),
    ).exists()
    primary_listing_has_source_image = select(ProductListingImage.id).where(
        ProductListingImage.listing_id == Product.primary_listing_id,
    ).exists()
    has_visible_images = case(
        (primary_listing_has_gallery_scope, primary_listing_has_visible_gallery_image),
        else_=and_(
            func.coalesce(SourceSetting.show_images, True).is_(True),
            primary_listing_has_source_image,
        ),
    )
    return case(
        (Product.dedup_status != "independent", literal("unavailable")),
        (Product.site_sort_price_rub.is_(None), literal("unavailable")),
        (not_(has_visible_images), literal("unavailable")),
        else_=func.coalesce(ProductListing.orderability_status, literal("unavailable")),
    )


def public_product_content_condition():
    """Conditions shared by public catalog and designer auto-availability, excluding visibility causes."""
    return and_(
        Product.lifecycle_status == "active",
        Product.dedup_status == "independent",
        effective_public_orderability_expression() == "orderable",
    )


def public_product_condition():
    """The complete public catalog eligibility rule for a product's primary listing."""
    return and_(
        public_product_content_condition(),
        Product.visibility_status == "visible",
    )


def public_product_candidate_condition():
    """Public eligibility excluding only source-brand inclusion, for auto re-enable decisions."""
    return and_(
        public_product_content_condition(),
        Product.is_manually_hidden.is_(False),
        func.coalesce(SourceSetting.hide_auto_added_products, False).is_(False),
    )
