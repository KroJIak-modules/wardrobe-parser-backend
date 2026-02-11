from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories.site_repository import SiteRepository
from app.schemas.site import SiteCreate, SiteResponse, SiteStatusUpdate, SiteUpdate
from app.services.site_service import SiteService
from app.utils.service_auth import verify_service_token

router = APIRouter()


@router.get("/", response_model=list[SiteResponse], summary="List sites")
def list_sites(db: Session = Depends(get_db)) -> list[SiteResponse]:
    sites = SiteRepository.list_all(db)
    return [SiteResponse.model_validate(site) for site in sites]


@router.post("/", response_model=SiteResponse, status_code=201, summary="Create site")
def create_site(payload: SiteCreate, db: Session = Depends(get_db)) -> SiteResponse:
    existing = SiteRepository.get_by_key(db, payload.key)
    if existing:
        raise HTTPException(status_code=409, detail="Site already exists")
    site = SiteRepository.create(db, **payload.model_dump())
    db.commit()
    return SiteResponse.model_validate(site)


@router.put("/{site_id}", response_model=SiteResponse, summary="Update site")
def update_site(site_id: int, payload: SiteUpdate, db: Session = Depends(get_db)) -> SiteResponse:
    site = SiteRepository.get_by_id(db, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    updated = SiteRepository.update(db, site, **payload.model_dump(exclude_none=True))
    db.commit()
    return SiteResponse.model_validate(updated)


@router.delete("/{site_id}", status_code=204, summary="Delete site")
def delete_site(site_id: int, db: Session = Depends(get_db)) -> None:
    site = SiteRepository.get_by_id(db, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    SiteRepository.soft_delete(db, site)
    db.commit()


@router.post(
    "/status",
    summary="Upsert site statuses",
    dependencies=[Depends(verify_service_token)],
)
def upsert_status(payload: list[SiteStatusUpdate], db: Session = Depends(get_db)) -> dict:
    site_ids = SiteService.upsert_statuses(db, [item.model_dump() for item in payload])
    return {"updated": len(site_ids)}
