"""The delivery window an order books (spec/requirements.md "Shopping list", stage 3)."""

from datetime import date, time

import pytest
from bite_planner_agent.checkout import CheckoutRefused, delivery_day
from bite_planner_agent.windows import Window, choose, parse


def window(start: int, end: int) -> Window:
    return Window(start=time(start), end=time(end))


def test_the_latest_window_ending_by_the_time_is_booked() -> None:
    assert choose([window(9, 11), window(12, 14), window(13, 15)], time(15)) == window(13, 15)
    assert choose([window(12, 14), window(13, 15), window(14, 16)], time(15, 30)) == window(13, 15)


def test_a_full_window_gives_way_to_the_one_before_it_and_so_on_through_the_day() -> None:
    offered = [window(7, 9), window(11, 13), window(14, 16)]
    assert choose(offered, time(15)) == window(11, 13)
    assert choose([window(7, 9), window(16, 18)], time(15)) == window(7, 9)


def test_a_later_ending_window_requires_new_approval() -> None:
    assert choose([window(14, 16), window(15, 17)], time(15)) is None
    assert choose([window(14, 16), window(15, 17)], time(15, 30)) is None


def test_with_neither_left_nothing_is_booked() -> None:
    # The user picks another day or time.
    assert choose([window(17, 19), window(18, 20)], time(15)) is None
    assert choose([], time(15)) is None


def test_relative_delivery_days_resolve_across_the_year_boundary() -> None:
    assert delivery_day("Today", date(2026, 12, 31)) == date(2026, 12, 31)
    assert delivery_day("Tomorrow, Jan 1", date(2026, 12, 31)) == date(2027, 1, 1)
    assert delivery_day("Jan 1", date(2026, 12, 31)) == date(2027, 1, 1)
    with pytest.raises(CheckoutRefused, match="disagree"):
        delivery_day("Tomorrow, Jan 2", date(2026, 12, 31))


@pytest.mark.parametrize("text", ["11pm–midnight", "25–2pm", "10:99am–Noon"])
def test_invalid_or_next_day_windows_cannot_be_authorized_as_same_day(text: str) -> None:
    assert parse(text) is None
