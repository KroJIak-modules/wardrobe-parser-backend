from types import SimpleNamespace

from app.services.catalog.source_gender_policy import (
    effective_gender_for_product,
    source_gender_overrides,
)


def test_source_override_is_effective_without_mutating_product_value() -> None:
    product = SimpleNamespace(
        gender="unisex",
        primary_listing=SimpleNamespace(source=SimpleNamespace(key="misssixty.com")),
    )

    assert source_gender_overrides()["misssixty.com"] == "female"
    assert effective_gender_for_product(product) == "female"
    assert product.gender == "unisex"


def test_source_without_override_keeps_stored_gender() -> None:
    product = SimpleNamespace(
        gender="male",
        primary_listing=SimpleNamespace(source=SimpleNamespace(key="example.test")),
    )

    assert effective_gender_for_product(product) == "male"
    assert product.gender == "male"
