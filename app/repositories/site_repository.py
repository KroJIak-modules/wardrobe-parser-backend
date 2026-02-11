from typing import Optional

from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.models.site import Site


class SiteRepository:
    @staticmethod
    def get_by_id(db: Session, site_id: int, include_deleted: bool = False) -> Optional[Site]:
        query = db.query(Site).filter(Site.id == site_id)
        if not include_deleted:
            query = query.filter(Site.is_deleted == False)
        return query.first()

    @staticmethod
    def get_by_key(db: Session, key: str, include_deleted: bool = False) -> Optional[Site]:
        query = db.query(Site).filter(Site.key == key)
        if not include_deleted:
            query = query.filter(Site.is_deleted == False)
        return query.first()

    @staticmethod
    def list_all(db: Session, include_deleted: bool = False) -> list[Site]:
        query = db.query(Site)
        if not include_deleted:
            query = query.filter(Site.is_deleted == False)
        return query.order_by(Site.id.asc()).all()

    @staticmethod
    def create(db: Session, **kwargs) -> Site:
        site = Site(**kwargs)
        db.add(site)
        db.flush()
        db.refresh(site)
        return site

    @staticmethod
    def update(db: Session, site: Site, **kwargs) -> Site:
        for key, value in kwargs.items():
            if hasattr(site, key):
                setattr(site, key, value)
        db.flush()
        db.refresh(site)
        return site

    @staticmethod
    def soft_delete(db: Session, site: Site) -> None:
        site.is_deleted = True
        site.deleted_at = utcnow()
        db.flush()
