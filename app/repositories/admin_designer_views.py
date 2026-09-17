"""Data access for per-admin designer editor view tracking."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import AdminBrandView, AdminDesignerView, Designer, DesignerSourceName


class AdminDesignerViewRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def filter_viewed_source_names(self, *, admin_user_id: int) -> set[str]:
        rows = (
            self.session.query(AdminBrandView.source_name)
            .filter(AdminBrandView.admin_user_id == int(admin_user_id))
            .all()
        )
        return {str(row[0]) for row in rows}

    def list_designer_viewed_at(self, *, admin_user_id: int) -> dict[int, datetime]:
        rows = (
            self.session.query(AdminDesignerView.designer_id, AdminDesignerView.viewed_at)
            .filter(AdminDesignerView.admin_user_id == int(admin_user_id))
            .all()
        )
        return {int(designer_id): viewed_at for designer_id, viewed_at in rows}

    def mark_brand_viewed(self, *, admin_user_id: int, source_name: str) -> None:
        self.session.execute(
            insert(AdminBrandView)
            .values(admin_user_id=int(admin_user_id), source_name=str(source_name))
            .on_conflict_do_nothing()
        )

    def mark_designer_viewed(self, *, admin_user_id: int, designer_id: int) -> None:
        # Single-item view refreshes the timestamp so a stale NEW flag clears.
        self.session.execute(
            insert(AdminDesignerView)
            .values(admin_user_id=int(admin_user_id), designer_id=int(designer_id))
            .on_conflict_do_update(
                index_elements=[AdminDesignerView.admin_user_id, AdminDesignerView.designer_id],
                set_={"viewed_at": func.now()},
            )
        )

    def mark_all_brands_viewed(self, *, admin_user_id: int) -> int:
        statement = (
            insert(AdminBrandView)
            .from_select(
                ["admin_user_id", "source_name"],
                select(
                    literal(int(admin_user_id)).label("admin_user_id"),
                    DesignerSourceName.source_name.label("source_name"),
                ),
            )
            .on_conflict_do_nothing()
        )
        result = self.session.execute(statement)
        return int(getattr(result, "rowcount", 0) or 0)

    def mark_all_designers_viewed(self, *, admin_user_id: int) -> int:
        # Designers use viewed_at-based NEW semantics (a brand arriving after the
        # last review re-marks them), so the reset refreshes only rows that are
        # stale relative to their mapped brand arrivals.
        new_brand_arrival = (
            select(DesignerSourceName.designer_id)
            .where(DesignerSourceName.designer_id == AdminDesignerView.designer_id)
            .where(DesignerSourceName.created_at > AdminDesignerView.viewed_at)
            .exists()
        )
        statement = (
            insert(AdminDesignerView)
            .from_select(
                ["admin_user_id", "designer_id"],
                select(
                    literal(int(admin_user_id)).label("admin_user_id"),
                    Designer.id.label("designer_id"),
                ),
            )
            .on_conflict_do_update(
                index_elements=[AdminDesignerView.admin_user_id, AdminDesignerView.designer_id],
                set_={"viewed_at": func.now()},
                where=new_brand_arrival,
            )
        )
        result = self.session.execute(statement)
        return int(getattr(result, "rowcount", 0) or 0)
