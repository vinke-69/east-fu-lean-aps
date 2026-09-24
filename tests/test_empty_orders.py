import pytest
from pathlib import Path
from streamlit.testing.v1 import AppTest

from aps.sample_data import demo_workbook
from aps.scheduler import schedule
from aps.strategies import STRATEGIES
from aps.validator import EMPTY_ORDERS_MESSAGE, validate_workbook


def empty_workbook():
    workbook = demo_workbook()
    workbook["待排工單"] = workbook["待排工單"].iloc[:0].copy()
    return workbook


def test_empty_template_is_rejected_with_actionable_message():
    ok, issues, data = validate_workbook(empty_workbook())
    assert not ok
    assert issues == [EMPTY_ORDERS_MESSAGE]
    assert data is None


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_every_strategy_rejects_empty_orders_before_calculation(strategy):
    with pytest.raises(ValueError, match="待排工單目前沒有資料"):
        schedule(empty_workbook(), strategy)


def test_empty_template_disables_schedule_and_valid_data_recovers():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=20)
    workbook = empty_workbook()
    app.session_state["workbook"] = workbook
    app.session_state["validation"] = validate_workbook(workbook)
    app.run()
    assert not app.exception
    assert EMPTY_ORDERS_MESSAGE in [item.value for item in app.warning]
    assert next(button for button in app.button if button.label == "確認設定並開始排程").disabled
    assert app.session_state["schedule_df"] is None
    assert app.session_state["last_version"] is None

    workbook = demo_workbook()
    app.session_state["workbook"] = workbook
    app.session_state["validation"] = validate_workbook(workbook)
    app.run()
    assert not app.exception
    assert not next(button for button in app.button if button.label == "確認設定並開始排程").disabled
    assert not app.warning
