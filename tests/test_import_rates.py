import pytest

from aps.import_rates import ActualRate, resolve_actual_rate


def test_backup_does_not_inherit_primary_speed():
    rows = [ActualRate("confirmed-product", "C2", 20, "capacity:5")]
    result = resolve_actual_rate("confirmed-product", "C5", rows)
    assert result.metres_per_minute is None
    assert result.reason == "缺少該產品在該機台的實際產速"


def test_backup_uses_its_own_product_measurement():
    rows = [ActualRate("p", "C2", 20, "capacity:5"),
            ActualRate("p", "C5", 16, "capacity:8"),
            ActualRate("other", "C5", 30, "capacity:9")]
    result = resolve_actual_rate("p", "C5", rows)
    assert result.metres_per_minute == 16
    assert [r.source for r in result.candidates] == ["capacity:8"]


def test_conflicting_measurements_require_review():
    rows = [ActualRate("p", "C5", 16, "capacity:8"),
            ActualRate("p", "C5", 18, "capacity:9")]
    result = resolve_actual_rate("p", "C5", rows)
    assert result.metres_per_minute is None
    assert len(result.candidates) == 2
    assert "需確認" in result.reason


@pytest.mark.parametrize("speed", [0, -1, float("nan"), float("inf"), "invalid", None])
def test_invalid_measurement_cannot_be_scheduled(speed):
    result = resolve_actual_rate("p", "C2", [ActualRate("p", "C2", speed, "capacity:5")])
    assert result.metres_per_minute is None
    assert result.reason


def test_identical_measurements_preserve_all_sources():
    rows = [ActualRate("p", "C2", 20, "capacity:5"),
            ActualRate("p", "C2", 20, "capacity:6")]
    result = resolve_actual_rate("p", "C2", rows)
    assert result.metres_per_minute == 20
    assert len(result.candidates) == 2
