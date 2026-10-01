"""Instacart's delivery windows, and the one an order books (spec/requirements.md
"Shopping list", stage 3). Wegmans' are two hours long, starting every hour from
7-9 AM to 9-11 PM (measured 2026-09-28), all in the store's time zone."""

from datetime import time

from pydantic import BaseModel, ConfigDict

FIRST_START_HOUR = 7
LAST_START_HOUR = 21
WINDOW_HOURS = 2


class Window(BaseModel):
    """A delivery window: from `start` to `end`, the same day."""

    model_config = ConfigDict(frozen=True)

    start: time
    end: time


# Every window Instacart has in a day (it offers those not full yet).
DAY = [
    Window(start=time(hour), end=time(hour + WINDOW_HOURS))
    for hour in range(FIRST_START_HOUR, LAST_START_HOUR + 1)
]


def choose(offered: list[Window], by: time) -> Window | None:
    """The window to book for "by 3:00 PM" among those offered: the latest one
    ending by then (a full one isn't offered, so it's the one before it, and so
    on through the day); if none is left, the earliest-ending one the time falls
    in (2-4 PM), the one window allowed to end after it; else none."""
    ending_by = [window for window in offered if window.end <= by]
    if ending_by:
        return max(ending_by, key=lambda window: window.end)
    around = [window for window in offered if window.start < by < window.end]
    return min(around, key=lambda window: window.end) if around else None


def fallback(by: time) -> Window | None:
    """The one window that may end after the chosen time, if nothing ending by
    it is left ("by 3:00 PM, or 2-4 PM"); None when there's no such window."""
    return choose([window for window in DAY if window.end > by], by)
