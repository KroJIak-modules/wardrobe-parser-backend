from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import event
from sqlalchemy.orm import Session


_AFTER_COMMIT_CALLBACKS_KEY = "after_commit_callbacks"


def register_after_commit(session: Session, callback: Callable[[], None]) -> None:
    callbacks = session.info.setdefault(_AFTER_COMMIT_CALLBACKS_KEY, [])
    callbacks.append(callback)


@event.listens_for(Session, "after_commit")
def _run_after_commit_callbacks(session: Session) -> None:
    callbacks = list(session.info.pop(_AFTER_COMMIT_CALLBACKS_KEY, []) or [])
    for callback in callbacks:
        callback()


@event.listens_for(Session, "after_rollback")
def _clear_after_commit_callbacks(session: Session) -> None:
    session.info.pop(_AFTER_COMMIT_CALLBACKS_KEY, None)
