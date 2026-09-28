from datetime import date

import pandas as pd
import pytest

from aps.import_orders import basic_order_issues, batch_must_stop, closing_date_from_filename, combine_order_batches


def test_filename_uses_closing_date_not_other_dates():
    assert closing_date_from_filename("BK_結關2026.09.23(08.06收到未建).xlsx") == date(2026, 9, 23)


@pytest.mark.parametrize("filename", ["orders.xlsx", "結關2026.02.30.xlsx", "結關2026.01.01_結關2026.02.01.xlsx"])
def test_unclear_date_requires_correction(filename):
    with pytest.raises(ValueError):
        closing_date_from_filename(filename)


@pytest.mark.parametrize("total, problems, stop", [(10, 3, True), (10, 2, False), (100, 29, False), (0, 0, True), (3, 1, True)])
def test_threshold_counts_problem_orders_once(total, problems, stop):
    assert batch_must_stop(total, problems) is stop


def test_duplicate_order_versions_are_both_reported():
    rows = pd.DataFrame([dict(製令單號="WO1", 產品品號="P1", 品名="透明管", 規格="100 FT", 預計產量="2", 單位="PCS")] * 2)
    issues = basic_order_issues(rows)
    assert set(issues) == {0, 1}
    assert all("重複" in messages[0] for messages in issues.values())


def test_urgency_is_computed_across_uploaded_files():
    first = pd.DataFrame({"結關日": [pd.Timestamp("2026-10-02")] * 9})
    second = pd.DataFrame({"結關日": [pd.Timestamp("2026-10-01")]})
    result = combine_order_batches([first, second])
    assert result["優先級"].tolist() == ["一般"] * 9 + ["急單"]
