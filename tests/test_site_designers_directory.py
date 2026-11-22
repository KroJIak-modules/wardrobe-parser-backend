from __future__ import annotations

from app.core.database import SessionLocal
from app.models import Designer
from app.services.catalog.site_query_service import SiteQueryService


def test_selected_designer_is_always_in_the_directory() -> None:
    """Выбранный дизайнер должен быть в списке, даже если его нет в каталоге.

    Бренд без публичных товаров в каталог дизайнеров не попадает. Но
    выбранного пользователем дизайнера нужно видеть и подчёркивать, иначе
    выбор нельзя ни подтвердить, ни снять.
    """
    db = SessionLocal()
    try:
        service = SiteQueryService(db)

        designer = Designer(
            name="Directory Test Brand",
            slug="directory-test-brand",
            origin_kind="manual",
            is_enabled=True,
        )
        db.add(designer)
        db.flush()
        selected_slug = str(designer.slug)

        base_entries = service.designers().entries
        base_slugs = {entry.slug for entry in base_entries}

        selected_entries = service.designers(selected_slugs=[selected_slug]).entries
        selected_slugs = {entry.slug for entry in selected_entries}

        assert selected_slug in selected_slugs, "выбранный дизайнер должен быть в списке"
        expected = len(base_entries) + (0 if selected_slug in base_slugs else 1)
        assert len(selected_entries) == expected
    finally:
        db.rollback()
        db.close()


def test_unknown_designer_is_not_added_to_the_directory() -> None:
    """Несуществующий slug в список не попадает."""
    db = SessionLocal()
    try:
        service = SiteQueryService(db)

        base_entries = service.designers().entries
        selected_entries = service.designers(selected_slugs=["no-such-designer-here"]).entries

        assert {entry.slug for entry in selected_entries} == {entry.slug for entry in base_entries}
    finally:
        db.rollback()
        db.close()
