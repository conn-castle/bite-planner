"""The delivery window an order books (spec/requirements.md "Shopping list", stage 3)."""

from datetime import time

from castle_chef_agent.windows import DAY, Window, choose, fallback


def window(start: int, end: int) -> Window:
    return Window(start=time(start), end=time(end))


def test_a_day_has_two_hour_windows_starting_every_hour_from_7_am_to_9_pm() -> None:
    assert (DAY[0], DAY[-1], len(DAY)) == (window(7, 9), window(21, 23), 15)


def test_the_latest_window_ending_by_the_time_is_booked() -> None:
    assert choose(DAY, time(15)) == window(13, 15)
    assert choose(DAY, time(15, 30)) == window(13, 15)


def test_a_full_window_gives_way_to_the_one_before_it_and_so_on_through_the_day() -> None:
    offered = [w for w in DAY if w.end not in (time(15), time(14))]
    assert choose(offered, time(15)) == window(11, 13)
    assert choose([window(7, 9), window(16, 18)], time(15)) == window(7, 9)


def test_with_nothing_ending_by_the_time_left_the_window_it_falls_in_is_booked() -> None:
    later = [w for w in DAY if w.end > time(15)]
    assert choose(later, time(15)) == window(14, 16)
    # Of the two a half hour falls in, the one ending first.
    assert choose([w for w in DAY if w.end > time(15, 30)], time(15, 30)) == window(14, 16)


def test_with_neither_left_nothing_is_booked() -> None:
    # The user picks another day or time.
    assert choose([window(17, 19), window(18, 20)], time(15)) is None
    assert choose([], time(15)) is None


def test_the_review_names_the_one_window_that_may_end_after_the_time() -> None:
    assert fallback(time(15)) == window(14, 16)
    assert fallback(time(18)) == window(17, 19)
    assert fallback(time(15, 30)) == window(14, 16)
    # Before any window, there's none.
    assert fallback(time(7)) is None
