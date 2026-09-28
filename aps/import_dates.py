"""Confirmed delivery/deadline and initial urgency rules for order imports."""
from __future__ import annotations

from datetime import date, timedelta
from math import ceil
from typing import Sequence


def production_completion_date(delivery: date) -> date:
    """Move back three weekdays, excluding Saturday and Sunday."""
    current = delivery
    remaining = 3
    while remaining:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def initial_urgent_flags(deliveries: Sequence[date]) -> list[bool]:
    """Earliest ceil(10% of orders), including every tie on the cutoff day.

    Call with the entire imported batch before separating incomplete orders.
    These are initial defaults; the UI must preserve subsequent user edits.
    """
    if not deliveries:
        return []
    cutoff = sorted(deliveries)[ceil(len(deliveries) / 10) - 1]
    return [delivery <= cutoff for delivery in deliveries]
