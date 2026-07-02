from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import WeightRecalcRuntimeState


class WeightRecalcRuntimeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_state(self, *, for_update: bool = False) -> WeightRecalcRuntimeState | None:
        query = self.session.query(WeightRecalcRuntimeState).filter(WeightRecalcRuntimeState.id == 1)
        if for_update:
            query = query.with_for_update()
        return query.one_or_none()

    def get_or_create_state(self, *, for_update: bool = False) -> WeightRecalcRuntimeState:
        state = self.get_state(for_update=for_update)
        if state is not None:
            return state
        state = WeightRecalcRuntimeState(id=1)
        self.session.add(state)
        self.session.flush()
        if for_update:
            return self.get_state(for_update=True) or state
        return state
