from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import threading
from typing import Callable


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DedupScanState:
    is_running: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_error: str | None = None
    last_completed_candidates: int | None = None

    def to_payload(self) -> dict:
        return {
            "is_running": bool(self.is_running),
            "started_at": self.started_at.isoformat() if self.started_at is not None else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at is not None else None,
            "last_error": self.last_error,
            "last_completed_candidates": self.last_completed_candidates,
        }


class DedupScanRuntime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = DedupScanState()

    def get_state(self) -> dict:
        with self._lock:
            return self._state.to_payload()

    def try_start(self, *, task: Callable[[], int]) -> bool:
        with self._lock:
            if self._state.is_running:
                return False
            self._state.is_running = True
            self._state.started_at = _utcnow()
            self._state.finished_at = None
            self._state.last_error = None

        thread = threading.Thread(target=self._run, args=(task,), daemon=True, name="dedup-scan-runtime")
        thread.start()
        return True

    def _run(self, task: Callable[[], int]) -> None:
        candidate_count: int | None = None
        error_message: str | None = None
        try:
            candidate_count = int(task())
        except Exception as exc:  # pragma: no cover
            error_message = f"{exc.__class__.__name__}: {exc}"
        finally:
            with self._lock:
                self._state.is_running = False
                self._state.finished_at = _utcnow()
                self._state.last_error = error_message
                if candidate_count is not None:
                    self._state.last_completed_candidates = candidate_count


dedup_scan_runtime = DedupScanRuntime()
