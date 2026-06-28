from __future__ import annotations

from sqlalchemy.orm import Session, joinedload

from app.models import PricingSetting, Product, ProductListing, Source, Supplier, SupplierShippingRate, WeightRule, WeightRuleKeyword


class CatalogPricingSettingsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_singleton(self) -> PricingSetting | None:
        return self.session.query(PricingSetting).order_by(PricingSetting.id.asc()).first()

    def get_or_create_default(self) -> tuple[PricingSetting, bool]:
        current = self.get_singleton()
        if current is not None:
            return current, False
        entity = PricingSetting(id=1)
        self.session.add(entity)
        self.session.flush()
        return entity, True


class CatalogSupplierRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def query(self):
        return self.session.query(Supplier)

    def list_all_with_rates(self) -> list[Supplier]:
        return (
            self.session.query(Supplier)
            .options(joinedload(Supplier.shipping_rates))
            .order_by(Supplier.parent_supplier_id.asc().nullsfirst(), Supplier.id.asc())
            .all()
        )

    def get_by_id(self, supplier_id: int) -> Supplier | None:
        return (
            self.session.query(Supplier)
            .options(joinedload(Supplier.shipping_rates))
            .filter(Supplier.id == int(supplier_id))
            .one_or_none()
        )

    def create(self, **kwargs) -> Supplier:
        entity = Supplier(**kwargs)
        self.session.add(entity)
        self.session.flush()
        return entity

    def replace_ranges(self, *, supplier_id: int, ranges: list[dict]) -> None:
        (
            self.session.query(SupplierShippingRate)
            .filter(SupplierShippingRate.supplier_id == int(supplier_id))
            .delete(synchronize_session=False)
        )
        for row in ranges:
            self.session.add(
                SupplierShippingRate(
                    supplier_id=int(supplier_id),
                    min_weight_kg=float(row["min_kg"]),
                    max_weight_kg=(float(row["max_kg"]) if row.get("max_kg") is not None else None),
                    price_rub=float(row["rub"]),
                )
            )
        self.session.flush()

    def count_assigned_sources(self, supplier_id: int) -> int:
        return int(
            self.session.query(Source)
            .join(Source.setting)
            .filter(Source.setting.has(supplier_id=int(supplier_id)))
            .count()
        )


class CatalogWeightRuleRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_active(self) -> list[WeightRule]:
        return (
            self.session.query(WeightRule)
            .filter(WeightRule.is_enabled.is_(True))
            .order_by(WeightRule.weight_grams.asc(), WeightRule.id.asc())
            .all()
        )

    def get_by_id(self, rule_id: int) -> WeightRule | None:
        return self.session.query(WeightRule).filter(WeightRule.id == int(rule_id)).one_or_none()

    def create_rule(self, *, weight_grams: int, is_enabled: bool = True) -> WeightRule:
        entity = WeightRule(weight_grams=int(weight_grams), is_enabled=bool(is_enabled))
        self.session.add(entity)
        self.session.flush()
        return entity

    def list_keywords(self, rule_id: int) -> list[WeightRuleKeyword]:
        return (
            self.session.query(WeightRuleKeyword)
            .filter(WeightRuleKeyword.rule_id == int(rule_id))
            .order_by(WeightRuleKeyword.keyword.asc(), WeightRuleKeyword.id.asc())
            .all()
        )

    def get_keyword(self, *, rule_id: int, keyword: str) -> WeightRuleKeyword | None:
        return (
            self.session.query(WeightRuleKeyword)
            .filter(WeightRuleKeyword.rule_id == int(rule_id), WeightRuleKeyword.keyword == str(keyword))
            .one_or_none()
        )

    def create_keyword(self, *, rule_id: int, keyword: str) -> WeightRuleKeyword:
        entity = WeightRuleKeyword(rule_id=int(rule_id), keyword=str(keyword))
        self.session.add(entity)
        self.session.flush()
        return entity

    def list_products_for_weight_recalc(self, *, product_ids: set[int] | None = None) -> list[Product]:
        query = (
            self.session.query(Product)
            .options(
                joinedload(Product.primary_listing).joinedload(ProductListing.variants),
                joinedload(Product.primary_listing).joinedload(ProductListing.source),
                joinedload(Product.weight_rule),
            )
            .filter(Product.lifecycle_status == "active")
            .order_by(Product.id.asc())
        )
        if product_ids:
            query = query.filter(Product.id.in_(sorted(int(product_id) for product_id in product_ids)))
        return query.all()
