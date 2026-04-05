"""Base Repository pattern for data access layer."""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy.orm import Session

T = TypeVar("T")


class BaseRepository(Generic[T]):
    """Generic base repository for domain entities."""

    def __init__(self, session: Session, model_class: type[T]):
        self.session = session
        self.model_class = model_class

    def create(self, **kwargs) -> T:
        entity = self.model_class(**kwargs)
        self.session.add(entity)
        return entity

    def get_by_id(self, entity_id: int) -> T | None:
        return self.session.query(self.model_class).filter(self.model_class.id == entity_id).first()

    def query(self):
        return self.session.query(self.model_class)

    def flush(self) -> None:
        self.session.flush()
