"""Journal-style stderr logging."""

import sys
from enum import IntEnum


class Priority(IntEnum):
    INFO = 6
    WARNING = 4
    ERROR = 3


def _emit(priority: Priority, message: str) -> None:
    print(f"<{priority.value}>immich-to-gphotos: {message}", file=sys.stderr)


def info(message: str) -> None:
    _emit(Priority.INFO, message)


def warning(message: str) -> None:
    _emit(Priority.WARNING, message)


def error(message: str) -> None:
    _emit(Priority.ERROR, message)
