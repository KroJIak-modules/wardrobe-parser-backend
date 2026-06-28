from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import NotFoundError, ValidationError
from app.models import (
    CustomCatalog,
    CustomCatalogProduct,
    Designer,
    Filter,
    FilterManualProduct,
    ImageAsset,
    ProductDedupDecision,
    ProductListingGalleryImage,
    ProductListingMember,
)
from app.repositories.catalog_products import CatalogProductRepository
from app.services.catalog.dedup_service_v2 import DedupServiceV2
from app.services.catalog.designer_catalog_sync_service import DesignerCatalogSyncService
from app.services.catalog.designer_support import normalize_designer_text, slugify_designer_name
from app.services.catalog.filter_assignment_service import ProductFilterAssignmentService
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.source_registry_service import SourceRegistryService


@dataclass
class _CombineTreeNode:
    product_id: int
    snapshot: dict[str, Any] | None = None
    children: list["_CombineTreeNode"] | None = None

    @property
    def is_combine(self) -> bool:
        return self.children is not None


class ProductWriteService:
    MANUAL_VARIANT_PRICING_MODE_SOURCE = "source"
    MANUAL_VARIANT_PRICING_MODE_FIXED_FINAL_RUB = "fixed_final_rub"

    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = CatalogProductRepository(db)
        self.sources = SourceRegistryService(db)
        self.filter_assignments = ProductFilterAssignmentService(db)

    @staticmethod
    def _normalize_visibility_status(raw: str | None) -> str:
        value = str(raw or "").strip().lower()
        if value in {"visible", "hidden"}:
            return value
        return "visible"

    @staticmethod
    def _normalize_availability_mode(raw: str | None) -> str:
        value = str(raw or "").strip().lower()
        if value in {"in_stock", "by_order"}:
            return value
        return "by_order"

    @staticmethod
    def _normalize_gender(raw: str | None) -> str:
        value = str(raw or "").strip().lower()
        if value in {"male", "female", "unisex"}:
            return value
        return "unisex"

    @staticmethod
    def _normalize_orderability_status(raw: str | None) -> str:
        value = str(raw or "").strip().lower()
        if value in {"orderable", "sold_out", "unavailable"}:
            return value
        return "orderable"

    @staticmethod
    def _validate_in_stock_availability(*, availability_mode: str, orderability_status: str) -> None:
        normalized_availability_mode = str(availability_mode or "").strip().lower()
        normalized_orderability_status = str(orderability_status or "").strip().lower()
        if normalized_availability_mode != "in_stock":
            return
        if normalized_orderability_status == "sold_out":
            raise ValidationError("Нельзя переключить, пока товар распродан")
        if normalized_orderability_status == "unavailable":
            raise ValidationError("Нельзя сделать товар 'В наличии', пока он недоступен")

    def _product_or_error(self, product_id: int):
        product = self.products.get_product(product_id)
        if product is None:
            raise NotFoundError("Товар не найден")
        return product

    def _primary_listing_or_error(self, product):
        listing = product.primary_listing or (product.memberships[0].listing if product.memberships else None)
        if listing is None:
            raise ValidationError("У товара нет основного listing")
        return listing

    @staticmethod
    def _manual_listing(product):
        for membership in product.memberships:
            listing = membership.listing
            if listing is not None and str(listing.ingest_mode or "") == "manual":
                return listing
        return None

    def _manual_listing_or_error(self, product):
        listing = self._manual_listing(product)
        if listing is None:
            raise ValidationError("У товара нет manual listing")
        return listing

    @staticmethod
    def _combine_snapshot(product) -> dict[str, Any]:
        presentation = getattr(product, "presentation", None)
        return {
            "designer_id": int(product.designer_id) if getattr(product, "designer_id", None) is not None else None,
            "gender": str(getattr(product, "gender", "") or "unisex"),
            "source_gender": str(getattr(product, "source_gender", "") or getattr(product, "gender", "") or "unisex"),
            "gender_is_manual": bool(getattr(product, "gender_is_manual", False)),
            "availability_mode": str(getattr(product, "availability_mode", "") or "by_order"),
            "manual_weight_grams": int(product.manual_weight_grams) if getattr(product, "manual_weight_grams", None) is not None else None,
            "weight_rule_id": int(product.weight_rule_id) if getattr(product, "weight_rule_id", None) is not None else None,
            "visibility_status": str(getattr(product, "visibility_status", "") or "visible"),
            "primary_listing_id": int(product.primary_listing_id) if getattr(product, "primary_listing_id", None) is not None else None,
            "presentation": (
                {
                    "title_override": presentation.title_override,
                    "brand_override_name": presentation.brand_override_name,
                    "description_text": presentation.description_text,
                    "description_html": presentation.description_html,
                    "description_visibility": presentation.description_visibility,
                }
                if presentation is not None
                else None
            ),
        }

    def _load_combine_decisions(self) -> list[ProductDedupDecision]:
        return (
            self.db.query(ProductDedupDecision)
            .options(joinedload(ProductDedupDecision.members))
            .filter(ProductDedupDecision.decision_kind == "combine")
            .order_by(ProductDedupDecision.id.asc())
            .all()
        )

    def _build_combine_tree(
        self,
        *,
        product_id: int,
        created_to_decision: dict[int, ProductDedupDecision],
        combine_products: dict[int, object],
    ) -> _CombineTreeNode:
        decision = created_to_decision.get(int(product_id))
        if decision is None:
            return _CombineTreeNode(product_id=int(product_id), children=None)
        product = combine_products.get(int(product_id))
        if product is None:
            raise NotFoundError("Комбинированный товар не найден")
        child_nodes = [
            self._build_combine_tree(
                product_id=int(member.product_id),
                created_to_decision=created_to_decision,
                combine_products=combine_products,
            )
            for member in sorted(decision.members, key=lambda item: int(item.product_id))
        ]
        return _CombineTreeNode(
            product_id=int(product_id),
            snapshot=self._combine_snapshot(product),
            children=child_nodes,
        )

    def _prune_combine_tree(self, node: _CombineTreeNode, *, deleted_product_id: int) -> _CombineTreeNode | None:
        if int(node.product_id) == int(deleted_product_id):
            return None
        if not node.is_combine:
            return node
        pruned_children: list[_CombineTreeNode] = []
        for child in node.children or []:
            pruned_child = self._prune_combine_tree(child, deleted_product_id=deleted_product_id)
            if pruned_child is not None:
                pruned_children.append(pruned_child)
        if not pruned_children:
            return None
        if len(pruned_children) == 1:
            return pruned_children[0]
        return _CombineTreeNode(
            product_id=int(node.product_id),
            snapshot=dict(node.snapshot or {}),
            children=pruned_children,
        )

    def _collect_leaf_product_ids(self, node: _CombineTreeNode) -> set[int]:
        if not node.is_combine:
            return {int(node.product_id)}
        collected: set[int] = set()
        for child in node.children or []:
            collected.update(self._collect_leaf_product_ids(child))
        return collected

    def _collect_combine_product_ids(self, node: _CombineTreeNode) -> set[int]:
        if not node.is_combine:
            return set()
        collected = {int(node.product_id)}
        for child in node.children or []:
            collected.update(self._collect_combine_product_ids(child))
        return collected

    def _collect_component_tree_product_ids(
        self,
        *,
        product_id: int,
        created_to_decision: dict[int, ProductDedupDecision],
    ) -> set[int]:
        decision = created_to_decision.get(int(product_id))
        if decision is None:
            return set()
        collected = {int(product_id)}
        for member in decision.members:
            collected.update(
                self._collect_component_tree_product_ids(
                    product_id=int(member.product_id),
                    created_to_decision=created_to_decision,
                )
            )
        return collected

    def _apply_combine_snapshot_to_product(self, *, product_id: int, snapshot: dict[str, Any]) -> None:
        product = self._product_or_error(product_id)
        product.designer_id = self._int_or_none(snapshot.get("designer_id"))
        product.gender = self._normalize_gender(snapshot.get("gender"))
        product.source_gender = self._normalize_gender(snapshot.get("source_gender"))
        product.gender_is_manual = bool(snapshot.get("gender_is_manual"))
        product.availability_mode = self._normalize_availability_mode(snapshot.get("availability_mode"))
        product.manual_weight_grams = self._int_or_none(snapshot.get("manual_weight_grams"))
        product.weight_rule_id = self._int_or_none(snapshot.get("weight_rule_id"))
        product.visibility_status = self._normalize_visibility_status(snapshot.get("visibility_status"))

        available_listing_ids = {
            int(listing.id)
            for listing in self.products.list_product_listings(int(product.id))
        }
        preferred_primary_listing_id = self._int_or_none(snapshot.get("primary_listing_id"))
        if preferred_primary_listing_id in available_listing_ids:
            product.primary_listing_id = preferred_primary_listing_id
        elif available_listing_ids:
            current_primary_listing_id = int(product.primary_listing_id) if product.primary_listing_id is not None else None
            if current_primary_listing_id not in available_listing_ids:
                product.primary_listing_id = min(available_listing_ids)
        else:
            product.primary_listing_id = None

        presentation_snapshot = snapshot.get("presentation") if isinstance(snapshot, dict) else None
        presentation = self._ensure_product_presentation(product)
        if isinstance(presentation_snapshot, dict):
            presentation.title_override = str(presentation_snapshot.get("title_override") or "").strip() or None
            presentation.brand_override_name = normalize_designer_text(presentation_snapshot.get("brand_override_name"))
            presentation.description_text = str(presentation_snapshot.get("description_text") or "").strip() or None
            presentation.description_html = str(presentation_snapshot.get("description_html") or "").strip() or None
            if "description_visibility" in presentation_snapshot:
                raw_visibility = presentation_snapshot.get("description_visibility")
                presentation.description_visibility = bool(raw_visibility) if raw_visibility is not None else None
        else:
            presentation.title_override = None
            presentation.brand_override_name = None
            presentation.description_text = None
            presentation.description_html = None
            presentation.description_visibility = None
        self.db.flush()

    def _choose_primary_product_id_for_snapshot(self, *, snapshot: dict[str, Any], child_product_ids: list[int]) -> int | None:
        preferred_primary_listing_id = self._int_or_none(snapshot.get("primary_listing_id"))
        if preferred_primary_listing_id is None:
            return child_product_ids[0] if child_product_ids else None
        for child_product_id in child_product_ids:
            listing_ids = {
                int(listing.id)
                for listing in self.products.list_product_listings(int(child_product_id))
            }
            if preferred_primary_listing_id in listing_ids:
                return int(child_product_id)
        return child_product_ids[0] if child_product_ids else None

    def _rebuild_pruned_combine_tree(
        self,
        *,
        node: _CombineTreeNode,
        dedup_service: DedupServiceV2,
        affected_product_ids: set[int],
    ) -> int:
        if not node.is_combine:
            return int(node.product_id)
        child_product_ids = [
            self._rebuild_pruned_combine_tree(node=child, dedup_service=dedup_service, affected_product_ids=affected_product_ids)
            for child in node.children or []
        ]
        if len(child_product_ids) < 2:
            raise ValidationError("Комбинированный узел не может быть перестроен меньше чем из двух товаров")
        primary_product_id = self._choose_primary_product_id_for_snapshot(
            snapshot=node.snapshot or {},
            child_product_ids=child_product_ids,
        )
        created_product_id = dedup_service.merge(
            product_ids=child_product_ids,
            merge_mode="combine",
            primary_product_id=primary_product_id,
        )
        affected_product_ids.add(int(created_product_id))
        self._apply_combine_snapshot_to_product(product_id=int(created_product_id), snapshot=node.snapshot or {})
        return int(created_product_id)

    def _rewrite_combine_component_after_delete(self, *, product_id: int, affected_product_ids: set[int]) -> bool:
        decisions = self._load_combine_decisions()
        if not decisions:
            return False

        created_to_decision = {
            int(decision.created_product_id): decision
            for decision in decisions
            if decision.created_product_id is not None
        }
        member_to_parents: dict[int, set[int]] = defaultdict(set)
        for decision in decisions:
            if decision.created_product_id is None:
                continue
            created_product_id = int(decision.created_product_id)
            for member in decision.members:
                member_to_parents[int(member.product_id)].add(created_product_id)

        component_created_ids: set[int] = set()
        stack = [int(product_id)]
        seen_product_ids: set[int] = set()
        while stack:
            current_product_id = int(stack.pop())
            if current_product_id in seen_product_ids:
                continue
            seen_product_ids.add(current_product_id)
            if current_product_id in created_to_decision:
                component_created_ids.add(current_product_id)
            for parent_product_id in member_to_parents.get(current_product_id, set()):
                if parent_product_id not in seen_product_ids:
                    component_created_ids.add(parent_product_id)
                    stack.append(parent_product_id)

        if not component_created_ids:
            return False

        root_product_ids = sorted(
            product_id_value
            for product_id_value in component_created_ids
            if not member_to_parents.get(int(product_id_value))
        )
        if not root_product_ids:
            root_product_ids = sorted(component_created_ids)

        tree_created_ids: set[int] = set()
        for root_product_id in root_product_ids:
            tree_created_ids.update(
                self._collect_component_tree_product_ids(
                    product_id=int(root_product_id),
                    created_to_decision=created_to_decision,
                )
            )

        combine_products = {
            int(product.id): product
            for product in self.products.list_products_by_ids(tree_created_ids, include_merged=True)
        }
        original_trees = [
            self._build_combine_tree(
                product_id=int(root_product_id),
                created_to_decision=created_to_decision,
                combine_products=combine_products,
            )
            for root_product_id in root_product_ids
        ]
        pruned_trees = [
            pruned
            for tree in original_trees
            if (pruned := self._prune_combine_tree(tree, deleted_product_id=int(product_id))) is not None
        ]

        original_combine_ids: set[int] = set()
        original_leaf_ids: set[int] = set()
        for tree in original_trees:
            original_combine_ids.update(self._collect_combine_product_ids(tree))
            original_leaf_ids.update(self._collect_leaf_product_ids(tree))
        surviving_leaf_ids: set[int] = set()
        for tree in pruned_trees:
            surviving_leaf_ids.update(self._collect_leaf_product_ids(tree))
        deleted_leaf_ids = sorted(original_leaf_ids - surviving_leaf_ids)

        affected_product_ids.update(original_combine_ids)
        affected_product_ids.update(original_leaf_ids)

        for leaf_product_id in sorted(surviving_leaf_ids):
            if self.products.get_product(int(leaf_product_id)) is not None:
                self.products.set_product_dedup_state(
                    product_id=int(leaf_product_id),
                    dedup_status="independent",
                    dedup_decision_id=None,
                    dedup_target_product_id=None,
                )

        for decision in decisions:
            created_product_id = int(decision.created_product_id or 0)
            if created_product_id in original_combine_ids:
                self.db.delete(decision)
        self.db.flush()

        for combine_product_id in sorted(original_combine_ids, reverse=True):
            if self.products.get_product(int(combine_product_id)) is not None:
                self.products.delete_product_hard(int(combine_product_id))

        for deleted_leaf_product_id in deleted_leaf_ids:
            if self.products.get_product(int(deleted_leaf_product_id)) is not None:
                self._delete_product_storage(product_id=int(deleted_leaf_product_id), affected_product_ids=affected_product_ids)

        dedup_service = DedupServiceV2(self.db)
        for tree in pruned_trees:
            if tree.is_combine:
                self._rebuild_pruned_combine_tree(
                    node=tree,
                    dedup_service=dedup_service,
                    affected_product_ids=affected_product_ids,
                )
        return True

    def _ensure_product_presentation(self, product):
        presentation = product.presentation
        if presentation is not None:
            return presentation
        return self.products.ensure_presentation(int(product.id))

    def _create_product_base(self, payload: dict) -> tuple[object, str, str]:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValidationError("title is required")

        normalized_availability_mode = self._normalize_availability_mode(payload.get("availability_mode") or "in_stock")
        normalized_orderability_status = self._normalize_orderability_status(payload.get("orderability_status"))
        self._validate_in_stock_availability(
            availability_mode=normalized_availability_mode,
            orderability_status=normalized_orderability_status,
        )

        product = self.products.create_product(
            designer_id=self._int_or_none(payload.get("designer_id")),
            gender=self._normalize_gender(payload.get("gender")),
            source_gender=self._normalize_gender(payload.get("gender")),
            gender_is_manual=True,
            availability_mode=normalized_availability_mode,
            lifecycle_status="active",
            visibility_status=self._normalize_visibility_status(payload.get("visibility_status")),
            manual_weight_grams=self._int_or_none(payload.get("manual_weight_grams")),
        )
        return product, title, normalized_orderability_status

    def _apply_bound_sync_overrides(self, *, product, listing, payload: dict) -> None:
        presentation = product.presentation

        requested_title = str(payload.get("title") or "").strip()
        source_title = str(getattr(listing, "source_title", "") or "").strip()
        if requested_title and requested_title != source_title:
            presentation = presentation or self._ensure_product_presentation(product)
            presentation.title_override = requested_title

        requested_description_text = str(payload.get("description_text") or "").strip() or None
        source_description_text = str(getattr(listing, "source_description_text", "") or "").strip() or None
        if requested_description_text and requested_description_text != source_description_text:
            presentation = presentation or self._ensure_product_presentation(product)
            presentation.description_text = requested_description_text

        requested_description_html = str(payload.get("description_html") or "").strip() or None
        source_description_html = str(getattr(listing, "source_description_html", "") or "").strip() or None
        if requested_description_html and requested_description_html != source_description_html:
            presentation = presentation or self._ensure_product_presentation(product)
            presentation.description_html = requested_description_html

        requested_brand_name = normalize_designer_text(payload.get("designer_name"))
        source_brand_name = normalize_designer_text(getattr(listing, "source_designer_raw", None))
        if requested_brand_name and requested_brand_name != source_brand_name:
            presentation = presentation or self._ensure_product_presentation(product)
            presentation.brand_override_name = requested_brand_name
            designer = self._ensure_designer_for_brand(requested_brand_name)
            product.designer_id = int(designer.id)

    def create_sync_bound_product(self, *, payload: dict, source_id: int, service_item: dict, force_primary_listing: bool = False) -> int:
        with self.db.begin_nested():
            product, _title, _normalized_orderability_status = self._create_product_base(payload)

            ProductIngestService(self.db).apply_batch(
                source_id=int(source_id),
                items=[service_item],
                target_product_id=int(product.id),
                force_primary_listing=bool(force_primary_listing),
            )

            refreshed_product_id = int(product.id)
            self.db.flush()
            self.db.expunge_all()
            refreshed_product = self._product_or_error(refreshed_product_id)
            primary_listing = self._primary_listing_or_error(refreshed_product)
            self._apply_bound_sync_overrides(product=refreshed_product, listing=primary_listing, payload=payload)

            self._replace_taxonomy_links(
                product_id=int(refreshed_product.id),
                filter_slugs=payload.get("filter_slugs"),
                custom_catalog_slugs=payload.get("custom_catalog_slugs"),
            )
            self.db.flush()
            self.db.expire(refreshed_product)
            DesignerCatalogSyncService(self.db).reconcile(sync_product_links=True)
            self._sync_weight_state(product=refreshed_product, listing=primary_listing)
            self.db.flush()
            self.filter_assignments.enqueue_product_ids_after_commit([int(refreshed_product.id)])
            return int(refreshed_product.id)

    def _ensure_designer_for_brand(self, brand_name: str):
        normalized_name = normalize_designer_text(brand_name)
        if not normalized_name:
            raise ValidationError("brand_override_name is required")
        existing = (
            self.db.query(Designer)
            .filter(Designer.name == normalized_name)
            .one_or_none()
        )
        if existing is not None:
            return existing

        used_slugs = {
            str(designer.slug).strip()
            for designer in self.db.query(Designer).all()
            if str(designer.slug or "").strip()
        }
        base_slug = slugify_designer_name(normalized_name)
        if base_slug not in used_slugs:
            slug = base_slug
        else:
            index = 2
            while f"{base_slug}-{index}" in used_slugs:
                index += 1
            slug = f"{base_slug}-{index}"

        created = Designer(
            name=normalized_name,
            slug=slug,
            origin_kind="auto",
            is_admin_touched=False,
            is_enabled=True,
        )
        self.db.add(created)
        self.db.flush()
        return created

    def _can_edit_manual_variants(self, product) -> bool:
        manual_listing = self._manual_listing(product)
        if manual_listing is None:
            return False
        if any(str(listing.ingest_mode or "") == "sync" for listing in self.products.list_product_listings(int(product.id))):
            return False
        return self.sources.derive_source_mode(getattr(manual_listing, "source", None)) == "personal"

    @staticmethod
    def _normalize_manual_variant_pricing_mode(*, currency_code: str | None, raw_mode: object) -> str:
        normalized_currency = str(currency_code or "").strip().upper() or None
        normalized_mode = str(raw_mode or "").strip().lower()
        if normalized_mode and normalized_mode not in {
            ProductWriteService.MANUAL_VARIANT_PRICING_MODE_SOURCE,
            ProductWriteService.MANUAL_VARIANT_PRICING_MODE_FIXED_FINAL_RUB,
        }:
            raise ValidationError("variant pricing_mode is invalid")
        if normalized_mode == ProductWriteService.MANUAL_VARIANT_PRICING_MODE_FIXED_FINAL_RUB and normalized_currency != "RUB":
            raise ValidationError("fixed_final_rub is allowed only for RUB variants")
        if normalized_currency == "RUB":
            return ProductWriteService.MANUAL_VARIANT_PRICING_MODE_FIXED_FINAL_RUB
        return ProductWriteService.MANUAL_VARIANT_PRICING_MODE_SOURCE

    @staticmethod
    def _normalize_manual_variants(variants: object) -> list[dict]:
        if not isinstance(variants, list):
            raise ValidationError("variants must be a list")
        normalized: list[dict] = []
        for index, item in enumerate(variants, start=1):
            if not isinstance(item, dict):
                raise ValidationError(f"variant #{index} is invalid")
            title = str(item.get("title") or "").strip()
            if not title:
                raise ValidationError("variant title is required")
            raw_price = item.get("price")
            try:
                price_amount = None if raw_price is None or str(raw_price).strip() == "" else Decimal(str(raw_price))
            except Exception as exc:  # noqa: BLE001
                raise ValidationError(f"variant #{index} has invalid price") from exc
            if price_amount is not None and price_amount <= 0:
                raise ValidationError("variant price must be greater than zero")
            raw_compare_at_price = item.get("compare_at_price")
            try:
                compare_at_price_amount = None if raw_compare_at_price is None or str(raw_compare_at_price).strip() == "" else Decimal(str(raw_compare_at_price))
            except Exception as exc:  # noqa: BLE001
                raise ValidationError(f"variant #{index} has invalid compare_at_price") from exc
            if compare_at_price_amount is not None and compare_at_price_amount <= 0:
                raise ValidationError("variant compare_at_price must be greater than zero")
            if (
                price_amount is not None
                and compare_at_price_amount is not None
                and compare_at_price_amount <= price_amount
            ):
                raise ValidationError("variant compare_at_price must be greater than price")
            currency_code = str(item.get("currency") or "").strip().upper() or None
            pricing_mode = ProductWriteService._normalize_manual_variant_pricing_mode(
                currency_code=currency_code,
                raw_mode=item.get("pricing_mode"),
            )
            if price_amount is None and compare_at_price_amount is not None:
                raise ValidationError("variant compare_at_price requires price")
            normalized.append(
                {
                    "title": title,
                    "price_amount": price_amount,
                    "compare_at_price_amount": compare_at_price_amount,
                    "currency_code": currency_code,
                    "pricing_mode": pricing_mode,
                    "is_orderable": bool(item.get("available", True)),
                    "source_ref_id": None,
                    "sku": None,
                }
            )
        if not normalized:
            raise ValidationError("variants are required")
        return normalized

    @staticmethod
    def _int_or_none(value: object) -> int | None:
        try:
            if value is None or str(value).strip() == "":
                return None
            candidate = int(value)
        except Exception:
            return None
        return candidate if candidate > 0 else None

    def _listing_belongs_to_product(self, *, product_id: int, listing_id: int) -> bool:
        return any(int(listing.id) == int(listing_id) for listing in self.products.list_product_listings(int(product_id)))

    def _listing_for_gallery_or_error(self, *, product, listing_id: int | None) -> object:
        target_listing_id = int(listing_id) if listing_id is not None else None
        if target_listing_id is None:
            return self._primary_listing_or_error(product)
        if not self._listing_belongs_to_product(product_id=int(product.id), listing_id=target_listing_id):
            raise ValidationError("gallery listing не принадлежит товару")
        listing = self.products.get_listing(target_listing_id)
        if listing is None:
            raise ValidationError("gallery listing не найден")
        return listing

    @staticmethod
    def _normalize_slug_list(values: object) -> list[str]:
        if not isinstance(values, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for raw_value in values:
            value = str(raw_value or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _replace_taxonomy_links(
        self,
        *,
        product_id: int,
        filter_slugs: object | None = None,
        custom_catalog_slugs: object | None = None,
    ) -> None:
        if filter_slugs is not None:
            normalized_filter_slugs = self._normalize_slug_list(filter_slugs)
            filters = (
                self.db.query(Filter)
                .filter(Filter.slug.in_(normalized_filter_slugs))
                .order_by(Filter.slug.asc(), Filter.id.asc())
                .all()
            ) if normalized_filter_slugs else []
            found_slugs = {str(entity.slug) for entity in filters}
            missing = [slug for slug in normalized_filter_slugs if slug not in found_slugs]
            if missing:
                raise ValidationError(f"Не найдены filters: {', '.join(missing)}")
            (
                self.db.query(FilterManualProduct)
                .filter(FilterManualProduct.product_id == int(product_id))
                .delete(synchronize_session=False)
            )
            for entity in filters:
                self.db.add(FilterManualProduct(filter_id=int(entity.id), product_id=int(product_id)))

        if custom_catalog_slugs is not None:
            normalized_catalog_slugs = self._normalize_slug_list(custom_catalog_slugs)
            catalogs = (
                self.db.query(CustomCatalog)
                .filter(CustomCatalog.slug.in_(normalized_catalog_slugs))
                .order_by(CustomCatalog.slug.asc(), CustomCatalog.id.asc())
                .all()
            ) if normalized_catalog_slugs else []
            found_slugs = {str(entity.slug) for entity in catalogs}
            missing = [slug for slug in normalized_catalog_slugs if slug not in found_slugs]
            if missing:
                raise ValidationError(f"Не найдены custom catalogs: {', '.join(missing)}")
            (
                self.db.query(CustomCatalogProduct)
                .filter(CustomCatalogProduct.product_id == int(product_id))
                .delete(synchronize_session=False)
            )
            for entity in catalogs:
                self.db.add(CustomCatalogProduct(catalog_id=int(entity.id), product_id=int(product_id)))

        self.db.flush()

    def _duplicate_gallery_scope(self, *, from_product_id: int, to_product_id: int, listing_id: int) -> None:
        scope_rows = self.products.list_gallery_scope(product_id=from_product_id, listing_id=listing_id)
        if not scope_rows:
            listing = self.products.get_listing(listing_id)
            if listing is not None:
                self.products.replace_gallery_scope_with_source_images(
                    product_id=to_product_id,
                    listing_id=listing_id,
                    listing_images=listing.images,
                )
            return
        for row in scope_rows:
            self.db.add(
                ProductListingGalleryImage(
                    product_id=int(to_product_id),
                    listing_id=int(listing_id),
                    listing_image_id=(int(row.listing_image_id) if row.listing_image_id is not None else None),
                    image_asset_id=(int(row.image_asset_id) if row.image_asset_id is not None else None),
                    position=int(row.position),
                    is_hidden=bool(row.is_hidden),
                    origin_kind=str(row.origin_kind),
                )
            )
        self.db.flush()

    def _sync_weight_state(self, *, product, listing) -> None:
        incoming_status = self._normalize_orderability_status(listing.orderability_status)
        incoming_reason = str(listing.status_reason or "").strip().lower() or None
        if incoming_reason == "missing_weight":
            variant_states = [bool(variant.is_orderable) for variant in getattr(listing, "variants", [])]
            if variant_states:
                incoming_status = "orderable" if any(variant_states) else "sold_out"
            incoming_reason = None
        listing.orderability_status, listing.status_reason = ProductIngestService(self.db)._resolve_listing_status(
            product=product,
            listing=listing,
            incoming_status=incoming_status,
            incoming_reason=incoming_reason,
            incoming_reasons=([incoming_reason] if incoming_reason else []),
        )

    def _gallery_asset_ids_from_urls(self, manual_image_urls: list[str]) -> list[int]:
        asset_ids: list[int] = []
        for url in manual_image_urls:
            value = str(url or "").strip()
            if not value.startswith("/api/v1/products/images/"):
                continue
            tail = value.rsplit("/", 1)[-1]
            try:
                asset_id = int(tail)
            except Exception:
                continue
            asset_ids.append(asset_id)
        return asset_ids

    def _replace_gallery_scope(
        self,
        *,
        product_id: int,
        listing_id: int,
        hidden_source_image_urls: list[str],
        manual_image_urls: list[str],
        manual_image_order: list[str],
    ) -> None:
        scope_rows = self.products.list_gallery_scope(product_id=product_id, listing_id=listing_id)
        for row in scope_rows:
            self.db.delete(row)
        self.db.flush()

        listing = self.products.get_listing(listing_id)
        if listing is None:
            return
        source_images = {str(image.url): image for image in listing.images}
        hidden_source_set = set(hidden_source_image_urls)
        manual_asset_ids = self._gallery_asset_ids_from_urls(manual_image_urls)
        manual_assets = {
            int(asset.id): asset
            for asset in self.db.query(ImageAsset).filter(ImageAsset.id.in_(manual_asset_ids)).all()
        } if manual_asset_ids else {}

        position = 1
        visible_source_urls = [url for url in source_images.keys() if url not in hidden_source_set]
        ordered_entries: list[tuple[str, str]] = []
        seen_source_urls: set[str] = set()
        seen_manual_urls: set[str] = set()

        for raw_entry in manual_image_order:
            entry = str(raw_entry or "").strip()
            if not entry:
                continue
            if entry.startswith("s:"):
                source_url = entry[2:].strip()
                if source_url and source_url in source_images and source_url not in hidden_source_set and source_url not in seen_source_urls:
                    ordered_entries.append(("source_image", source_url))
                    seen_source_urls.add(source_url)
                continue
            if entry.startswith("m:"):
                manual_url = entry[2:].strip()
                if manual_url and manual_url in manual_image_urls and manual_url not in seen_manual_urls:
                    ordered_entries.append(("uploaded_asset", manual_url))
                    seen_manual_urls.add(manual_url)
                continue
            if entry in source_images and entry not in hidden_source_set and entry not in seen_source_urls:
                ordered_entries.append(("source_image", entry))
                seen_source_urls.add(entry)
                continue
            if entry in manual_image_urls and entry not in seen_manual_urls:
                ordered_entries.append(("uploaded_asset", entry))
                seen_manual_urls.add(entry)

        for source_url in visible_source_urls:
            if source_url in seen_source_urls:
                continue
            ordered_entries.append(("source_image", source_url))
            seen_source_urls.add(source_url)

        for manual_url in manual_image_urls:
            if manual_url in seen_manual_urls:
                continue
            ordered_entries.append(("uploaded_asset", manual_url))
            seen_manual_urls.add(manual_url)

        for origin_kind, entry_url in ordered_entries:
            if origin_kind == "source_image":
                image = source_images.get(entry_url)
                if image is None:
                    continue
                self.db.add(
                    ProductListingGalleryImage(
                        product_id=int(product_id),
                        listing_id=int(listing_id),
                        listing_image_id=int(image.id),
                        position=position,
                        is_hidden=False,
                        origin_kind="source_image",
                    )
                )
                position += 1
                continue

            asset_id = self._gallery_asset_ids_from_urls([entry_url])
            if not asset_id or asset_id[0] not in manual_assets:
                continue
            self.db.add(
                ProductListingGalleryImage(
                    product_id=int(product_id),
                    listing_id=int(listing_id),
                    image_asset_id=int(asset_id[0]),
                    position=position,
                    is_hidden=False,
                    origin_kind="uploaded_asset",
                )
            )
            position += 1

        for hidden_url in hidden_source_image_urls:
            image = source_images.get(hidden_url)
            if image is None:
                continue
            self.db.add(
                ProductListingGalleryImage(
                    product_id=int(product_id),
                    listing_id=int(listing_id),
                    listing_image_id=int(image.id),
                    position=position,
                    is_hidden=True,
                    origin_kind="source_image",
                )
            )
            position += 1

        self.db.flush()

    def update_product(self, *, product_id: int, payload: dict) -> None:
        product = self._product_or_error(product_id)
        if payload.get("primary_listing_id") is not None:
            primary_listing_id = int(payload["primary_listing_id"])
            if not self._listing_belongs_to_product(product_id=int(product.id), listing_id=primary_listing_id):
                raise ValidationError("primary_listing_id не принадлежит товару")
            product.primary_listing_id = primary_listing_id
        listing = self._primary_listing_or_error(product)
        presentation = product.presentation
        manual_listing = self._manual_listing(product)
        brand_override_changed = False

        reset_to_default = {str(item).strip() for item in payload.get("reset_to_default") or []}
        current_brand_override_name = normalize_designer_text(getattr(presentation, "brand_override_name", None))
        if manual_listing is not None:
            if "title_override" in payload:
                next_title = str(payload.get("title_override") or "").strip()
                if not next_title:
                    raise ValidationError("manual title is required")
                manual_listing.source_title = next_title

            if "description_text" in reset_to_default:
                manual_listing.source_description_text = None
            elif "description_text" in payload:
                manual_listing.source_description_text = str(payload.get("description_text") or "").strip() or None

            if "description_html" in reset_to_default:
                manual_listing.source_description_html = None
            elif "description_html" in payload:
                manual_listing.source_description_html = str(payload.get("description_html") or "").strip() or None
        else:
            if "title_override" in reset_to_default:
                presentation = presentation or self._ensure_product_presentation(product)
                presentation.title_override = None
            elif "title_override" in payload:
                presentation = presentation or self._ensure_product_presentation(product)
                presentation.title_override = str(payload.get("title_override") or "").strip() or None

            if "description_text" in reset_to_default:
                presentation = presentation or self._ensure_product_presentation(product)
                presentation.description_text = None
            elif "description_text" in payload:
                presentation = presentation or self._ensure_product_presentation(product)
                presentation.description_text = str(payload.get("description_text") or "").strip() or None

            if "description_html" in reset_to_default:
                presentation = presentation or self._ensure_product_presentation(product)
                presentation.description_html = None
            elif "description_html" in payload:
                presentation = presentation or self._ensure_product_presentation(product)
                presentation.description_html = str(payload.get("description_html") or "").strip() or None

        if "brand_override_name" in reset_to_default:
            presentation = presentation or self._ensure_product_presentation(product)
            presentation.brand_override_name = None
            brand_override_changed = bool(current_brand_override_name)
        elif "brand_override_name" in payload:
            presentation = presentation or self._ensure_product_presentation(product)
            next_brand_override_name = normalize_designer_text(payload.get("brand_override_name"))
            if next_brand_override_name == normalize_designer_text(getattr(listing, "source_designer_raw", None)):
                next_brand_override_name = ""
            presentation.brand_override_name = next_brand_override_name or None
            brand_override_changed = current_brand_override_name != next_brand_override_name
            if next_brand_override_name:
                designer = self._ensure_designer_for_brand(next_brand_override_name)
                product.designer_id = int(designer.id)

        if "description_visibility" in reset_to_default:
            presentation = presentation or self._ensure_product_presentation(product)
            presentation.description_visibility = None
        elif "description_visibility" in payload:
            presentation = presentation or self._ensure_product_presentation(product)
            presentation.description_visibility = bool(payload.get("description_visibility"))

        if "visibility_status" in payload:
            product.visibility_status = self._normalize_visibility_status(payload.get("visibility_status"))

        if "availability_mode" in payload:
            next_availability_mode = self._normalize_availability_mode(payload.get("availability_mode"))
            self._validate_in_stock_availability(
                availability_mode=next_availability_mode,
                orderability_status=self._normalize_orderability_status(getattr(listing, "orderability_status", None)),
            )
            product.availability_mode = next_availability_mode

        if "gender" in payload:
            product.gender = self._normalize_gender(payload.get("gender"))
            product.gender_is_manual = True
            if not str(getattr(product, "source_gender", "") or "").strip():
                product.source_gender = product.gender
        if "gender" in reset_to_default:
            product.gender_is_manual = False
            product.gender = self._normalize_gender(getattr(product, "source_gender", None) or product.gender)

        if "manual_weight_grams" in reset_to_default:
            product.manual_weight_grams = None
        elif "manual_weight_grams" in payload:
            product.manual_weight_grams = self._int_or_none(payload.get("manual_weight_grams"))

        if "filter_slugs" in payload or "custom_catalog_slugs" in payload:
            self._replace_taxonomy_links(
                product_id=int(product.id),
                filter_slugs=payload.get("filter_slugs") if "filter_slugs" in payload else None,
                custom_catalog_slugs=payload.get("custom_catalog_slugs") if "custom_catalog_slugs" in payload else None,
            )

        images_patch = payload.get("images") if isinstance(payload.get("images"), dict) else None
        gallery_listing = self._listing_for_gallery_or_error(product=product, listing_id=self._int_or_none(payload.get("gallery_listing_id")))
        if "images" in reset_to_default:
            self.products.replace_gallery_scope_with_source_images(
                product_id=int(product.id),
                listing_id=int(gallery_listing.id),
                listing_images=gallery_listing.images,
            )
        elif images_patch is not None:
            hidden_source_image_urls = [str(url).strip() for url in images_patch.get("hidden_source_image_urls") or [] if str(url).strip()]
            manual_image_urls = [str(url).strip() for url in images_patch.get("manual_image_urls") or [] if str(url).strip()]
            manual_image_order = [str(url).strip() for url in images_patch.get("manual_image_order") or [] if str(url).strip()]
            self._replace_gallery_scope(
                product_id=int(product.id),
                listing_id=int(gallery_listing.id),
                hidden_source_image_urls=hidden_source_image_urls,
                manual_image_urls=manual_image_urls,
                manual_image_order=manual_image_order,
            )

        if brand_override_changed:
            self.db.flush()
            self.db.expire(product)
            DesignerCatalogSyncService(self.db).reconcile(sync_product_links=True)
        self._sync_weight_state(product=product, listing=listing)
        self.db.flush()
        if "filter_slugs" in payload:
            self.filter_assignments.enqueue_product_ids_after_commit([int(product.id)])

    def update_manual_variants(self, *, product_id: int, variants: object) -> None:
        product = self._product_or_error(product_id)
        if not self._can_edit_manual_variants(product):
            raise ValidationError("variants can be edited only for a personal source without sync binding")

        listing = self._manual_listing_or_error(product)
        normalized_variants = self._normalize_manual_variants(variants)
        self.products.replace_variants(listing_id=int(listing.id), variants=normalized_variants)
        listing.orderability_status = "orderable" if any(bool(item.get("is_orderable")) for item in normalized_variants) else "sold_out"
        listing.status_reason = None
        self._sync_weight_state(product=product, listing=listing)
        self.db.flush()

    def bulk_update_products(self, *, product_ids: list[int], payload: dict) -> list[int]:
        normalized_ids: list[int] = []
        seen: set[int] = set()
        for raw_product_id in product_ids:
            product_id = int(raw_product_id)
            if product_id <= 0 or product_id in seen:
                continue
            seen.add(product_id)
            normalized_ids.append(product_id)
        if not normalized_ids:
            raise ValidationError("product_ids is required")

        supports_gender = "gender" in payload
        if not supports_gender:
            raise ValidationError("bulk update payload is empty")

        products = self.products.list_products_by_ids(normalized_ids, include_merged=False)
        found_ids = {int(product.id) for product in products}
        missing_ids = [product_id for product_id in normalized_ids if product_id not in found_ids]
        if missing_ids:
            raise NotFoundError(f"Не найдены товары: {', '.join(str(product_id) for product_id in missing_ids)}")

        for product in products:
            if supports_gender:
                product.gender = self._normalize_gender(payload.get("gender"))
                product.gender_is_manual = True
                if not str(getattr(product, "source_gender", "") or "").strip():
                    product.source_gender = product.gender
            listing = self._primary_listing_or_error(product)
            self._sync_weight_state(product=product, listing=listing)

        self.db.flush()
        return [int(product.id) for product in products]

    def create_manual_product(self, payload: dict) -> int:
        with self.db.begin_nested():
            source = self.sources.ensure_manual_source()
            product, title, normalized_orderability_status = self._create_product_base(payload)
            listing = self.products.create_listing(
                source_id=int(source.id),
                external_id=None,
                url=f"manual://product/{product.id}",
                handle=f"manual-{product.id}",
                source_title=title,
                source_description_text=str(payload.get("description_text") or "").strip() or None,
                source_description_html=str(payload.get("description_html") or "").strip() or None,
                source_weight_grams=None,
                source_designer_raw=str(payload.get("designer_name") or "").strip() or None,
                source_category_raw=str(payload.get("source_category_name") or "").strip() or None,
                orderability_status=normalized_orderability_status,
                status_reason=None,
                ingest_mode="manual",
            )
            self.products.ensure_membership(product_id=int(product.id), listing_id=int(listing.id))
            product.primary_listing_id = int(listing.id)

            variants = self._normalize_manual_variants(payload.get("variants") or [])
            self.products.replace_variants(listing_id=int(listing.id), variants=variants)

            manual_image_ids = [int(value) for value in payload.get("manual_image_asset_ids") or [] if int(value) > 0]
            manual_urls = [f"/api/v1/products/images/{image_id}" for image_id in manual_image_ids]
            self._replace_gallery_scope(
                product_id=int(product.id),
                listing_id=int(listing.id),
                hidden_source_image_urls=[],
                manual_image_urls=manual_urls,
                manual_image_order=manual_urls,
            )

            self._replace_taxonomy_links(
                product_id=int(product.id),
                filter_slugs=payload.get("filter_slugs"),
                custom_catalog_slugs=payload.get("custom_catalog_slugs"),
            )
            DesignerCatalogSyncService(self.db).reconcile(sync_product_links=True)
            self._sync_weight_state(product=product, listing=listing)
            self.db.flush()
            self.filter_assignments.enqueue_product_ids_after_commit([int(product.id)])
            return int(product.id)

    def update_manual_product(self, *, product_id: int, payload: dict) -> None:
        product = self._product_or_error(product_id)
        listing = self._manual_listing_or_error(product)
        next_orderability_status = self._normalize_orderability_status(
            payload.get("orderability_status") if "orderability_status" in payload else getattr(listing, "orderability_status", None)
        )
        next_availability_mode = self._normalize_availability_mode(
            payload.get("availability_mode") if "availability_mode" in payload else getattr(product, "availability_mode", None)
        )
        self._validate_in_stock_availability(
            availability_mode=next_availability_mode,
            orderability_status=next_orderability_status,
        )

        if "title" in payload:
            title = str(payload.get("title") or "").strip()
            if not title:
                raise ValidationError("title is required")
            listing.source_title = title
        if "designer_id" in payload:
            product.designer_id = self._int_or_none(payload.get("designer_id"))
        if "gender" in payload:
            product.gender = self._normalize_gender(payload.get("gender"))
            product.gender_is_manual = True
            if not str(getattr(product, "source_gender", "") or "").strip():
                product.source_gender = product.gender
        if "availability_mode" in payload:
            product.availability_mode = next_availability_mode
        if "visibility_status" in payload:
            product.visibility_status = self._normalize_visibility_status(payload.get("visibility_status"))
        if "description_text" in payload:
            listing.source_description_text = str(payload.get("description_text") or "").strip() or None
        if "description_html" in payload:
            listing.source_description_html = str(payload.get("description_html") or "").strip() or None
        designer_state_changed = False
        if "designer_name" in payload:
            listing.source_designer_raw = str(payload.get("designer_name") or "").strip() or None
            designer_state_changed = True
        if "source_category_name" in payload:
            listing.source_category_raw = str(payload.get("source_category_name") or "").strip() or None
        if "manual_weight_grams" in payload:
            product.manual_weight_grams = self._int_or_none(payload.get("manual_weight_grams"))
        if "orderability_status" in payload:
            listing.orderability_status = next_orderability_status
            listing.status_reason = None
            designer_state_changed = True

        if "variants" in payload:
            variants = self._normalize_manual_variants(payload.get("variants") or [])
            self.products.replace_variants(listing_id=int(listing.id), variants=variants)

        if "manual_image_asset_ids" in payload:
            manual_image_ids = [int(value) for value in payload.get("manual_image_asset_ids") or [] if int(value) > 0]
            manual_urls = [f"/api/v1/products/images/{image_id}" for image_id in manual_image_ids]
            gallery_listing = self._listing_for_gallery_or_error(product=product, listing_id=self._int_or_none(payload.get("gallery_listing_id")))
            self._replace_gallery_scope(
                product_id=int(product.id),
                listing_id=int(gallery_listing.id),
                hidden_source_image_urls=[],
                manual_image_urls=manual_urls,
                manual_image_order=manual_urls,
            )
        if "filter_slugs" in payload or "custom_catalog_slugs" in payload:
            self._replace_taxonomy_links(
                product_id=int(product.id),
                filter_slugs=payload.get("filter_slugs") if "filter_slugs" in payload else None,
                custom_catalog_slugs=payload.get("custom_catalog_slugs") if "custom_catalog_slugs" in payload else None,
            )
        if designer_state_changed:
            DesignerCatalogSyncService(self.db).reconcile(sync_product_links=True)
        self._sync_weight_state(product=product, listing=listing)
        self.db.flush()
        if any(key in payload for key in ("title", "source_category_name", "filter_slugs")):
            self.filter_assignments.enqueue_product_ids_after_commit([int(product.id)])

    def _delete_product_storage(self, *, product_id: int, affected_product_ids: set[int]) -> None:
        product = self._product_or_error(product_id)
        affected_product_ids.add(int(product.id))
        listings = self.products.list_product_listings(int(product.id))
        if not listings:
            self.db.delete(product)
            self.db.flush()
            return
        manual_listings = [listing for listing in listings if str(listing.ingest_mode or "") == "manual"]
        if not manual_listings:
            for row in list(product.gallery_images or []):
                self.db.delete(row)
            for listing in listings:
                self.db.delete(listing)
            self.db.delete(product)
            self.db.flush()
            return
        sync_listings = [listing for listing in listings if str(listing.ingest_mode or "") == "sync"]
        for listing in sync_listings:
            detached = self.products.create_product(
                designer_id=product.designer_id,
                gender=str(product.gender),
                source_gender=str(getattr(product, "source_gender", "") or product.gender),
                gender_is_manual=bool(getattr(product, "gender_is_manual", False)),
                availability_mode=str(product.availability_mode),
                lifecycle_status="active",
                visibility_status="visible",
                manual_weight_grams=None,
                weight_rule_id=None,
            )
            self.products.ensure_membership(product_id=int(detached.id), listing_id=int(listing.id))
            detached.primary_listing_id = int(listing.id)
            affected_product_ids.add(int(detached.id))
            self._duplicate_gallery_scope(
                from_product_id=int(product.id),
                to_product_id=int(detached.id),
                listing_id=int(listing.id),
            )
            for row in self.products.list_gallery_scope(product_id=int(product.id), listing_id=int(listing.id)):
                self.db.delete(row)
        for listing in listings:
            if str(listing.ingest_mode or "") == "manual":
                self.db.delete(listing)
        self.db.delete(product)
        self.db.flush()

    def delete_manual_product(self, *, product_id: int) -> None:
        affected_product_ids: set[int] = set()
        if not self._rewrite_combine_component_after_delete(product_id=int(product_id), affected_product_ids=affected_product_ids):
            self._delete_product_storage(product_id=int(product_id), affected_product_ids=affected_product_ids)
        DesignerCatalogSyncService(self.db).reconcile(sync_product_links=True)
        self.filter_assignments.enqueue_product_ids_after_commit(sorted(affected_product_ids))

    def unbind_listing(self, *, product_id: int, listing_id: int) -> int:
        product = self._product_or_error(product_id)
        listing = self.products.get_listing(listing_id)
        if listing is None:
            raise NotFoundError("Листинг не найден")
        if not self._listing_belongs_to_product(product_id=int(product.id), listing_id=int(listing.id)):
            raise ValidationError("listing не принадлежит товару")
        if str(listing.ingest_mode or "") != "sync":
            raise ValidationError("Можно отвязать только sync listing")

        detached = self.products.create_product(
            designer_id=product.designer_id,
            gender=str(product.gender),
            source_gender=str(getattr(product, "source_gender", "") or product.gender),
            gender_is_manual=bool(getattr(product, "gender_is_manual", False)),
            availability_mode=str(product.availability_mode),
            lifecycle_status="active",
            visibility_status="visible",
            manual_weight_grams=None,
            weight_rule_id=None,
        )
        self.products.ensure_membership(product_id=int(detached.id), listing_id=int(listing.id))
        detached.primary_listing_id = int(listing.id)
        affected_product_ids: set[int] = {int(product.id), int(detached.id)}
        self._duplicate_gallery_scope(
            from_product_id=int(product.id),
            to_product_id=int(detached.id),
            listing_id=int(listing.id),
        )
        for row in self.products.list_gallery_scope(product_id=int(product.id), listing_id=int(listing.id)):
            self.db.delete(row)

        remaining_memberships = (
            self.db.query(ProductListingMember)
            .filter(ProductListingMember.product_id == int(product.id))
            .order_by(ProductListingMember.listing_id.asc())
            .all()
        )
        if not remaining_memberships:
            self.db.delete(product)
            self.db.flush()
            DesignerCatalogSyncService(self.db).reconcile(sync_product_links=True)
            self.filter_assignments.enqueue_product_ids_after_commit(affected_product_ids)
            return int(detached.id)

        if int(product.primary_listing_id or 0) == int(listing.id):
            product.primary_listing_id = int(remaining_memberships[0].listing_id)
        self.db.flush()
        DesignerCatalogSyncService(self.db).reconcile(sync_product_links=True)
        self.filter_assignments.enqueue_product_ids_after_commit(affected_product_ids)
        return int(detached.id)
