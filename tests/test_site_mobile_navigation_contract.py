from __future__ import annotations

from app.core.database import SessionLocal
from app.services.catalog.site_query_service import SiteQueryService


def _column_label(column) -> str:
    return column.title.label if column.title is not None else (column.entries[0].label if column.entries else column.id)


def test_mobile_navigation_uses_gender_scoped_desktop_columns_and_mobile_pairs() -> None:
    db = SessionLocal()
    try:
        service = SiteQueryService(db)
        payload = service.navigation()
        filters_by_id = service._flatten_admin_filter_nodes(service.preview.taxonomy_state["filters"])

        for gender in ("men", "women"):
            desktop_menu = payload.desktop_menus.get(gender)
            if desktop_menu is None:
                assert gender not in payload.mobile_menu.groups_by_gender
                continue

            columns_by_root_id = {
                root_id: column
                for column in desktop_menu.columns
                if (root_id := service._filter_node_id_from_entry_id(column.id, "filter-")) is not None
            }
            expected_groups = []
            used_column_ids: set[str] = set()
            for column in desktop_menu.columns:
                if column.id in used_column_ids or (not column.entries and column.title is None):
                    continue
                root_id = service._filter_node_id_from_entry_id(column.id, "filter-")
                pair_id = int(filters_by_id.get(root_id, {}).get("mobile_pair_root_id") or 0)
                reciprocal_pair_id = int(filters_by_id.get(pair_id, {}).get("mobile_pair_root_id") or 0)
                paired_column = columns_by_root_id.get(pair_id) if reciprocal_pair_id == root_id else None
                if paired_column is not None and paired_column.id not in used_column_ids:
                    expected_groups.append({
                        "id": f"mobile-pair:{column.id}:{paired_column.id}",
                        "label": f"{_column_label(column)} и {_column_label(paired_column)}",
                        "entries": [entry.model_dump() for entry in [*column.entries, *paired_column.entries]],
                    })
                    used_column_ids.update({column.id, paired_column.id})
                    continue
                expected_groups.append({
                    "id": column.id,
                    "label": _column_label(column),
                    "entries": [entry.model_dump() for entry in column.entries],
                })
                used_column_ids.add(column.id)

            assert [group.model_dump() for group in payload.mobile_menu.groups_by_gender.get(gender, [])] == expected_groups
    finally:
        db.rollback()
        db.close()
