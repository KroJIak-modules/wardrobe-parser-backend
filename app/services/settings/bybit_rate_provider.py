"""Bybit P2P public rate provider with cache and USDT bucket strategy."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from statistics import median
from typing import Any

import requests


_BYBIT_P2P_ONLINE_URL = "https://api2.bybit.com/fiat/otc/item/online"


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed:
        return float(default)
    return parsed


@dataclass(slots=True)
class BybitAd:
    """One BUY ad from Bybit P2P in normalized numeric shape."""

    order_id: str
    nickname: str
    price_rub_per_usdt: float
    min_rub: float
    max_rub: float
    quantity_usdt: float


@dataclass(slots=True)
class BybitBucketQuote:
    """Effective quote for one USDT bucket."""

    bucket_usdt: float
    rate_rub_per_usdt: float
    pay_rub: float
    source: str
    order_id: str | None = None
    nickname: str | None = None
    min_rub: float | None = None
    max_rub: float | None = None
    quantity_usdt: float | None = None
    legs: list[dict[str, Any]] | None = None


@dataclass(slots=True)
class BybitRateSnapshot:
    """Cached market snapshot and derived bucket table."""

    representative_rate: float
    bucket_quotes: list[BybitBucketQuote]
    fetched_at: float
    ads_total: int
    ads_used: int
    outliers_dropped: int


@dataclass(slots=True)
class CachedSnapshot:
    snapshot: BybitRateSnapshot | None
    expires_at: float
    error_message: str | None = None


class BybitP2PRateProvider:
    """Fetch BUY-side USDT rates and prebuild realistic price buckets."""

    _cache: dict[str, CachedSnapshot] = {}
    _lock = threading.Lock()

    @classmethod
    def get_rate(
        cls,
        *,
        fiat: str,
        asset: str = "USDT",
        timeout_sec: float = 8.0,
        cache_sec: int = 300,
        ads_limit: int = 60,
        bucket_step_usdt: int = 50,
        bucket_max_usdt: int = 1000,
        outlier_max_deviation_ratio: float = 0.08,
    ) -> float:
        snapshot = cls.get_snapshot(
            fiat=fiat,
            asset=asset,
            timeout_sec=timeout_sec,
            cache_sec=cache_sec,
            ads_limit=ads_limit,
            bucket_step_usdt=bucket_step_usdt,
            bucket_max_usdt=bucket_max_usdt,
            outlier_max_deviation_ratio=outlier_max_deviation_ratio,
        )
        return snapshot.representative_rate

    @classmethod
    def get_cached_snapshot(cls, *, fiat: str, asset: str = "USDT") -> BybitRateSnapshot | None:
        key = f"{asset.upper()}:{fiat.upper()}"
        now = time.time()
        with cls._lock:
            cached = cls._cache.get(key)
            if cached and cached.expires_at > now and cached.snapshot is not None:
                return cached.snapshot
        return None

    @classmethod
    def get_cached_error(cls, *, fiat: str, asset: str = "USDT") -> str | None:
        key = f"{asset.upper()}:{fiat.upper()}"
        now = time.time()
        with cls._lock:
            cached = cls._cache.get(key)
            if cached and cached.expires_at > now:
                return cached.error_message
        return None

    @classmethod
    def get_snapshot(
        cls,
        *,
        fiat: str,
        asset: str = "USDT",
        timeout_sec: float = 8.0,
        cache_sec: int = 300,
        ads_limit: int = 60,
        bucket_step_usdt: int = 50,
        bucket_max_usdt: int = 1000,
        outlier_max_deviation_ratio: float = 0.08,
    ) -> BybitRateSnapshot:
        key = f"{asset.upper()}:{fiat.upper()}"
        now = time.time()
        with cls._lock:
            cached = cls._cache.get(key)
            if cached and cached.expires_at > now:
                if cached.snapshot is None:
                    raise RuntimeError(cached.error_message or "Bybit snapshot is temporarily unavailable")
                return cached.snapshot

        try:
            snapshot = cls._fetch_snapshot(
                fiat=fiat,
                asset=asset,
                timeout_sec=timeout_sec,
                ads_limit=max(10, int(ads_limit)),
                bucket_step_usdt=max(1, int(bucket_step_usdt)),
                bucket_max_usdt=max(1, int(bucket_max_usdt)),
                outlier_max_deviation_ratio=max(0.0, float(outlier_max_deviation_ratio)),
            )
        except Exception as exc:
            with cls._lock:
                cls._cache[key] = CachedSnapshot(
                    snapshot=None,
                    expires_at=now + max(30.0, float(cache_sec) / 2.0),
                    error_message=str(exc) or "Bybit fetch failed",
                )
            raise

        with cls._lock:
            cls._cache[key] = CachedSnapshot(
                snapshot=snapshot,
                expires_at=now + float(cache_sec),
                error_message=None,
            )
        return snapshot

    @classmethod
    def _fetch_snapshot(
        cls,
        *,
        fiat: str,
        asset: str,
        timeout_sec: float,
        ads_limit: int,
        bucket_step_usdt: int,
        bucket_max_usdt: int,
        outlier_max_deviation_ratio: float,
    ) -> BybitRateSnapshot:
        response = requests.post(
            _BYBIT_P2P_ONLINE_URL,
            json={
                "tokenId": asset.upper(),
                "currencyId": fiat.upper(),
                "side": "1",  # buy
                "size": str(max(10, ads_limit)),
                "page": "1",
                "amount": "",
            },
            timeout=timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()
        rows = ((payload.get("result") or {}).get("items") or [])
        ads = cls._normalize_ads(rows)
        if not ads:
            raise RuntimeError("Bybit P2P returned no valid ads")

        filtered_ads, outliers_dropped = cls._drop_price_outliers(
            ads=ads,
            max_deviation_ratio=outlier_max_deviation_ratio,
        )
        representative_rate = cls._representative_rate(filtered_ads)
        bucket_quotes = cls._build_bucket_quotes(
            ads=filtered_ads,
            bucket_step_usdt=bucket_step_usdt,
            bucket_max_usdt=bucket_max_usdt,
        )
        return BybitRateSnapshot(
            representative_rate=representative_rate,
            bucket_quotes=bucket_quotes,
            fetched_at=time.time(),
            ads_total=len(ads),
            ads_used=len(filtered_ads),
            outliers_dropped=outliers_dropped,
        )

    @classmethod
    def _normalize_ads(cls, rows: list[dict[str, Any]]) -> list[BybitAd]:
        output: list[BybitAd] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = _safe_float(row.get("price"))
            if price <= 0:
                continue
            min_rub = max(0.0, _safe_float(row.get("minAmount")))
            max_rub = _safe_float(row.get("maxAmount"))
            if max_rub <= 0:
                max_rub = float("inf")
            quantity_usdt = max(0.0, _safe_float(row.get("quantity")))
            if quantity_usdt <= 0:
                continue
            output.append(
                BybitAd(
                    order_id=str(row.get("id") or ""),
                    nickname=str(row.get("nickName") or ""),
                    price_rub_per_usdt=price,
                    min_rub=min_rub,
                    max_rub=max_rub,
                    quantity_usdt=quantity_usdt,
                )
            )
        output.sort(key=lambda item: item.price_rub_per_usdt)
        return output

    @classmethod
    def _drop_price_outliers(
        cls,
        *,
        ads: list[BybitAd],
        max_deviation_ratio: float,
    ) -> tuple[list[BybitAd], int]:
        if not ads:
            return [], 0
        if max_deviation_ratio <= 0:
            return list(ads), 0
        prices = sorted(item.price_rub_per_usdt for item in ads if item.price_rub_per_usdt > 0)
        if not prices:
            return list(ads), 0
        mid = float(median(prices))
        if mid <= 0:
            return list(ads), 0
        lower = mid * (1.0 - max_deviation_ratio)
        upper = mid * (1.0 + max_deviation_ratio)
        filtered = [item for item in ads if lower <= item.price_rub_per_usdt <= upper]
        if not filtered:
            return list(ads), 0
        dropped = max(0, len(ads) - len(filtered))
        filtered.sort(key=lambda item: item.price_rub_per_usdt)
        return filtered, dropped

    @classmethod
    def _representative_rate(cls, ads: list[BybitAd]) -> float:
        prices = sorted(item.price_rub_per_usdt for item in ads if item.price_rub_per_usdt > 0)
        if not prices:
            raise RuntimeError("Bybit snapshot has no usable prices")
        top = prices[: min(10, len(prices))]
        return float(median(top))

    @classmethod
    def _build_bucket_quotes(
        cls,
        *,
        ads: list[BybitAd],
        bucket_step_usdt: int,
        bucket_max_usdt: int,
    ) -> list[BybitBucketQuote]:
        if bucket_step_usdt <= 0 or bucket_max_usdt <= 0:
            return []
        bucket_quotes: list[BybitBucketQuote] = []
        bucket_count = max(1, int(bucket_max_usdt // bucket_step_usdt))
        for index in range(1, bucket_count + 1):
            bucket_usdt = float(index * bucket_step_usdt)
            best_quote: BybitBucketQuote | None = None
            for ad in ads:
                candidate = cls._build_single_ad_bucket_quote(ad=ad, bucket_usdt=bucket_usdt)
                if candidate is None:
                    continue
                if best_quote is None or candidate.pay_rub < best_quote.pay_rub:
                    best_quote = candidate
            combo_candidate = cls._build_multi_ad_bucket_quote(ads=ads, bucket_usdt=bucket_usdt)
            if combo_candidate is not None:
                if best_quote is None or combo_candidate.pay_rub < best_quote.pay_rub:
                    best_quote = combo_candidate
            if best_quote is not None:
                bucket_quotes.append(best_quote)
        return bucket_quotes

    @classmethod
    def _build_single_ad_bucket_quote(
        cls,
        *,
        ad: BybitAd,
        bucket_usdt: float,
    ) -> BybitBucketQuote | None:
        if bucket_usdt <= 0 or ad.price_rub_per_usdt <= 0:
            return None
        max_usdt_by_amount = ad.max_rub / ad.price_rub_per_usdt if ad.max_rub != float("inf") else float("inf")
        if min(ad.quantity_usdt, max_usdt_by_amount) + 1e-9 < bucket_usdt:
            return None
        pay_rub = bucket_usdt * ad.price_rub_per_usdt
        if pay_rub + 1e-9 < ad.min_rub:
            return None
        if ad.max_rub != float("inf") and pay_rub - 1e-9 > ad.max_rub:
            return None
        return BybitBucketQuote(
            bucket_usdt=bucket_usdt,
            rate_rub_per_usdt=ad.price_rub_per_usdt,
            pay_rub=pay_rub,
            source="single_ad",
            order_id=ad.order_id or None,
            nickname=ad.nickname or None,
            min_rub=ad.min_rub,
            max_rub=None if ad.max_rub == float("inf") else ad.max_rub,
            quantity_usdt=ad.quantity_usdt,
            legs=[
                {
                    "order_id": ad.order_id or None,
                    "nickname": ad.nickname or None,
                    "usdt": round(float(bucket_usdt), 4),
                    "rate_rub_per_usdt": round(float(ad.price_rub_per_usdt), 6),
                    "pay_rub": round(float(pay_rub), 2),
                }
            ],
        )

    @classmethod
    def _build_multi_ad_bucket_quote(
        cls,
        *,
        ads: list[BybitAd],
        bucket_usdt: float,
    ) -> BybitBucketQuote | None:
        target = float(bucket_usdt)
        if target <= 0:
            return None
        prepared = cls._prepare_multi_ad_candidates(ads=ads)
        if not prepared:
            return None
        allocations, total_pay = cls._allocate_multi_ad(prepared=prepared, target_usdt=target)
        if not allocations:
            return None
        if total_pay <= 0:
            return None

        legs = [
            {
                "order_id": ad.order_id or None,
                "nickname": ad.nickname or None,
                "usdt": round(float(usdt), 4),
                "rate_rub_per_usdt": round(float(ad.price_rub_per_usdt), 6),
                "pay_rub": round(float(usdt * ad.price_rub_per_usdt), 2),
            }
            for ad, usdt in allocations
        ]
        leg_ids = [str(item["order_id"]) for item in legs if item.get("order_id")]
        leg_names = [str(item["nickname"]) for item in legs if item.get("nickname")]
        min_rub = min(float(item["pay_rub"]) for item in legs) if legs else None
        max_rub = max(float(item["pay_rub"]) for item in legs) if legs else None
        total_usdt = sum(float(item["usdt"]) for item in legs)
        return BybitBucketQuote(
            bucket_usdt=target,
            rate_rub_per_usdt=float(total_pay / target),
            pay_rub=float(total_pay),
            source="multi_ad",
            order_id=",".join(leg_ids) if leg_ids else None,
            nickname=", ".join(leg_names) if leg_names else None,
            min_rub=min_rub,
            max_rub=max_rub,
            quantity_usdt=total_usdt,
            legs=legs,
        )

    @classmethod
    def _prepare_multi_ad_candidates(cls, *, ads: list[BybitAd]) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        for ad in ads:
            if ad.price_rub_per_usdt <= 0:
                continue
            max_usdt_by_amount = ad.max_rub / ad.price_rub_per_usdt if ad.max_rub != float("inf") else float("inf")
            max_usdt = min(float(ad.quantity_usdt), float(max_usdt_by_amount))
            if max_usdt <= 0:
                continue
            min_usdt = max(0.0, float(ad.min_rub) / float(ad.price_rub_per_usdt))
            if max_usdt + 1e-9 < min_usdt:
                continue
            prepared.append(
                {
                    "ad": ad,
                    "min_usdt": min_usdt,
                    "max_usdt": max_usdt,
                }
            )
        prepared.sort(key=lambda item: item["ad"].price_rub_per_usdt)
        return prepared

    @classmethod
    def _allocate_multi_ad(
        cls,
        *,
        prepared: list[dict[str, Any]],
        target_usdt: float,
    ) -> tuple[list[tuple[BybitAd, float]], float]:
        remaining = float(target_usdt)
        allocations: list[list[Any]] = []

        for row in prepared:
            if remaining <= 1e-9:
                break
            min_usdt = float(row["min_usdt"])
            max_usdt = float(row["max_usdt"])
            if max_usdt <= 1e-9:
                continue
            take = min(max_usdt, remaining)
            if take + 1e-9 < min_usdt:
                continue
            if take <= 1e-9:
                continue
            allocations.append([row, float(take)])
            remaining -= float(take)

        if remaining > 1e-9:
            resolved = cls._try_close_remaining_with_rebalance(
                prepared=prepared,
                allocations=allocations,
                remaining=remaining,
            )
            if not resolved:
                return [], 0.0
            remaining = 0.0

        fixed = cls._normalize_allocations_to_target(allocations=allocations, target_usdt=target_usdt)
        if not fixed:
            return [], 0.0

        result: list[tuple[BybitAd, float]] = []
        total_pay = 0.0
        for row, amount_usdt in fixed:
            ad = row["ad"]
            pay = float(amount_usdt) * float(ad.price_rub_per_usdt)
            if pay + 1e-9 < float(ad.min_rub):
                return [], 0.0
            if ad.max_rub != float("inf") and pay - 1e-9 > float(ad.max_rub):
                return [], 0.0
            if amount_usdt - 1e-9 > float(row["max_usdt"]):
                return [], 0.0
            if amount_usdt + 1e-9 < float(row["min_usdt"]):
                return [], 0.0
            result.append((ad, float(amount_usdt)))
            total_pay += pay

        if not result:
            return [], 0.0
        return result, float(total_pay)

    @classmethod
    def _try_close_remaining_with_rebalance(
        cls,
        *,
        prepared: list[dict[str, Any]],
        allocations: list[list[Any]],
        remaining: float,
    ) -> bool:
        if remaining <= 1e-9:
            return True
        used_ids = {id(item[0]) for item in allocations}
        for row in prepared:
            if id(row) in used_ids:
                continue
            min_usdt = float(row["min_usdt"])
            max_usdt = float(row["max_usdt"])
            if max_usdt + 1e-9 < min_usdt:
                continue
            if remaining + 1e-9 >= min_usdt and remaining <= max_usdt + 1e-9:
                allocations.append([row, float(remaining)])
                return True
            if remaining + 1e-9 >= min_usdt:
                continue
            need = min_usdt - remaining
            if need <= 1e-9:
                allocations.append([row, float(min_usdt)])
                return True
            reducible_rows = sorted(
                allocations,
                key=lambda item: item[0]["ad"].price_rub_per_usdt,
                reverse=True,
            )
            total_reducible = sum(max(0.0, float(item[1]) - float(item[0]["min_usdt"])) for item in reducible_rows)
            if total_reducible + 1e-9 < need:
                continue
            left = need
            for alloc in reducible_rows:
                if left <= 1e-9:
                    break
                reducible = max(0.0, float(alloc[1]) - float(alloc[0]["min_usdt"]))
                if reducible <= 1e-9:
                    continue
                cut = min(reducible, left)
                alloc[1] = float(alloc[1]) - float(cut)
                left -= cut
            if left > 1e-6:
                continue
            allocations.append([row, float(min_usdt)])
            return True
        return False

    @classmethod
    def _normalize_allocations_to_target(
        cls,
        *,
        allocations: list[list[Any]],
        target_usdt: float,
    ) -> list[tuple[dict[str, Any], float]]:
        cleaned: list[list[Any]] = []
        for row, amount_usdt in allocations:
            amount = float(amount_usdt)
            if amount <= 1e-9:
                continue
            cleaned.append([row, amount])
        if not cleaned:
            return []

        total = sum(float(item[1]) for item in cleaned)
        if total + 1e-9 < float(target_usdt):
            return []
        excess = total - float(target_usdt)
        if excess > 1e-9:
            # Prefer trimming the most expensive legs first.
            for item in sorted(cleaned, key=lambda a: a[0]["ad"].price_rub_per_usdt, reverse=True):
                if excess <= 1e-9:
                    break
                min_usdt = float(item[0]["min_usdt"])
                reducible = max(0.0, float(item[1]) - min_usdt)
                if reducible <= 1e-9:
                    continue
                cut = min(reducible, excess)
                item[1] = float(item[1]) - float(cut)
                excess -= cut
        if excess > 1e-6:
            return []

        final_total = sum(float(item[1]) for item in cleaned)
        if abs(final_total - float(target_usdt)) > 1e-4:
            return []
        return [(row, float(amount)) for row, amount in cleaned if float(amount) > 1e-9]
