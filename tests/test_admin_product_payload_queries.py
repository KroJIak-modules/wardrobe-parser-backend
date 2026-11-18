from __future__ import annotations

from pathlib import Path

from app.core.database import SessionLocal
from app.models import (
    Product,
    ProductListing,
    ProductListingGalleryImage,
    ProductListingImage,
    ProductListingMember,
    ProductListingVariant,
    Source,
)
from app.services.catalog.admin_editor_service import AdminEditorService

GALLERY = 10
VARIANTS = 10
IMAGES = 10
MEMBERSHIPS = 2


def _current_rss_mb() -> float:
    """Текущее потребление памяти процесса в мегабайтах."""
    try:
        pages = int(Path("/proc/self/statm").read_text().split()[1])
    except (OSError, IndexError, ValueError):
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return pages * 4096 / 1024 / 1024


def _make_source(db, key: str = "payload-guard-source") -> Source:
    source = db.query(Source).filter(Source.key == key).one_or_none()
    if source is None:
        source = Source(
            key=key,
            name="Payload Guard",
            base_url=f"https://example.com/{key}",
            base_url_normalized=f"https://example.com/{key}",
            adapter_key="guard",
            parser_config={},
        )
        db.add(source)
        db.flush()
    return source


def _make_product_with_gallery(db, source: Source) -> int:
    listing = ProductListing(
        source_id=source.id,
        external_id="payload-guard",
        url="https://example.com/payload-guard",
        url_normalized="https://example.com/payload-guard",
        host_normalized="example.com",
        handle="payload-guard",
        source_title="Payload Guard",
        source_description_text="",
        source_description_html="",
        source_tags=[],
        source_designer_raw="Payload Guard",
        source_category_raw="dress",
    )
    db.add(listing)
    db.flush()

    product = Product(lifecycle_status="active", gender="female")
    db.add(product)
    db.flush()
    db.add(ProductListingMember(product_id=product.id, listing_id=listing.id))
    db.flush()
    product.primary_listing_id = listing.id
    db.flush()

    listing_images = []
    for position in range(IMAGES):
        image = ProductListingImage(
            listing_id=listing.id,
            position=position,
            url=f"https://example.com/{position}.jpg",
        )
        db.add(image)
        listing_images.append(image)
    for position in range(VARIANTS):
        db.add(
            ProductListingVariant(
                listing_id=listing.id,
                position=position,
                title=f"Variant {position}",
                price_amount=1000 + position,
                currency_code="RUB",
            )
        )
    db.flush()

    for position in range(GALLERY):
        db.add(
            ProductListingGalleryImage(
                product_id=product.id,
                listing_id=listing.id,
                listing_image_id=listing_images[position % IMAGES].id,
                position=position,
                origin_kind="source_image",
                is_hidden=False,
            )
        )
    db.flush()

    for index in range(MEMBERSHIPS - 1):
        extra = ProductListing(
            source_id=source.id,
            external_id=f"payload-guard-extra-{index}",
            url=f"https://example.com/payload-guard-extra-{index}",
            url_normalized=f"https://example.com/payload-guard-extra-{index}",
            host_normalized="example.com",
            handle=f"payload-guard-extra-{index}",
            source_title="Payload Guard Extra",
            source_description_text="",
            source_description_html="",
            source_tags=[],
            source_designer_raw="Payload Guard",
            source_category_raw="dress",
        )
        db.add(extra)
        db.flush()
        for position in range(VARIANTS):
            db.add(
                ProductListingVariant(
                    listing_id=extra.id,
                    position=position,
                    title=f"Extra {position}",
                    price_amount=1000 + position,
                    currency_code="RUB",
                )
            )
        db.flush()
        db.add(ProductListingMember(product_id=product.id, listing_id=extra.id))

    db.flush()
    return int(product.id)


def _cleanup(db) -> None:
    """Убрать фикстуру: настройки цен коммитятся по ходу дела, поэтому
    откат транзакции уже не спасает.
    """
    listing_ids = [
        int(listing_id)
        for (listing_id,) in db.query(ProductListing.id)
        .filter(ProductListing.url_normalized.like("%/payload-guard%"))
        .all()
    ]
    product_ids = [
        int(product_id)
        for (product_id,) in db.query(Product.id)
        .filter(Product.primary_listing_id.in_(listing_ids) if listing_ids else False)
        .all()
    ]
    if product_ids:
        db.query(Product).filter(Product.id.in_(product_ids)).delete(synchronize_session=False)
    if listing_ids:
        db.query(ProductListing).filter(ProductListing.id.in_(listing_ids)).delete(synchronize_session=False)
    db.query(Source).filter(Source.key == "payload-guard-source").delete(synchronize_session=False)
    db.commit()


def test_admin_product_payload_does_not_multiply_rows() -> None:
    """Карточка товара не должна размножать строки запроса.

    Совместный JOIN нескольких коллекций (картинки, варианты, листинги)
    даёт декартово произведение: на товаре с десятью картинками и десятью
    вариантами это тысячи строк на карточку, и процесс бэкенда разрастается
    на сотни мегабайт, а на полном каталоге — до гигабайтов.
    """
    db = SessionLocal()
    try:
        source = _make_source(db)
        product_id = _make_product_with_gallery(db, source)

        service = AdminEditorService(db)
        before = _current_rss_mb()
        payloads = service._admin_product_payload_index([product_id])
        growth_mb = max(0.0, _current_rss_mb() - before)

        assert product_id in payloads
        assert growth_mb < 150, f"карточка товара съела {growth_mb:.0f} МБ памяти"
    finally:
        _cleanup(db)
        db.rollback()
        db.close()
