"""Instacart's delivery windows, and the one an order books (spec/requirements.md
"Shopping list", stage 3). Wegmans' are two hours long, starting every hour from
7-9 AM to 9-11 PM (measured 2026-09-28), all in the store's time zone."""

import re
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


# A time as Instacart writes it: "7am", "10:30 pm", "Noon"; the first of a range
# may leave out am or pm ("2–4pm").
_TIME = r"(?:(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?\s*m\b\.?)?|(noon|midnight))"
_RANGE = re.compile(rf"{_TIME}\s*(?:–|—|-|to)\s*{_TIME}", re.I)


def _clock(hour: str, minute: str, half: str | None, word: str) -> time | None:
    """A time from one match of _TIME; None when it has no am or pm to go by."""
    if word:
        return time(12) if word.lower() == "noon" else time(0)
    if not half:
        return None
    hours = int(hour) % 12 + (12 if half.lower() == "p" else 0)
    return time(hours, int(minute or 0))


def parse(text: str) -> Window | None:
    """The first delivery window written in the text ("7am–9am $0", "10am–Noon",
    "Noon–2pm", "2–4pm"), or None."""
    for match in _RANGE.finditer(text):
        end = _clock(*(group or "" for group in match.groups()[4:]))
        if end is None:
            continue
        start_hour, start_minute, start_half, start_word = (g or "" for g in match.groups()[:4])
        start = _clock(start_hour, start_minute, start_half, start_word)
        if start is None and start_hour:
            # "2–4pm": the same half of the day as the end, unless that puts it after it.
            hours = int(start_hour) % 12 + (12 if end.hour >= 12 else 0)
            start = time(
                hours if hours < end.hour or end.hour == 0 else hours - 12, int(start_minute or 0)
            )
        if start is not None and (start < end or end == time(0)):
            return Window(start=start, end=end)
    return None


def label(window: Window) -> str:
    """ "2–4 PM", "11 AM–1 PM", "10:30–11:30 AM"."""

    def clock(at: time) -> str:
        hour = at.hour % 12 or 12
        return f"{hour}:{at.minute:02d}" if at.minute else str(hour)

    def half(at: time) -> str:
        return "AM" if at.hour < 12 else "PM"

    if half(window.start) == half(window.end):
        return f"{clock(window.start)}–{clock(window.end)} {half(window.end)}"
    return f"{clock(window.start)} {half(window.start)}–{clock(window.end)} {half(window.end)}"


def time_label(at: time) -> str:
    """ "6:00 PM"."""
    return f"{at.hour % 12 or 12}:{at.minute:02d} {'AM' if at.hour < 12 else 'PM'}"
