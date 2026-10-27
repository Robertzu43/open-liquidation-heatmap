"""Forward pagination for public REST lists that are ordered by time."""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")


def paginate_forward(
    fetch: Callable[[int, int], list[T]],
    start_ms: int,
    end_ms: int,
    *,
    page_limit: int,
    time_of: Callable[[T], int],
    max_pages: int = 40,
) -> list[T]:
    """Pull pages until the window is covered or a short page arrives.

    ``fetch(cursor_ms, end_ms)`` must return rows at or after ``cursor_ms``.
    The next cursor is one millisecond after the last row's timestamp.
    """
    cursor = start_ms
    rows: list[T] = []
    for _ in range(max_pages):
        if cursor > end_ms:
            break
        batch = fetch(cursor, end_ms)
        if not batch:
            break
        rows.extend(batch)
        last = time_of(batch[-1])
        if len(batch) < page_limit or last < cursor:
            break
        nxt = last + 1
        if nxt <= cursor:
            break
        cursor = nxt
    return rows
