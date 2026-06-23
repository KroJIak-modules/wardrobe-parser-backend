from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import FilterAssignmentRuntimeState, ProductFilterAssignment


class CatalogFilterAssignmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_runtime_state(self, *, for_update: bool = False) -> FilterAssignmentRuntimeState | None:
        query = self.session.query(FilterAssignmentRuntimeState).filter(FilterAssignmentRuntimeState.id == 1)
        if for_update:
            query = query.with_for_update()
        return query.one_or_none()

    def get_or_create_runtime_state(self, *, for_update: bool = False) -> FilterAssignmentRuntimeState:
        state = self.get_runtime_state(for_update=for_update)
        if state is not None:
            return state
        state = FilterAssignmentRuntimeState(id=1)
        self.session.add(state)
        self.session.flush()
        if for_update:
            return self.get_runtime_state(for_update=True) or state
        return state

    def delete_revision(self, revision: int) -> None:
        (
            self.session.query(ProductFilterAssignment)
            .filter(ProductFilterAssignment.revision == int(revision))
            .delete(synchronize_session=False)
        )

    def delete_revision_for_product_ids(self, *, revision: int, product_ids: list[int]) -> None:
        if not product_ids:
            return
        (
            self.session.query(ProductFilterAssignment)
            .filter(ProductFilterAssignment.revision == int(revision))
            .filter(ProductFilterAssignment.product_id.in_(product_ids))
            .delete(synchronize_session=False)
        )

    def delete_other_revisions(self, *, keep_revision: int) -> None:
        (
            self.session.query(ProductFilterAssignment)
            .filter(ProductFilterAssignment.revision != int(keep_revision))
            .delete(synchronize_session=False)
        )
