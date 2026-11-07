from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import AdminAgainView, CatalogPresenceState


class CatalogPresenceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def sync_counts(
        self,
        *,
        entity_kind: str,
        counts: Mapping[str, int],
        known_keys: Iterable[str] = (),
        ignored_keys: Iterable[str] = (),
        emit_reappearance: bool = True,
        complete_snapshot: bool = True,
    ) -> set[str]:
        normalized_kind = str(entity_kind).strip().lower()
        current = {
            str(key).strip(): max(0, int(value))
            for key, value in counts.items()
            if str(key).strip()
        }
        existing_keys = {
            str(key).strip()
            for key in known_keys
            if str(key).strip()
        }
        query = self.session.query(CatalogPresenceState).filter(
            CatalogPresenceState.entity_kind == normalized_kind
        )
        if not complete_snapshot:
            keys = sorted(set(current) | existing_keys)
            if not keys:
                return set()
            query = query.filter(CatalogPresenceState.entity_key.in_(keys))
        stored_rows = query.with_for_update().all()
        existing_keys.update(str(row.entity_key).strip() for row in stored_rows if str(row.entity_key).strip())
        stored = {str(row.entity_key): row for row in stored_rows}
        ignored = {str(key).strip() for key in ignored_keys if str(key).strip()}
        reappeared: set[str] = set()
        now = datetime.now(timezone.utc)
        for key in sorted(set(current) | existing_keys):
            count = current.get(key, 0)
            row = stored.get(key)
            if row is None:
                self.session.add(
                    CatalogPresenceState(
                        entity_kind=normalized_kind,
                        entity_key=key,
                        product_count=count,
                        is_auto_tracked=0 if key in ignored else 1,
                        observed_at=now,
                    )
                )
                continue
            if key in ignored:
                row.is_auto_tracked = 0
                row.product_count = count
                row.observed_at = now
                continue
            if not bool(row.is_auto_tracked):
                row.product_count = count
                row.observed_at = now
                continue
            if emit_reappearance and int(row.product_count or 0) == 0 and count > 0:
                row.last_reappeared_at = now
                reappeared.add(key)
            row.product_count = count
            row.observed_at = now
        self.session.flush()
        return reappeared


class AdminAgainViewRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def filter_again_keys(
        self,
        *,
        admin_user_id: int,
        entity_kind: str,
        entity_keys: Iterable[str],
    ) -> set[str]:
        normalized_kind = str(entity_kind).strip().lower()
        keys = sorted({str(key).strip() for key in entity_keys if str(key).strip()})
        if not keys:
            return set()
        states = (
            self.session.query(CatalogPresenceState.entity_key, CatalogPresenceState.last_reappeared_at)
            .filter(CatalogPresenceState.entity_kind == normalized_kind)
            .filter(CatalogPresenceState.entity_key.in_(keys))
            .filter(CatalogPresenceState.last_reappeared_at.is_not(None))
            .all()
        )
        viewed = {
            str(row[0]): row[1]
            for row in self.session.query(AdminAgainView.entity_key, AdminAgainView.viewed_event_at)
            .filter(AdminAgainView.admin_user_id == int(admin_user_id))
            .filter(AdminAgainView.entity_kind == normalized_kind)
            .filter(AdminAgainView.entity_key.in_(keys))
            .all()
        }
        return {
            str(entity_key)
            for entity_key, event_at in states
            if event_at is not None and (viewed.get(str(entity_key)) is None or event_at > viewed[str(entity_key)])
        }

    def mark_viewed(self, *, admin_user_id: int, entity_kind: str, entity_key: str) -> bool:
        normalized_kind = str(entity_kind).strip().lower()
        normalized_key = str(entity_key).strip()
        if not normalized_key:
            return False
        state = (
            self.session.query(CatalogPresenceState.last_reappeared_at)
            .filter(CatalogPresenceState.entity_kind == normalized_kind)
            .filter(CatalogPresenceState.entity_key == normalized_key)
            .one_or_none()
        )
        if state is None or state[0] is None:
            return False
        self.session.execute(
            insert(AdminAgainView)
            .values(
                admin_user_id=int(admin_user_id),
                entity_kind=normalized_kind,
                entity_key=normalized_key,
                viewed_event_at=state[0],
            )
            .on_conflict_do_update(
                index_elements=[
                    AdminAgainView.admin_user_id,
                    AdminAgainView.entity_kind,
                    AdminAgainView.entity_key,
                ],
                set_={"viewed_event_at": state[0]},
            )
        )
        return True

    def mark_all_viewed(self, *, admin_user_id: int, entity_kind: str) -> int:
        normalized_kind = str(entity_kind).strip().lower()
        states = (
            self.session.query(CatalogPresenceState.entity_key, CatalogPresenceState.last_reappeared_at)
            .filter(CatalogPresenceState.entity_kind == normalized_kind)
            .filter(CatalogPresenceState.last_reappeared_at.is_not(None))
            .all()
        )
        for entity_key, event_at in states:
            self.session.execute(
                insert(AdminAgainView)
                .values(
                    admin_user_id=int(admin_user_id),
                    entity_kind=normalized_kind,
                    entity_key=str(entity_key),
                    viewed_event_at=event_at,
                )
                .on_conflict_do_update(
                    index_elements=[
                        AdminAgainView.admin_user_id,
                        AdminAgainView.entity_kind,
                        AdminAgainView.entity_key,
                    ],
                    set_={"viewed_event_at": event_at},
                )
            )
        return len(states)
