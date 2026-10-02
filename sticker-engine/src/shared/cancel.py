"""A Stop button for long runs: set once, checked between units of work, never mid-request."""

import threading

_event = threading.Event()


class Cancelled(Exception):
    """The user pressed Stop. Work already paid for is saved and the run can be resumed."""


def request() -> None:
    _event.set()


def reset() -> None:
    _event.clear()


def is_set() -> bool:
    return _event.is_set()


def check() -> None:
    if _event.is_set():
        raise Cancelled("Stopped by the user")
