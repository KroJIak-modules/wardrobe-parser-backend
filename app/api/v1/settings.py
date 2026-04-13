"""API endpoints for parser/admin settings."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.parser import (
    PricingSettingsResponse,
    PricingSettingsUpdateRequest,
    PricingSupplierCreateRequest,
    PricingSupplierResponse,
    PricingSupplierUpdateRequest,
    WeightMissingProductResponse,
    WeightRuleCreateRequest,
    WeightRuleKeywordRequest,
    WeightRuleResponse,
    WeightRuleUpdateRequest,
)
from app.services.settings.pricing_service import PricingSettingsService
from app.services.settings.weight_rule_service import WeightRuleService

router = APIRouter(prefix="/settings", tags=["settings"])
LOGGER = logging.getLogger(__name__)


@router.get("/pricing", response_model=PricingSettingsResponse)
def get_pricing_settings(db: Session = Depends(get_db)):
    return PricingSettingsService(db).get_settings()


@router.patch("/pricing", response_model=PricingSettingsResponse)
def update_pricing_settings(payload: PricingSettingsUpdateRequest, db: Session = Depends(get_db)):
    return PricingSettingsService(db).update_settings(payload)


@router.patch("/pricing/suppliers/{supplier_id}", response_model=PricingSupplierResponse)
def update_pricing_supplier(supplier_id: int, payload: PricingSupplierUpdateRequest, db: Session = Depends(get_db)):
    return PricingSettingsService(db).update_supplier(supplier_id=supplier_id, payload=payload)


@router.post("/pricing/suppliers", response_model=PricingSupplierResponse)
def create_pricing_supplier(payload: PricingSupplierCreateRequest, db: Session = Depends(get_db)):
    return PricingSettingsService(db).create_supplier(payload)


@router.delete("/pricing/suppliers/{supplier_id}")
def delete_pricing_supplier(supplier_id: int, db: Session = Depends(get_db)):
    return PricingSettingsService(db).delete_supplier(supplier_id)


@router.get("/weight-rules", response_model=list[WeightRuleResponse])
def list_weight_rules(db: Session = Depends(get_db)):
    try:
        return WeightRuleService(db).list_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules, returning empty list")
        return []


@router.get("/weight-rules/missing-products", response_model=list[WeightMissingProductResponse])
def list_missing_weight_products(limit: int = 500, db: Session = Depends(get_db)):
    try:
        return WeightRuleService(db).list_missing_weight_products(limit=limit)
    except Exception:
        LOGGER.exception("Failed to load missing weight products, returning empty list")
        return []


@router.post("/weight-rules", response_model=WeightRuleResponse)
def create_weight_rule(payload: WeightRuleCreateRequest, db: Session = Depends(get_db)):
    return WeightRuleService(db).create_rule(payload)


@router.patch("/weight-rules/{rule_id}", response_model=WeightRuleResponse)
def update_weight_rule(rule_id: int, payload: WeightRuleUpdateRequest, db: Session = Depends(get_db)):
    return WeightRuleService(db).update_rule(rule_id, payload)


@router.delete("/weight-rules/{rule_id}")
def delete_weight_rule(rule_id: int, db: Session = Depends(get_db)):
    return WeightRuleService(db).delete_rule(rule_id)


@router.post("/weight-rules/{rule_id}/keywords")
def add_weight_rule_keyword(rule_id: int, payload: WeightRuleKeywordRequest, db: Session = Depends(get_db)):
    return WeightRuleService(db).add_keyword(rule_id, payload)


@router.delete("/weight-rules/{rule_id}/keywords/{keyword}")
def remove_weight_rule_keyword(rule_id: int, keyword: str, db: Session = Depends(get_db)):
    return WeightRuleService(db).remove_keyword(rule_id, keyword)
