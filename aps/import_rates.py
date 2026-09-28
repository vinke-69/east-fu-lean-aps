"""Resolve rates only after product/specification matching has been confirmed.

Backup eligibility is deliberately separate from a measured machine rate.
This module does not infer product matches or copy a primary machine's speed.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable


@dataclass(frozen=True)
class ActualRate:
    product_key: str
    machine: str
    metres_per_minute: float
    source: str


@dataclass(frozen=True)
class RateResolution:
    machine: str
    metres_per_minute: float | None
    reason: str
    candidates: tuple[ActualRate, ...]


def resolve_actual_rate(
    product_key: str, machine: str, records: Iterable[ActualRate]
) -> RateResolution:
    """Require an explicit measurement for this confirmed product and machine.

    Conflicting measurements require review; neither newest nor fastest wins
    implicitly. Source references are retained for the user's review.
    """
    candidates = tuple(
        row for row in records
        if row.product_key == product_key and row.machine == machine
    )
    if not candidates:
        return RateResolution(machine, None, "缺少該產品在該機台的實際產速", candidates)
    try:
        speeds = {float(row.metres_per_minute) for row in candidates}
    except (TypeError, ValueError):
        return RateResolution(machine, None, "實際產速不是有效數值", candidates)
    if any(not isfinite(speed) or speed <= 0 for speed in speeds):
        return RateResolution(machine, None, "實際產速必須為大於零的有限數值", candidates)
    if len(speeds) != 1:
        return RateResolution(machine, None, "同產品同機台有多筆不同實際產速，需確認", candidates)
    return RateResolution(machine, speeds.pop(), "", candidates)
