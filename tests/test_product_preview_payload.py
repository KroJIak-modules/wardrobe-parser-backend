from app.api.v1.products import _preview_payload_from_service_item


def test_preview_payload_defaults_source_probe_to_by_order() -> None:
    payload = _preview_payload_from_service_item(
        {
            "title": "Archive Coat",
            "url": "https://example.com/products/archive-coat",
            "designer": "Carol Christian Poell",
            "category": "Coats",
            "gender": "men",
            "variants": [{"title": "48", "price": 1250.0, "currency": "EUR", "available": True}],
        }
    )

    assert payload["availability_mode"] == "by_order"
    assert payload["orderability_status"] == "orderable"

