"""Public parser contracts consumed by parser-service (no admin auth)."""

import hashlib
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.parser import ParserWeightRuleItem, ParserWeightRulesContractResponse
from app.services.settings.weight_rule_service import WeightRuleService

router = APIRouter(prefix="/public/parser-contract", tags=["public-parser-contract"])
LOGGER = logging.getLogger(__name__)


@router.get("/weight-rules", response_model=ParserWeightRulesContractResponse)
def parser_weight_rules_contract_public(db: Session = Depends(get_db)):
    try:
        rules = WeightRuleService(db).list_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules for parser-contract, returning empty payload")
        rules = []

    payload_rules: list[ParserWeightRuleItem] = []
    revision_parts: list[str] = []
    for rule in rules:
        keywords = sorted({str(item).strip().lower() for item in (rule.keywords or []) if str(item).strip()})
        payload_rules.append(ParserWeightRuleItem(weight_grams=int(rule.weight_grams), keywords=keywords))
        revision_parts.append(f'{int(rule.weight_grams)}:{"|".join(keywords)}')

    revision_raw = ";".join(revision_parts).encode("utf-8")
    revision = hashlib.sha1(revision_raw).hexdigest()[:12] if revision_parts else "empty-rules"
    return ParserWeightRulesContractResponse(revision=revision, rules=payload_rules)
