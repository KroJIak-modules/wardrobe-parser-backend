from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.models import Product, ProductListingGalleryImage, ProductPresentation
from app.repositories.catalog_dedup import CatalogDedupRepository
from app.repositories.catalog_products import CatalogProductRepository
from app.services.catalog.filter_assignment_service import ProductFilterAssignmentService
from app.services.catalog.site_catalog_sort_price_service import SiteCatalogSortPriceService


class DedupServiceV2:
    _DECISION_ACTIONS = {"combine", "keep_left", "keep_right", "reject"}

    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = CatalogProductRepository(db)
        self.decisions = CatalogDedupRepository(db)
        self.filter_assignments = ProductFilterAssignmentService(db)
        self.site_sort_prices = SiteCatalogSortPriceService(db)

    def count_decisions(self) -> int:
        return self.decisions.count_decisions()

    @staticmethod
    def _norm(value: str | None) -> str:
        return " ".join(str(value or "").strip().lower().split())

    @staticmethod
    def _title_tokens(value: str | None) -> list[str]:
        stop_words = {"the", "and", "for", "with", "from", "без", "для", "theory"}
        return [
            token
            for token in DedupServiceV2._norm(value).split()
            if len(token) >= 3 and token not in stop_words
        ]

    def _designer_key(self, payload: dict) -> str:
        return self._norm(
            payload.get("display_designer_name")
            or payload.get("designer_name")
            or payload.get("source_designer_name")
        )

    def _blocking_keys(self, payload: dict) -> set[tuple[str, str]]:
        designer_key = self._designer_key(payload) or "__unknown__"
        title_norm = self._norm(payload.get("title"))
        tokens = self._title_tokens(title_norm)
        keys: set[tuple[str, str]] = set()
        if tokens:
            for token in tokens[:3]:
                keys.add((designer_key, token))
            keys.add((designer_key, title_norm[:10]))
            if len(tokens) > 1:
                keys.add((designer_key, f"{tokens[0]} {tokens[1]}"))
        elif title_norm:
            keys.add((designer_key, title_norm[:10]))
        else:
            keys.add((designer_key, f"id:{int(payload['id'])}"))
        return keys

    @staticmethod
    def _candidate_price(value: Decimal | float | int | None) -> float | None:
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _candidate_effective_weight(
        *,
        manual_weight_grams: int | None,
        source_weight_grams: int | None,
        rule_weight_grams: int | None,
    ) -> int | None:
        if manual_weight_grams is not None and int(manual_weight_grams) > 0:
            return int(manual_weight_grams)
        if source_weight_grams is not None and int(source_weight_grams) > 0:
            return int(source_weight_grams)
        if rule_weight_grams is not None and int(rule_weight_grams) > 0:
            return int(rule_weight_grams)
        return None

    @staticmethod
    def _source_price_summary_for_listing(listing) -> dict[str, Any] | None:
        if listing is None:
            return None
        variants = [variant for variant in list(getattr(listing, "variants", []) or []) if getattr(variant, "price_amount", None) is not None]
        if not variants:
            return None
        variants.sort(key=lambda item: (0 if bool(getattr(item, "is_orderable", False)) else 1, float(getattr(item, "price_amount", 0) or 0), int(getattr(item, "position", 0)), int(getattr(item, "id", 0))))
        representative = variants[0]
        baseline_currency = str(getattr(representative, "currency_code", "") or "").upper() or None
        baseline_price = DedupServiceV2._candidate_price(getattr(representative, "price_amount", None))
        has_range = any(
            (str(getattr(variant, "currency_code", "") or "").upper() or None) != baseline_currency
            or DedupServiceV2._candidate_price(getattr(variant, "price_amount", None)) != baseline_price
            for variant in variants[1:]
        )
        compare_at_price = DedupServiceV2._candidate_price(getattr(representative, "compare_at_price_amount", None))
        if compare_at_price is not None and baseline_price is not None and compare_at_price <= baseline_price:
            compare_at_price = None
        return {
            "source_display_price": baseline_price,
            "source_currency": baseline_currency,
            "source_compare_at_price": compare_at_price,
            "source_has_range": has_range,
            "final_display_price": None,
            "final_currency": None,
            "final_compare_at_price": None,
            "final_has_range": False,
            "pricing_manual_required": False,
            "pricing_reason": None,
            "representative_variant_id": int(getattr(representative, "id", 0) or 0) or None,
            "representative_listing_id": int(getattr(listing, "id", 0) or 0) or None,
            "representative_source_ref_id": str(getattr(representative, "source_ref_id", "") or "").strip() or None,
        }

    @staticmethod
    def _row_price_summary(*, price: float | None, currency: str | None) -> dict[str, Any] | None:
        if price is None:
            return None
        normalized_currency = str(currency or "").upper() or None
        return {
            "source_display_price": price,
            "source_currency": normalized_currency,
            "source_compare_at_price": None,
            "source_has_range": False,
            "final_display_price": None,
            "final_currency": None,
            "final_compare_at_price": None,
            "final_has_range": False,
            "pricing_manual_required": False,
            "pricing_reason": None,
            "representative_variant_id": None,
            "representative_listing_id": None,
            "representative_source_ref_id": None,
        }

    def _candidate_payload_from_row(self, row) -> dict:
        variant_price = self._candidate_price(getattr(row, "variant_price_amount", None))
        image_url = str(getattr(row, "image_url", "") or "").strip()
        show_images = bool(getattr(row, "show_images", True))
        title_override = str(getattr(row, "title_override", "") or "").strip()
        source_title = str(getattr(row, "source_title", "") or "").strip()
        source_designer_name = str(getattr(row, "source_designer_raw", "") or "").strip() or None
        designer_name = str(getattr(row, "designer_name", "") or "").strip() or None
        ingest_mode = str(getattr(row, "ingest_mode", "") or "").strip().lower()
        return {
            "id": int(getattr(row, "product_id")),
            "title": title_override or source_title or f"Товар {int(getattr(row, 'product_id'))}",
            "designer_name": designer_name,
            "source_designer_name": source_designer_name,
            "display_designer_name": designer_name or source_designer_name,
            "url": (str(getattr(row, "url", "") or "").strip() or None) if ingest_mode != "manual" else None,
            "price_summary": self._row_price_summary(
                price=variant_price,
                currency=str(getattr(row, "variant_currency_code", "") or "").upper() or None,
            ),
            "visibility_status": str(getattr(row, "visibility_status", "") or "visible"),
            "effective_weight_grams": self._candidate_effective_weight(
                manual_weight_grams=getattr(row, "manual_weight_grams", None),
                source_weight_grams=getattr(row, "source_weight_grams", None),
                rule_weight_grams=getattr(row, "rule_weight_grams", None),
            ),
            "image_urls": [image_url] if image_url and show_images else [],
            "image_ids": [],
            "image_count": 1 if image_url and show_images else 0,
        }

    def _candidate_payload_from_product(self, product: Product) -> dict:
        listing = getattr(product, "primary_listing", None)
        weight_rule = getattr(product, "weight_rule", None)
        designer_name = str(getattr(getattr(product, "designer", None), "name", "") or "").strip() or None
        source_designer_name = str(getattr(listing, "source_designer_raw", "") or "").strip() or None
        presentation = getattr(product, "presentation", None)
        title_override = str(getattr(presentation, "title_override", "") or "").strip()
        source_title = str(getattr(listing, "source_title", "") or "").strip()
        ingest_mode = str(getattr(listing, "ingest_mode", "") or "").strip().lower()
        image_url = ""
        show_images = True
        if listing is not None:
            source_setting = getattr(getattr(listing, "source", None), "setting", None)
            show_images = bool(getattr(source_setting, "show_images", True))
            listing_images = list(getattr(listing, "images", []) or [])
            if listing_images:
                listing_images.sort(key=lambda item: (int(getattr(item, "position", 0)), int(getattr(item, "id", 0))))
                image_url = str(getattr(listing_images[0], "url", "") or "").strip()
        return {
            "id": int(product.id),
            "title": title_override or source_title or f"Товар {int(product.id)}",
            "designer_name": designer_name,
            "source_designer_name": source_designer_name,
            "display_designer_name": designer_name or source_designer_name,
            "url": (str(getattr(listing, "url", "") or "").strip() or None) if listing is not None and ingest_mode != "manual" else None,
            "price_summary": self._source_price_summary_for_listing(listing),
            "visibility_status": str(getattr(product, "visibility_status", "") or "visible"),
            "effective_weight_grams": self._candidate_effective_weight(
                manual_weight_grams=getattr(product, "manual_weight_grams", None),
                source_weight_grams=getattr(listing, "source_weight_grams", None) if listing is not None else None,
                rule_weight_grams=getattr(weight_rule, "weight_grams", None),
            ),
            "image_urls": [image_url] if image_url and show_images else [],
            "image_ids": [],
            "image_count": 1 if image_url and show_images else 0,
        }

    def _candidate_score(self, left: dict, right: dict) -> tuple[float, list[str]]:
        left_title = self._norm(left.get("title"))
        right_title = self._norm(right.get("title"))
        if not left_title or not right_title:
            return 0.0, []
        left_tokens = set(self._title_tokens(left_title))
        right_tokens = set(self._title_tokens(right_title))
        shared_tokens = left_tokens & right_tokens
        left_designer = self._designer_key(left)
        right_designer = self._designer_key(right)
        if left_designer and right_designer and left_designer != right_designer:
            return 0.0, []
        max_title_length = max(len(left_title), len(right_title))
        if abs(len(left_title) - len(right_title)) > max(8, int(max_title_length * 0.45)):
            return 0.0, []
        if not shared_tokens and left_title != right_title and left_title[:8] != right_title[:8]:
            return 0.0, []
        ratio = SequenceMatcher(None, left_title, right_title).ratio()
        reasons: list[str] = []
        if ratio >= 0.9:
            reasons.append("same_title")
        elif ratio >= 0.75:
            reasons.append("close_title")
        if left_designer and left_designer == right_designer:
            reasons.append("same_designer")
            ratio += 0.1
        if shared_tokens:
            reasons.append("shared_tokens")
            ratio += min(0.08, 0.02 * len(shared_tokens))
        if left.get("effective_weight_grams") and right.get("effective_weight_grams") and left["effective_weight_grams"] == right["effective_weight_grams"]:
            reasons.append("same_weight")
            ratio += 0.05
        return min(ratio, 0.99), reasons

    @staticmethod
    def _serialize_gallery_rows(rows) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for row in rows:
            serialized.append(
                {
                    "position": int(row.position),
                    "is_hidden": bool(row.is_hidden),
                    "origin_kind": str(row.origin_kind),
                    "listing_image_id": int(row.listing_image_id) if row.listing_image_id is not None else None,
                    "image_asset_id": int(row.image_asset_id) if row.image_asset_id is not None else None,
                }
            )
        return serialized

    @staticmethod
    def _restore_gallery_rows(product_id: int, listing_id: int, rows: list[dict[str, Any]]) -> list[ProductListingGalleryImage]:
        restored: list[ProductListingGalleryImage] = []
        for row in rows:
            restored.append(
                ProductListingGalleryImage(
                    product_id=int(product_id),
                    listing_id=int(listing_id),
                    listing_image_id=(int(row["listing_image_id"]) if row.get("listing_image_id") is not None else None),
                    image_asset_id=(int(row["image_asset_id"]) if row.get("image_asset_id") is not None else None),
                    position=int(row["position"]),
                    is_hidden=bool(row.get("is_hidden", False)),
                    origin_kind=str(row["origin_kind"]),
                )
            )
        return restored

    @staticmethod
    def _candidate_pair_key(left_product_id: int, right_product_id: int) -> str:
        left_id, right_id = sorted((int(left_product_id), int(right_product_id)))
        return f"{left_id}:{right_id}"

    @staticmethod
    def _serialize_candidate_snapshot(*, left_product_id: int, right_product_id: int, score: float, reasons: list[str]) -> dict[str, Any]:
        left_id, right_id = sorted((int(left_product_id), int(right_product_id)))
        return {
            "left_product_id": left_id,
            "right_product_id": right_id,
            "score": float(score),
            "reasons": list(reasons or []),
        }

    def _compute_candidate_rows_from_payloads(self, payloads: list[dict]) -> list[dict]:
        decided_pair_keys = self._decided_pair_keys()
        candidate_pairs: set[tuple[int, int]] = set()
        block_map: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for payload in payloads:
            for block_key in self._blocking_keys(payload):
                block_map[block_key].append(payload)

        for bucket in block_map.values():
            if len(bucket) < 2:
                continue
            for index, left in enumerate(bucket):
                left_id = int(left["id"])
                for right in bucket[index + 1 :]:
                    right_id = int(right["id"])
                    if left_id == right_id:
                        continue
                    candidate_pairs.add((min(left_id, right_id), max(left_id, right_id)))

        payload_by_id = {int(payload["id"]): payload for payload in payloads}
        candidates: list[dict] = []
        for left_id, right_id in candidate_pairs:
            pair_key = self._candidate_pair_key(left_id, right_id)
            if pair_key in decided_pair_keys:
                continue
            left = payload_by_id.get(left_id)
            right = payload_by_id.get(right_id)
            if left is None or right is None:
                continue
            score, reasons = self._candidate_score(left, right)
            if score < 0.75:
                continue
            candidates.append(
                {
                    "left_product_id": left_id,
                    "right_product_id": right_id,
                    "score": round(score, 4),
                    "reasons": list(reasons),
                    "left": left,
                    "right": right,
                }
            )
        candidates.sort(
            key=lambda item: (
                -float(item["score"]),
                -int(item["right_product_id"]),
                -int(item["left_product_id"]),
            )
        )
        return candidates

    def list_candidates(self, *, limit: int, offset: int) -> dict:
        rows = self.decisions.list_candidates(limit=max(1, int(limit)) + 1, offset=offset)
        has_more = len(rows) > int(limit)
        page_rows = rows[: max(1, int(limit))]
        total = self.decisions.count_candidates()
        product_ids = sorted(
            {
                int(row.left_product_id)
                for row in page_rows
            }
            | {
                int(row.right_product_id)
                for row in page_rows
            }
        )
        products = self.products.list_products_for_dedup_by_ids(product_ids)
        payloads_by_id = {int(product.id): self._candidate_payload_from_product(product) for product in products}
        items: list[dict] = []
        for row in page_rows:
            left_id = int(row.left_product_id)
            right_id = int(row.right_product_id)
            left = payloads_by_id.get(left_id)
            right = payloads_by_id.get(right_id)
            if left is None or right is None:
                continue
            items.append(
                {
                    "pair_key": self._candidate_pair_key(left_id, right_id),
                    "score": round(float(row.score), 4),
                    "reasons": list(getattr(row, "reasons", []) or []),
                    "left": left,
                    "right": right,
                }
            )
        return {
            "items": items,
            "total": int(total),
            "has_more": bool(has_more),
            "limit": int(limit),
            "offset": int(offset),
        }

    def scan_and_replace_candidates(self) -> int:
        payloads: list[dict] = []
        rows = self.products.list_dedup_candidate_rows()
        for row in rows:
            payload = self._candidate_payload_from_row(row)
            if payload.get("visibility_status") == "hidden":
                continue
            payloads.append(payload)
        candidates = self._compute_candidate_rows_from_payloads(payloads)
        serialized = [
            self._serialize_candidate_snapshot(
                left_product_id=int(item["left_product_id"]),
                right_product_id=int(item["right_product_id"]),
                score=float(item["score"]),
                reasons=list(item["reasons"]),
            )
            for item in candidates
        ]
        self.decisions.replace_candidates(candidates=serialized)
        return len(serialized)

    @classmethod
    def _normalize_decision_action(cls, value: str | None, *, fallback: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in cls._DECISION_ACTIONS:
            return normalized
        return str(fallback or "").strip().lower()

    def describe_decision_action(self, decision) -> str:
        decision_kind = str(getattr(decision, "decision_kind", "") or "").strip().lower()
        undo_payload = getattr(decision, "undo_payload", None)
        if isinstance(undo_payload, dict):
            return self._normalize_decision_action(undo_payload.get("decision_action"), fallback=decision_kind)
        return self._normalize_decision_action(None, fallback=decision_kind)

    @staticmethod
    def _decision_kind_from_merge_mode(merge_mode: str | None) -> str:
        normalized = str(merge_mode or "").strip().lower()
        if normalized in {"combine", "keep_left", "keep_right"}:
            return normalized
        return "combine"

    @staticmethod
    def _product_snapshot(product: Product) -> dict[str, Any]:
        return {
            "product_id": int(product.id),
            "lifecycle_status": str(product.lifecycle_status or "active"),
            "primary_listing_id": int(product.primary_listing_id) if product.primary_listing_id is not None else None,
            "source_gender": str(getattr(product, "source_gender", "") or ""),
            "gender_is_manual": bool(getattr(product, "gender_is_manual", False)),
            "is_manually_hidden": bool(getattr(product, "is_manually_hidden", False)),
            "dedup_status": str(getattr(product, "dedup_status", "") or "independent"),
            "dedup_decision_id": (
                int(getattr(product, "dedup_decision_id"))
                if getattr(product, "dedup_decision_id", None) is not None
                else None
            ),
            "dedup_target_product_id": (
                int(getattr(product, "dedup_target_product_id"))
                if getattr(product, "dedup_target_product_id", None) is not None
                else None
            ),
        }

    def _restore_product_snapshot(self, snapshot: dict[str, Any]) -> None:
        product_id = int(snapshot["product_id"])
        product = self.products.get_product(product_id)
        self.products.set_product_lifecycle_status(
            product_id=product_id,
            lifecycle_status=str(snapshot.get("lifecycle_status") or "active"),
        )
        self.products.set_product_primary_listing(
            product_id=product_id,
            primary_listing_id=(int(snapshot["primary_listing_id"]) if snapshot.get("primary_listing_id") is not None else None),
        )
        if product is not None and "source_gender" in snapshot:
            product.source_gender = str(snapshot.get("source_gender") or product.gender)
        if product is not None and "gender_is_manual" in snapshot:
            product.gender_is_manual = bool(snapshot.get("gender_is_manual"))
        if product is not None and "is_manually_hidden" in snapshot:
            product.is_manually_hidden = bool(snapshot.get("is_manually_hidden"))
        self.products.set_product_dedup_state(
            product_id=product_id,
            dedup_status=str(snapshot.get("dedup_status") or "independent"),
            dedup_decision_id=(int(snapshot["dedup_decision_id"]) if snapshot.get("dedup_decision_id") is not None else None),
            dedup_target_product_id=(
                int(snapshot["dedup_target_product_id"])
                if snapshot.get("dedup_target_product_id") is not None
                else None
            ),
        )

    def reject(self, *, product_ids: list[int]) -> None:
        candidate = self.decisions.get_candidate_by_pair(product_ids=product_ids)
        decision = self.decisions.create_decision(decision_kind="reject", created_product_id=None)
        undo_payload: dict[str, Any] = {"decision_action": "reject"}
        if candidate is not None:
            undo_payload["candidate"] = self._serialize_candidate_snapshot(
                left_product_id=int(candidate.left_product_id),
                right_product_id=int(candidate.right_product_id),
                score=float(candidate.score),
                reasons=list(candidate.reasons or []),
            )
        decision.undo_payload = undo_payload
        self.decisions.add_members(decision_id=int(decision.id), product_ids=product_ids)
        self.decisions.delete_candidate_by_pair(product_ids=product_ids)

    def merge(
        self,
        *,
        product_ids: list[int],
        primary_product_id: int | None = None,
        primary_listing_id: int | None = None,
        merge_mode: str | None = None,
    ) -> int:
        unique_product_ids = []
        seen_product_ids: set[int] = set()
        for raw_product_id in product_ids:
            product_id = int(raw_product_id)
            if product_id <= 0 or product_id in seen_product_ids:
                continue
            seen_product_ids.add(product_id)
            unique_product_ids.append(product_id)
        if len(unique_product_ids) < 2:
            raise ValidationError("Для merge нужно минимум два разных товара")

        normalized_merge_mode = str(merge_mode or "").strip().lower()
        if normalized_merge_mode and normalized_merge_mode not in {"combine", "keep_left", "keep_right"}:
            raise ValidationError("Неизвестный merge_mode")
        if normalized_merge_mode == "keep_left":
            primary_product_id = unique_product_ids[0]
        elif normalized_merge_mode == "keep_right":
            primary_product_id = unique_product_ids[-1]
        decision_kind = self._decision_kind_from_merge_mode(normalized_merge_mode)

        products = []
        for product_id in unique_product_ids:
            product = self.products.get_product(product_id)
            if product is None:
                raise ValidationError(f"Товар {product_id} не найден")
            if str(getattr(product, "dedup_status", "") or "independent") != "independent":
                raise ValidationError(f"Товар {product_id} уже исключен дедубликацией")
            products.append(product)

        if primary_product_id is None:
            primary_product_id = unique_product_ids[0]
        primary = next((product for product in products if int(product.id) == int(primary_product_id)), None)
        if primary is None:
            raise ValidationError("primary_product_id должен входить в product_ids")
        primary_snapshot = {
            "product_id": int(primary.id),
            "designer_id": primary.designer_id,
            "gender": primary.gender,
            "source_gender": str(getattr(primary, "source_gender", "") or primary.gender),
            "gender_is_manual": bool(getattr(primary, "gender_is_manual", False)),
            "availability_mode": primary.availability_mode,
            "manual_weight_grams": primary.manual_weight_grams,
            "weight_rule_id": primary.weight_rule_id,
            "is_manually_hidden": bool(getattr(primary, "is_manually_hidden", False)),
            "primary_listing_id": (int(primary.primary_listing_id) if primary.primary_listing_id is not None else None),
            "presentation": (
                {
                    "title_override": primary.presentation.title_override,
                    "description_text": primary.presentation.description_text,
                    "description_html": primary.presentation.description_html,
                    "description_visibility": primary.presentation.description_visibility,
                }
                if primary.presentation is not None
                else None
            ),
        }
        candidate = self.decisions.get_candidate_by_pair(product_ids=unique_product_ids)

        created_product_id: int | None = None
        affected_products = list(products)
        hidden_products = list(products)
        if decision_kind == "keep_left":
            hidden_products = [product for product in products if int(product.id) != int(unique_product_ids[0])]
        elif decision_kind == "keep_right":
            hidden_products = [product for product in products if int(product.id) != int(unique_product_ids[-1])]

        undo_payload: dict[str, Any] = {
            "version": 2,
            "decision_action": decision_kind,
            "products": [self._product_snapshot(product) for product in affected_products],
        }
        if candidate is not None:
            undo_payload["candidate"] = self._serialize_candidate_snapshot(
                left_product_id=int(candidate.left_product_id),
                right_product_id=int(candidate.right_product_id),
                score=float(candidate.score),
                reasons=list(candidate.reasons or []),
            )
        if decision_kind == "combine":
            created = self.products.create_product(
                designer_id=primary_snapshot["designer_id"],
                gender=primary_snapshot["gender"],
                source_gender=primary_snapshot["source_gender"],
                gender_is_manual=bool(primary_snapshot.get("gender_is_manual", False)),
                availability_mode=primary_snapshot["availability_mode"],
                manual_weight_grams=primary_snapshot["manual_weight_grams"],
                weight_rule_id=primary_snapshot["weight_rule_id"],
                lifecycle_status="active",
                visibility_status="visible",
                is_manually_hidden=bool(primary_snapshot.get("is_manually_hidden", False)),
                dedup_status="independent",
                dedup_decision_id=None,
                dedup_target_product_id=None,
            )
            created_product_id = int(created.id)
            unique_listings: dict[int, tuple[Any, int]] = {}
            for source_product_id in unique_product_ids:
                for listing in self.products.list_product_listings(int(source_product_id)):
                    unique_listings.setdefault(int(listing.id), (listing, int(source_product_id)))
            allowed_primary_listing_ids = set(unique_listings.keys())
            if primary_listing_id is not None and int(primary_listing_id) not in allowed_primary_listing_ids:
                raise ValidationError("primary_listing_id не входит в объединенный товар")
            selected_primary_listing_id = (
                int(primary_listing_id)
                if primary_listing_id is not None
                else primary_snapshot["primary_listing_id"]
                or (min(allowed_primary_listing_ids) if allowed_primary_listing_ids else None)
            )
            for listing_id, (listing, source_product_id) in unique_listings.items():
                self.products.ensure_included_membership(product_id=created_product_id, listing_id=int(listing_id))
                source_scope_rows = self.products.list_gallery_scope(product_id=source_product_id, listing_id=int(listing_id))
                if source_scope_rows:
                    for row in self._restore_gallery_rows(
                        created_product_id,
                        int(listing_id),
                        self._serialize_gallery_rows(source_scope_rows),
                    ):
                        self.db.add(row)
                else:
                    self.products.replace_gallery_scope_with_source_images(
                        product_id=created_product_id,
                        listing_id=int(listing_id),
                        listing_images=listing.images,
                    )
            self.products.set_product_primary_listing(product_id=created_product_id, primary_listing_id=selected_primary_listing_id)
            if primary_snapshot["presentation"] is not None:
                self.db.add(
                    ProductPresentation(
                        product_id=created_product_id,
                        title_override=primary_snapshot["presentation"]["title_override"],
                        description_text=primary_snapshot["presentation"]["description_text"],
                        description_html=primary_snapshot["presentation"]["description_html"],
                        description_visibility=primary_snapshot["presentation"]["description_visibility"],
                    )
                )
        decision = self.decisions.create_decision(decision_kind=decision_kind, created_product_id=created_product_id)
        decision.undo_payload = undo_payload
        self.decisions.add_members(decision_id=int(decision.id), product_ids=unique_product_ids)

        if hidden_products:
            hidden_status = "combined_source" if decision_kind == "combine" else "hidden_by_keep"
            target_product_id = created_product_id if decision_kind == "combine" else int(primary_product_id)
            for product in hidden_products:
                self.products.set_product_dedup_state(
                    product_id=int(product.id),
                    dedup_status=hidden_status,
                    dedup_decision_id=int(decision.id),
                    dedup_target_product_id=target_product_id,
                )

        self.decisions.delete_candidates_for_product_ids(product_ids=unique_product_ids)
        affected_ids = set(unique_product_ids)
        if created_product_id is not None:
            affected_ids.add(created_product_id)
        self.db.flush()
        self.filter_assignments.enqueue_product_ids_after_commit(sorted(affected_ids))
        self.site_sort_prices.enqueue_product_ids_after_commit(sorted(affected_ids))
        return int(created_product_id or primary_product_id)

    def can_undo_decision(self, decision) -> tuple[bool, str | None]:
        decision_kind = str(getattr(decision, "decision_kind", "") or "").lower()
        if decision_kind == "reject":
            return True, None
        if decision_kind in {"keep_left", "keep_right"}:
            return True, None
        if decision_kind != "combine":
            return False, "Неизвестный тип решения"
        created_product_id = int(getattr(decision, "created_product_id", 0) or 0)
        undo_payload = getattr(decision, "undo_payload", None) or {}
        if created_product_id <= 0 or not isinstance(undo_payload, dict):
            return False, "Для этого решения нет данных для отката"
        if self.decisions.has_dependent_decisions(created_product_id=created_product_id, exclude_decision_id=int(decision.id)):
            return False, "Есть более новые решения, зависящие от этого объединения"
        created_product = self.products.get_product(created_product_id)
        if created_product is None:
            return False, "Объединенный товар уже недоступен"
        return True, None

    def undo(self, *, decision_id: int) -> None:
        decision = self.decisions.get_decision(decision_id)
        if decision is None:
            raise NotFoundError("Решение не найдено")
        decision_kind = str(decision.decision_kind or "").lower()
        if decision_kind == "reject":
            undo_payload = decision.undo_payload or {}
            candidate_snapshot = undo_payload.get("candidate") if isinstance(undo_payload, dict) else None
            if isinstance(candidate_snapshot, dict):
                self.decisions.restore_candidate(
                    left_product_id=int(candidate_snapshot["left_product_id"]),
                    right_product_id=int(candidate_snapshot["right_product_id"]),
                    score=float(candidate_snapshot.get("score") or 0.0),
                    reasons=[str(item) for item in list(candidate_snapshot.get("reasons") or [])],
                )
            self.decisions.delete_decision(decision)
            return

        can_undo, blocked_reason = self.can_undo_decision(decision)
        if not can_undo:
            raise ValidationError(blocked_reason or "Отмена этого решения недоступна")

        undo_payload = decision.undo_payload or {}
        legacy_listing_owners = [
            item
            for item in list(undo_payload.get("listing_owners") or [])
            if isinstance(item, dict) and item.get("listing_id") is not None and item.get("product_id") is not None
        ]
        if legacy_listing_owners:
            for item in legacy_listing_owners:
                self.products.ensure_owner_membership(
                    product_id=int(item["product_id"]),
                    listing_id=int(item["listing_id"]),
                )

        legacy_gallery_scopes = [
            item
            for item in list(undo_payload.get("gallery_scopes") or [])
            if isinstance(item, dict) and item.get("product_id") is not None and item.get("listing_id") is not None
        ]
        if legacy_gallery_scopes:
            for scope in legacy_gallery_scopes:
                product_id = int(scope["product_id"])
                listing_id = int(scope["listing_id"])
                for row in self.products.list_gallery_scope(product_id=product_id, listing_id=listing_id):
                    self.db.delete(row)
            self.db.flush()
            for scope in legacy_gallery_scopes:
                product_id = int(scope["product_id"])
                listing_id = int(scope["listing_id"])
                for row in self._restore_gallery_rows(product_id, listing_id, list(scope.get("rows") or [])):
                    self.db.add(row)
            self.db.flush()

        product_snapshots = [
            item
            for item in list(undo_payload.get("products") or [])
            if isinstance(item, dict) and item.get("product_id") is not None
        ]
        for snapshot in product_snapshots:
            self._restore_product_snapshot(snapshot)

        candidate_snapshot = undo_payload.get("candidate") if isinstance(undo_payload, dict) else None
        if isinstance(candidate_snapshot, dict):
            self.decisions.restore_candidate(
                left_product_id=int(candidate_snapshot["left_product_id"]),
                right_product_id=int(candidate_snapshot["right_product_id"]),
                score=float(candidate_snapshot.get("score") or 0.0),
                reasons=[str(item) for item in list(candidate_snapshot.get("reasons") or [])],
            )
        self.decisions.delete_decision(decision)
        affected_ids = {int(snapshot["product_id"]) for snapshot in product_snapshots}
        if decision_kind == "combine":
            created_product_id = int(decision.created_product_id or 0)
            if created_product_id <= 0:
                raise ValidationError("Объединенный товар не найден")
            self.products.delete_product_hard(created_product_id)
            affected_ids.add(created_product_id)
        self.filter_assignments.enqueue_product_ids_after_commit(sorted(affected_ids))
        self.site_sort_prices.enqueue_product_ids_after_commit(sorted(affected_ids))

    def _decided_pair_keys(self) -> set[str]:
        decided: set[str] = set()
        for decision in self.decisions.list_decisions(limit=10_000, offset=0):
            member_ids = sorted({int(member.product_id) for member in getattr(decision, "members", [])})
            if len(member_ids) < 2:
                continue
            for index, left_id in enumerate(member_ids):
                for right_id in member_ids[index + 1 :]:
                    decided.add(f"{left_id}:{right_id}")
        return decided
