"""Instacart's delivery windows, and the one an order books (spec/requirements.md
"Shopping list", stage 3). Wegmans' are two hours long, starting every hour from
7-9 AM to 9-11 PM (measured 2026-09-28), all in the store's time zone."""

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Self
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

AVAILABILITY_MAX_AGE = timedelta(minutes=30)


class Window(BaseModel):
    """A delivery window: from `start` to `end`, the same day."""

    model_config = ConfigDict(frozen=True)

    start: time
    end: time

    @model_validator(mode="after")
    def same_day(self) -> Self:
        if self.start.tzinfo is not None or self.end.tzinfo is not None or self.start >= self.end:
            raise ValueError("A delivery window must start and end on the same local calendar day.")
        return self


class AvailableDay(BaseModel):
    """Scheduled windows read from one of the store's delivery-day tabs."""

    day: date
    windows: list[Window]


class Availability(BaseModel):
    """A dated snapshot of the store's actual offered delivery windows."""

    observed_at: AwareDatetime
    time_zone: str
    days: list[AvailableDay]

    def remaining(self, now: datetime) -> list[AvailableDay]:
        """Windows whose start has not passed, in the store's time zone."""
        from zoneinfo import ZoneInfo

        local = now.astimezone(ZoneInfo(self.time_zone))
        return [
            AvailableDay(
                day=day.day,
                windows=[
                    window
                    for window in day.windows
                    if datetime.combine(day.day, window.start, local.tzinfo) > local
                ],
            )
            for day in self.days
        ]


class DeliveryProposal(BaseModel):
    """A live alternative, which is never permission to purchase it."""

    id: UUID = Field(default_factory=uuid4)
    day: date
    window: Window
    observed_at: AwareDatetime
    starts_at: AwareDatetime
    valid_until: AwareDatetime


def choose(offered: list[Window], by: time) -> Window | None:
    """The latest offered window ending no later than the approved deadline."""
    ending_by = [window for window in offered if window.end <= by]
    if ending_by:
        return max(ending_by, key=lambda window: window.end)
    return None


def propose(snapshot: Availability, day: date, by: time, now: datetime) -> DeliveryProposal | None:
    """The earliest recorded alternative; never infer windows on unobserved days."""
    alternatives = sorted(
        (offered.day, window.start, window.end)
        for offered in snapshot.remaining(now)
        for window in offered.windows
        if offered.day > day or (offered.day == day and window.end > by)
    )
    if not alternatives:
        return None
    proposed_day, start, end = alternatives[0]
    return DeliveryProposal(
        day=proposed_day,
        window=Window(start=start, end=end),
        observed_at=snapshot.observed_at,
        starts_at=datetime.combine(proposed_day, start, ZoneInfo(snapshot.time_zone)).astimezone(
            UTC
        ),
        valid_until=snapshot.observed_at + AVAILABILITY_MAX_AGE,
    )


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
    if not 1 <= int(hour) <= 12 or not 0 <= int(minute or 0) <= 59:
        return None
    hours = int(hour) % 12 + (12 if half.lower() == "p" else 0)
    return time(hours, int(minute or 0))


_DEADLINE = re.compile(rf"\bby\s+{_TIME}", re.I)


def deadline(text: str) -> time | None:
    """The first delivery deadline written in the text ("Today by 10pm"), or None."""
    for match in _DEADLINE.finditer(text):
        found = _clock(*(group or "" for group in match.groups()))
        if found is not None:
            return found
    return None


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
            if not 1 <= int(start_hour) <= 12 or not 0 <= int(start_minute or 0) <= 59:
                continue
            # "2–4pm": the same half of the day as the end, unless that puts it after it.
            hours = int(start_hour) % 12 + (12 if end.hour >= 12 else 0)
            start = time(
                hours if hours < end.hour or end.hour == 0 else hours - 12, int(start_minute or 0)
            )
        if start is not None and start < end:
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
