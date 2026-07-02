from __future__ import annotations

from app.core.database import SessionLocal
from app.models import WeightRule, WeightRuleKeyword
from app.services.settings.weight_rule_service import WeightRuleService


def test_weight_rule_defaults_do_not_reseed_missing_rules_when_custom_rules_exist() -> None:
    db = SessionLocal()
    try:
        db.query(WeightRuleKeyword).delete(synchronize_session=False)
        db.query(WeightRule).delete(synchronize_session=False)
        db.flush()

        rule = WeightRule(weight_grams=1234, is_enabled=True)
        db.add(rule)
        db.flush()
        db.add(WeightRuleKeyword(rule_id=int(rule.id), keyword="custom coat"))
        db.flush()

        WeightRuleService(db).ensure_default_rules()
        db.flush()

        active_rules = db.query(WeightRule).filter(WeightRule.is_enabled.is_(True)).order_by(WeightRule.id.asc()).all()
        assert len(active_rules) == 1
        assert int(active_rules[0].weight_grams) == 1234
    finally:
        db.rollback()
        db.close()


def test_weight_rule_defaults_do_not_seed_rules_when_database_is_empty() -> None:
    db = SessionLocal()
    try:
        db.query(WeightRuleKeyword).delete(synchronize_session=False)
        db.query(WeightRule).delete(synchronize_session=False)
        db.flush()

        WeightRuleService(db).ensure_default_rules()
        db.flush()

        active_rules = db.query(WeightRule).filter(WeightRule.is_enabled.is_(True)).all()
        assert active_rules == []
    finally:
        db.rollback()
        db.close()
