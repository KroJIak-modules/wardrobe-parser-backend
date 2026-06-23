import pytest

from app.services.settings import weight_recalc_queue as queue_module
from app.services.settings.weight_recalc_queue import WeightRuleRecalcQueue


class _FakeRedis:
    def __init__(self) -> None:
        self._items: dict[str, float] = {}

    def zadd(self, _key: str, values: dict[str, float]) -> int:
        added = 0
        for member, score in values.items():
            if member not in self._items:
                added += 1
            self._items[member] = float(score)
        return added

    def zrangebyscore(self, _key: str, min: float, max: float, start: int, num: int):
        items = sorted(
            (member for member, score in self._items.items() if float(min) <= score <= float(max)),
            key=lambda member: (self._items[member], member),
        )
        return items[start:start + num]

    def zrem(self, _key: str, *members: str) -> int:
        removed = 0
        for member in members:
            if member in self._items:
                removed += 1
                self._items.pop(member, None)
        return removed

    def zcard(self, _key: str) -> int:
        return len(self._items)


def test_weight_recalc_queue_deduplicates_and_pops_ready_batch() -> None:
    queue = WeightRuleRecalcQueue(client=_FakeRedis())

    assert queue.enqueue_product_ids([7, 3, 7, 0, -2, 5]) == 3
    assert queue.size() == 3
    assert queue.pop_ready_batch(limit=2, debounce_sec=0) == [3, 5]
    assert queue.size() == 1
    assert queue.pop_ready_batch(limit=10, debounce_sec=0) == [7]
    assert queue.size() == 0


def test_weight_recalc_queue_waits_for_debounce_window() -> None:
    queue = WeightRuleRecalcQueue(client=_FakeRedis())

    assert queue.enqueue_product_ids([11, 12]) == 2
    assert queue.pop_ready_batch(limit=10, debounce_sec=60) == []
    assert queue.size() == 2


def test_weight_recalc_queue_reenqueue_moves_product_to_latest_window(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = WeightRuleRecalcQueue(client=_FakeRedis())
    now = 1_000.0

    monkeypatch.setattr(queue_module.time, "time", lambda: now)
    assert queue.enqueue_product_ids([21]) == 1

    now = 1_002.0
    assert queue.enqueue_product_ids([21]) == 0

    now = 1_004.0
    assert queue.pop_ready_batch(limit=10, debounce_sec=3) == []

    now = 1_006.0
    assert queue.pop_ready_batch(limit=10, debounce_sec=3) == [21]
