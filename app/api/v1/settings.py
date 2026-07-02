"""API endpoints for parser/admin settings."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.admin_settings import (
    AdminUiSettingsResponse,
    AdminUiSettingsUpdateRequest,
    FilterWeightRuleResponse,
    FilterWeightRuleUpdateRequest,
    PricingSettingsResponse,
    PricingSettingsUpdateRequest,
    PricingSupplierCreateRequest,
    PricingSupplierResponse,
    PricingSupplierUpdateRequest,
    SettingsTransferPayload,
    SettingsTransferResponse,
    WeightMissingProductResponse,
    WeightRecalcStartResponse,
    WeightRecalcStatusResponse,
    WeightRuleCreateRequest,
    WeightRuleKeywordRequest,
    WeightRuleResponse,
    WeightRuleUpdateRequest,
)
from app.services.settings.filter_weight_rule_service import FilterWeightRuleService
from app.services.settings.pricing_service import PricingSettingsService
from app.services.settings.settings_transfer_service import SettingsTransferService
from app.services.settings.weight_rule_service import WeightRuleService
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.site_catalog_sort_price_service import SiteCatalogSortPriceService

router = APIRouter(prefix="/settings", tags=["settings"])
LOGGER = logging.getLogger(__name__)


@router.get("/pricing", response_model=PricingSettingsResponse, dependencies=[Depends(require_permission("control.pricing.read"))])
def get_pricing_settings(db: Session = Depends(get_db)):
    return PricingSettingsService(db).get_settings(refresh_bybit=False)


@router.patch("/pricing", response_model=PricingSettingsResponse, dependencies=[Depends(require_permission("control.pricing.edit"))])
def update_pricing_settings(payload: PricingSettingsUpdateRequest, db: Session = Depends(get_db)):
    return PricingSettingsService(db).update_settings(payload)


@router.get("/admin-ui", response_model=AdminUiSettingsResponse, dependencies=[Depends(require_permission("control.settings.read"))])
def get_admin_ui_settings(db: Session = Depends(get_db)):
    return PricingSettingsService(db).get_admin_ui_settings()


@router.patch("/admin-ui", response_model=AdminUiSettingsResponse, dependencies=[Depends(require_permission("control.settings.edit"))])
def update_admin_ui_settings(payload: AdminUiSettingsUpdateRequest, db: Session = Depends(get_db)):
    return PricingSettingsService(db).update_admin_ui_settings(payload)




@router.patch("/pricing/suppliers/{supplier_id}", response_model=PricingSupplierResponse, dependencies=[Depends(require_permission("control.pricing.edit"))])
def update_pricing_supplier(supplier_id: int, payload: PricingSupplierUpdateRequest, db: Session = Depends(get_db)):
    return PricingSettingsService(db).update_supplier(supplier_id=supplier_id, payload=payload)


@router.post("/pricing/suppliers", response_model=PricingSupplierResponse, dependencies=[Depends(require_permission("control.pricing.edit"))])
def create_pricing_supplier(payload: PricingSupplierCreateRequest, db: Session = Depends(get_db)):
    return PricingSettingsService(db).create_supplier(payload)


@router.delete("/pricing/suppliers/{supplier_id}", dependencies=[Depends(require_permission("control.pricing.edit"))])
def delete_pricing_supplier(supplier_id: int, db: Session = Depends(get_db)):
    return PricingSettingsService(db).delete_supplier(supplier_id)


@router.get("/weight-rules", response_model=list[WeightRuleResponse], dependencies=[Depends(require_permission("control.weight.read"))])
def list_weight_rules(db: Session = Depends(get_db)):
    try:
        return WeightRuleService(db).list_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules, returning empty list")
        return []


@router.get("/weight-rules/missing-products", response_model=list[WeightMissingProductResponse], dependencies=[Depends(require_permission("control.weight.read"))])
def list_missing_weight_products(limit: int = 500, offset: int = 0, db: Session = Depends(get_db)):
    try:
        return WeightRuleService(db).list_missing_weight_products(limit=limit, offset=offset)
    except Exception:
        LOGGER.exception("Failed to load missing weight products, returning empty list")
        return []


@router.get("/filter-weight-rules", response_model=list[FilterWeightRuleResponse], dependencies=[Depends(require_permission("control.weight.read"))])
def list_filter_weight_rules(db: Session = Depends(get_db)):
    return FilterWeightRuleService(db).list_mappings()


@router.patch("/filter-weight-rules", response_model=list[FilterWeightRuleResponse], dependencies=[Depends(require_permission("control.weight.edit"))])
def update_filter_weight_rules(payload: FilterWeightRuleUpdateRequest, db: Session = Depends(get_db)):
    return FilterWeightRuleService(db).update_mappings(payload)


@router.get("/weight-rules/recalculate-status", response_model=WeightRecalcStatusResponse, dependencies=[Depends(require_permission("control.weight.read"))])
def get_weight_rules_recalculation_status(db: Session = Depends(get_db)):
    return WeightRuleService(db).get_recalculation_status()


@router.post("/weight-rules/recalculate", response_model=WeightRecalcStartResponse, dependencies=[Depends(require_permission("control.weight.edit"))])
def start_weight_rules_recalculation(db: Session = Depends(get_db)):
    queued, status_payload, started = WeightRuleService(db).start_full_recalculation()
    return {"ok": True, "queued": queued, "started": started, "status": status_payload.model_dump()}


@router.post("/weight-rules", response_model=WeightRuleResponse, dependencies=[Depends(require_permission("control.weight.edit"))])
def create_weight_rule(payload: WeightRuleCreateRequest, db: Session = Depends(get_db)):
    return WeightRuleService(db).create_rule(payload)


@router.patch("/weight-rules/{rule_id}", response_model=WeightRuleResponse, dependencies=[Depends(require_permission("control.weight.edit"))])
def update_weight_rule(rule_id: int, payload: WeightRuleUpdateRequest, db: Session = Depends(get_db)):
    return WeightRuleService(db).update_rule(rule_id, payload)


@router.delete("/weight-rules/{rule_id}", dependencies=[Depends(require_permission("control.weight.edit"))])
def delete_weight_rule(rule_id: int, db: Session = Depends(get_db)):
    return WeightRuleService(db).delete_rule(rule_id)


@router.post("/weight-rules/{rule_id}/keywords", dependencies=[Depends(require_permission("control.weight.edit"))])
def add_weight_rule_keyword(rule_id: int, payload: WeightRuleKeywordRequest, db: Session = Depends(get_db)):
    return WeightRuleService(db).add_keyword(rule_id, payload)


@router.delete("/weight-rules/{rule_id}/keywords/{keyword}", dependencies=[Depends(require_permission("control.weight.edit"))])
def remove_weight_rule_keyword(rule_id: int, keyword: str, db: Session = Depends(get_db)):
    return WeightRuleService(db).remove_keyword(rule_id, keyword)


@router.get("/export", response_model=SettingsTransferPayload, dependencies=[Depends(require_permission("control.settings.read"))])
def export_settings(db: Session = Depends(get_db)):
    return SettingsTransferService(db).export_payload()


@router.post("/import", response_model=SettingsTransferResponse, dependencies=[Depends(require_permission("control.settings.edit"))])
def import_settings(payload: SettingsTransferPayload, db: Session = Depends(get_db)):
    response = SettingsTransferService(db).import_payload(payload)
    SiteCatalogSortPriceService(db).enqueue_all_active_products()
    return response


@router.post("/reset", response_model=SettingsTransferResponse, dependencies=[Depends(require_permission("control.settings.edit"))])
def reset_settings(db: Session = Depends(get_db)):
    response = SettingsTransferService(db).reset_all()
    SiteCatalogSortPriceService(db).enqueue_all_active_products()
    return response
