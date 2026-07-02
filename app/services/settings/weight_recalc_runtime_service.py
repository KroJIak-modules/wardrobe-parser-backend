from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.repositories.weight_recalc_runtime import WeightRecalcRuntimeRepository
from app.schemas.admin_settings import WeightRecalcStatusResponse


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WeightRecalcRuntimeService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = WeightRecalcRuntimeRepository(db)

    @staticmethod
    def _serialize_datetime(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()

    def serialize(self) -> WeightRecalcStatusResponse:
        state = self.repo.get_or_create_state()
        status = str(getattr(state, "status", "") or "idle").strip().lower() or "idle"
        total_products = max(0, int(getattr(state, "total_products", 0) or 0))
        processed_products = max(0, int(getattr(state, "processed_products", 0) or 0))
        return WeightRecalcStatusResponse(
            status=status,
            is_running=status in {"queued", "running"},
            queued_at=self._serialize_datetime(getattr(state, "queued_at", None)),
            started_at=self._serialize_datetime(getattr(state, "started_at", None)),
            finished_at=self._serialize_datetime(getattr(state, "finished_at", None)),
            last_error=str(getattr(state, "last_error", "") or "") or None,
            total_products=total_products,
            processed_products=min(processed_products, total_products) if total_products > 0 else processed_products,
        )

    def mark_queued(self, *, total_products: int) -> WeightRecalcStatusResponse:
        now = _utcnow()
        state = self.repo.get_or_create_state(for_update=True)
        state.status = "queued"
        state.queued_at = now
        state.started_at = None
        state.finished_at = None
        state.last_error = None
        state.total_products = max(0, int(total_products))
        state.processed_products = 0
        self.db.commit()
        return self.serialize()

    def try_mark_queued(self, *, total_products: int) -> tuple[WeightRecalcStatusResponse, bool]:
        now = _utcnow()
        state = self.repo.get_or_create_state(for_update=True)
        current_status = str(getattr(state, "status", "") or "idle").strip().lower() or "idle"
        if current_status in {"queued", "running"}:
            self.db.commit()
            return self.serialize(), False
        state.status = "queued"
        state.queued_at = now
        state.started_at = None
        state.finished_at = None
        state.last_error = None
        state.total_products = max(0, int(total_products))
        state.processed_products = 0
        self.db.commit()
        return self.serialize(), True

    def mark_running(self) -> WeightRecalcStatusResponse:
        now = _utcnow()
        state = self.repo.get_or_create_state(for_update=True)
        if str(getattr(state, "status", "") or "idle").strip().lower() == "idle":
            state.queued_at = now
        state.status = "running"
        state.started_at = state.started_at or now
        state.finished_at = None
        self.db.commit()
        return self.serialize()

    def mark_retryable_error(self, *, message: str) -> WeightRecalcStatusResponse:
        state = self.repo.get_or_create_state(for_update=True)
        if str(getattr(state, "status", "") or "idle").strip().lower() in {"queued", "running"}:
            state.status = "queued"
        state.last_error = str(message or "").strip()[:4000] or None
        self.db.commit()
        return self.serialize()

    def mark_failed(self, *, message: str) -> WeightRecalcStatusResponse:
        now = _utcnow()
        state = self.repo.get_or_create_state(for_update=True)
        state.status = "idle"
        state.finished_at = now
        state.last_error = str(message or "").strip()[:4000] or None
        self.db.commit()
        return self.serialize()

    def advance_after_batch(self, *, processed_count: int, has_more_work: bool) -> WeightRecalcStatusResponse:
        now = _utcnow()
        state = self.repo.get_or_create_state(for_update=True)
        total_products = max(0, int(getattr(state, "total_products", 0) or 0))
        next_processed = max(0, int(getattr(state, "processed_products", 0) or 0)) + max(0, int(processed_count))
        state.processed_products = min(next_processed, total_products) if total_products > 0 else next_processed
        if has_more_work:
            state.status = "running"
            state.started_at = state.started_at or now
            state.finished_at = None
        else:
            state.status = "idle"
            state.finished_at = now
            if total_products > 0:
                state.processed_products = total_products
        self.db.commit()
        return self.serialize()
