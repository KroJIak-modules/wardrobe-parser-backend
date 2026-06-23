from app.services.catalog.product_title_service import ProductTitleService


def test_display_title_strips_designer_prefix_when_title_contains_product_name() -> None:
    assert ProductTitleService.display_title(
        source_title="COMME DES GARCONS Parfums Amazingreen Perfume 100ml",
        source_designer_name="COMME DES GARCONS Parfums",
    ) == "Amazingreen Perfume 100ml"


def test_display_title_keeps_original_when_title_equals_designer_name() -> None:
    assert ProductTitleService.display_title(
        source_title="Chrome Hearts",
        source_designer_name="Chrome Hearts",
    ) == "Chrome Hearts"


def test_display_title_uses_category_when_source_title_is_only_designer_name() -> None:
    assert ProductTitleService.display_title(
        source_title="Rick Owens",
        source_designer_name="Rick Owens",
        source_category_name="Wool Hoodie",
    ) == "Wool Hoodie"
