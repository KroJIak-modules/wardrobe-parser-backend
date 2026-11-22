from __future__ import annotations

from app.core.database import SessionLocal
from app.services.catalog.admin_editor_service import AdminEditorService
from app.services.catalog.showcase_state_cache import state_cache


def test_taxonomy_state_is_cached_between_requests() -> None:
    """Таксономия витрины не пересобирается в каждом запросе.

    Сборка стоит около двух с половиной секунд, а витрина запрашивает её
    в каждом HTTP-запросе — главная страница делает это пять раз параллельно.
    """
    db = SessionLocal()
    try:
        service = AdminEditorService(db)
        state_cache().invalidate()

        first = service.list_taxonomy_editor_state()
        second = service.list_taxonomy_editor_state()

        assert first["filters"] is second["filters"], "таксономия должна браться из кэша"
        assert first["categories"] is second["categories"]
    finally:
        db.rollback()
        db.close()


def test_designer_directory_is_not_served_from_cache() -> None:
    """Новый бренд должен появляться в каталоге дизайнеров сразу.

    Счётчики по брендам не кэшируются: иначе новый бренд ждал бы истечения
    срока кэша.
    """
    db = SessionLocal()
    try:
        service = AdminEditorService(db)
        state_cache().invalidate()

        # Счётчики по брендам считаются заново для каждого экземпляра
        # сервиса: иначе новый бренд ждал бы истечения срока кэша.
        first = AdminEditorService(db).list_designer_editor_state()
        second = AdminEditorService(db).list_designer_editor_state()

        assert first is not second, "состояние дизайнеров должно считаться заново"

        taxonomy_state = service.list_taxonomy_editor_state()
        assert "designer_directory" in taxonomy_state
    finally:
        db.rollback()
        db.close()
