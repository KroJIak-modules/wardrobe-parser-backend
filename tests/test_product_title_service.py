from app.services.catalog.product_title_service import ProductTitleService


def test_display_title_removes_all_punctuation_after_designer_prefix() -> None:
    assert ProductTitleService.display_title(
        source_title="POST ARCHIVE FACTION (PAF) 8.0 Hoodie Center Brown",
        source_designer_name="POST ARCHIVE FACTION (PAF)",
    ) == "8.0 Hoodie Center Brown"


def test_display_title_preserves_meaningful_parentheses_in_product_name() -> None:
    assert ProductTitleService.display_title(
        source_title="Vivien Ramsay Boy Brief (3 Pack) White",
        source_designer_name="Vivien Ramsay",
    ) == "Boy Brief (3 Pack) White"


def test_public_title_preserves_size_and_pack_count_parentheses() -> None:
    assert ProductTitleService.public_title(
        source_title="Twin Set (S-M)",
        source_designer_name=None,
        clean=True,
    ) == "Twin Set (S-M)"
    assert ProductTitleService.public_title(
        source_title="Boy Brief (3 Pack) White",
        source_designer_name=None,
        clean=True,
    ) == "Boy Brief (3 Pack) White"
