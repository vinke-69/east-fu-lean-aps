from datetime import date

import pytest

from aps.import_dates import initial_urgent_flags, production_completion_date


@pytest.mark.parametrize("delivery, expected", [
    (date(2026, 9, 28), date(2026, 9, 23)),
    (date(2026, 9, 30), date(2026, 9, 25)),
    (date(2026, 9, 25), date(2026, 9, 22)),
    (date(2026, 9, 27), date(2026, 9, 23)),
    (date(2026, 3, 2), date(2026, 2, 25)),
])
def test_deadline_skips_weekends(delivery, expected):
    assert production_completion_date(delivery) == expected


def test_urgency_rounds_up_and_preserves_input_order():
    deliveries = [date(2026, 10, day) for day in range(11, 0, -1)]
    flags = initial_urgent_flags(deliveries)
    assert flags == [False] * 9 + [True, True]


def test_all_orders_on_cutoff_day_are_urgent():
    deliveries = [date(2026, 10, 1)] * 4 + [date(2026, 10, 2)] * 6
    assert initial_urgent_flags(deliveries) == [True] * 4 + [False] * 6


def test_single_date_batch_is_entirely_urgent():
    assert all(initial_urgent_flags([date(2026, 10, 1)] * 60))


def test_empty_batch_has_no_urgency_flags():
    assert initial_urgent_flags([]) == []
