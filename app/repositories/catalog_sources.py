from __future__ import annotations

from sqlalchemy.orm import Session, joinedload

from app.core.source_identity import normalize_host
from app.models import Source, SourceSetting, SourceSyncState


class CatalogSourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[Source]:
        return (
            self.session.query(Source)
            .options(joinedload(Source.setting), joinedload(Source.sync_state))
            .order_by(Source.name.asc(), Source.id.asc())
            .all()
        )

    def get_by_id(self, source_id: int) -> Source | None:
        return (
            self.session.query(Source)
            .options(joinedload(Source.setting), joinedload(Source.sync_state))
            .filter(Source.id == int(source_id))
            .one_or_none()
        )

    def get_by_key(self, source_key: str) -> Source | None:
        normalized = str(source_key or "").strip().lower()
        if not normalized:
            return None
        return (
            self.session.query(Source)
            .options(joinedload(Source.setting), joinedload(Source.sync_state))
            .filter(Source.key == normalized)
            .one_or_none()
        )

    def create(self, *, key: str, name: str, base_url: str) -> Source:
        entity = Source(key=key, name=name, base_url=base_url, host_normalized=normalize_host(base_url))
        self.session.add(entity)
        self.session.flush()
        return entity

    def count_by_supplier_id(self, supplier_id: int) -> int:
        return (
            self.session.query(SourceSetting)
            .filter(SourceSetting.supplier_id == int(supplier_id))
            .count()
        )

    def ensure_setting(self, source: Source) -> SourceSetting:
        if source.setting is not None:
            return source.setting
        setting = SourceSetting(source_id=int(source.id))
        self.session.add(setting)
        self.session.flush()
        source.setting = setting
        return setting

    def ensure_sync_state(self, source: Source) -> SourceSyncState:
        if source.sync_state is not None:
            return source.sync_state
        sync_state = SourceSyncState(source_id=int(source.id))
        self.session.add(sync_state)
        self.session.flush()
        source.sync_state = sync_state
        return sync_state
