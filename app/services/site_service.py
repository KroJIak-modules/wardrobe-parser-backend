from datetime import datetime

from sqlalchemy.orm import Session

from app.repositories.site_repository import SiteRepository


class SiteService:
    @staticmethod
    def upsert_statuses(db: Session, statuses: list[dict]) -> list[int]:
        updated_ids: list[int] = []
        for item in statuses:
            key = item.get("key")
            if not key:
                continue
            site = SiteRepository.get_by_key(db, key)
            last_status_at = SiteService._parse_datetime(item.get("last_status_at"))
            last_error_at = SiteService._parse_datetime(item.get("last_error_at"))
            if site is None:
                site = SiteRepository.create(
                    db,
                    key=key,
                    name=item.get("name", key),
                    base_url=item.get("base_url"),
                    is_active=item.get("is_active", True),
                    last_status=item.get("last_status"),
                    last_status_at=last_status_at,
                    last_error=item.get("last_error"),
                    last_error_at=last_error_at,
                )
            else:
                SiteRepository.update(
                    db,
                    site,
                    name=item.get("name", site.name),
                    base_url=item.get("base_url", site.base_url),
                    is_active=item.get("is_active", site.is_active),
                    last_status=item.get("last_status", site.last_status),
                    last_status_at=last_status_at or site.last_status_at,
                    last_error=item.get("last_error", site.last_error),
                    last_error_at=last_error_at or site.last_error_at,
                )
            updated_ids.append(site.id)
        db.commit()
        return updated_ids

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
