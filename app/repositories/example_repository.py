from sqlalchemy.orm import Session

from app.core.database import utcnow
from service.app.models.example import Example


class ExampleRepository:
    @staticmethod
    def get_all(
        db: Session,
        # ...
        order_by: str = "created_at",
        include_deleted: bool = False
    ) -> tuple[list[Example], int]:
        query = db.query(Example)
        if not include_deleted:
            query = query.filter(Example.is_deleted == False)
        total = query.count()
        order_col = getattr(Example, order_by, Example.created_at)
        items = query.order_by(order_col.desc()).all()
        return items, total

    @staticmethod
    def soft_delete(db: Session, example: Example) -> None:
        example.is_deleted = True
        example.deleted_at = utcnow()
        db.flush()

    @staticmethod
    def get_by_id(db: Session, example_id: int, include_deleted: bool = False) -> Example | None:
        q = db.query(Example).filter(Example.id == example_id)
        if not include_deleted:
            q = q.filter(Example.is_deleted == False)
        return q.first()

    @staticmethod
    def create(db: Session, **kwargs) -> Example:
        example = Example(**kwargs)
        db.add(example)
        db.flush()
        db.refresh(example)
        return example

    @staticmethod
    def update(db: Session, example: Example, **kwargs) -> Example:
        for key, value in kwargs.items():
            if hasattr(example, key):
                setattr(example, key, value)
        db.flush()
        db.refresh(example)
        return example

    @staticmethod
    def count_all(db: Session, include_deleted: bool = False) -> int:
        query = db.query(Example)
        if not include_deleted:
            query = query.filter(Example.is_deleted == False)
        return query.count()