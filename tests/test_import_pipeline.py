from copy import deepcopy

import pandas as pd
import pytest

from aps.import_catalog import matching_dimensions, parse_spec, rounded, mapping_key, read_capacity, read_molds
from aps.import_pipeline import prepare_import, export_mappings, load_mappings
from aps.metrics import calculate_kpis
from aps.scheduler import schedule
from aps.validator import validate_workbook


def inputs(count=10):
    orders = pd.DataFrame([dict(製令單號=f"WO{i}", 產品品號="P", 品名="透明管", 規格='ID 1/8" x OD 1/4" x 100 FT',
                               預計產量="1", 單位="PCS", 優先級="一般", 結關日=pd.Timestamp("2026-10-09"),
                               生產完成日=pd.Timestamp("2026-10-06"), 來源檔案="orders.xlsx", 來源列=i + 4) for i in range(count)])
    rates = [dict(ref="R2", name="透明管", spec="100FT", inner=3.175, outer=6.35, thickness=1.5875, speed=20., machines=["C2"], backup=["C5"], reason=""),
             dict(ref="R5", name="透明管", spec="100FT", inner=3.175, outer=6.35, thickness=1.5875, speed=10., machines=["C5"], backup=[], reason="")]
    molds = [dict(ref="M2", name="透明管", inner=3.18, thickness=1.59, machines=["C2", "C5"])]
    mappings = {mapping_key(orders.iloc[0]): {"machines": {"C2": {"rate": "R2", "mold": "M2"}, "C5": {"rate": "R5", "mold": "M2"}}}}
    return orders, rates, molds, mappings


def test_imperial_spec_and_half_up_rounding():
    spec = parse_spec('1/8"ID x 1/4" OD X 100 FT')
    assert spec["length_m"] == 30.48
    assert str(rounded(spec["inner"])) == "3.18"
    assert str(rounded(spec["thickness"])) == "1.59"
    assert matching_dimensions(spec, dict(inner=3.18, thickness=1.59), mold=True)
    assert parse_spec('ID 1-1/4" x OD 1-3/4" x 50\'')["inner"] == 31.75


def test_partial_batch_preserves_unscheduled_rows_in_all_results():
    orders, rates, molds, mappings = inputs()
    orders.loc[0, "產品品號"] = "UNKNOWN"
    workbook, issues, blocked = prepare_import(orders, rates, molds, mappings)
    assert not blocked and len(issues) == 1
    ok, messages, normalized = validate_workbook(workbook)
    assert ok, messages
    result = schedule(normalized, "spt", horizon_start=pd.Timestamp("2026-10-01 08:00"), horizon_end=pd.Timestamp("2026-10-02 08:00"))
    assert len(result) == 10
    assert result["工單編號"].nunique() == 10
    assert len(result[result["狀態"] == "scheduled"]) == 9
    assert result.loc[result["工單編號"] == "WO0", "備註"].str.contains("尚未確認").all()
    assert calculate_kpis(result, normalized["排程基本設定"])["未完成 / 超出 horizon 工單數"] == 1


def test_exactly_thirty_percent_blocks_validation():
    orders, rates, molds, mappings = inputs()
    orders.loc[:2, "產品品號"] = "UNKNOWN"
    workbook, issues, blocked = prepare_import(orders, rates, molds, mappings)
    assert blocked and len(issues) == 3
    assert not validate_workbook(workbook)[0]
    with pytest.raises(ValueError, match="30%"):
        schedule(workbook, "edd")


def test_backup_rate_is_specific_and_order_is_never_split():
    orders, rates, molds, mappings = inputs(1)
    workbook, _, _ = prepare_import(orders, rates, molds, mappings)
    speed = workbook["產品機台產速"].set_index("機台")["產速_PCS_per_hr"]
    assert speed["C2"] == pytest.approx(20 * 60 / 30.48)
    assert speed["C5"] == pytest.approx(10 * 60 / 30.48)
    ok, _, data = validate_workbook(workbook)
    assert ok
    assert len(schedule(data, "edd")) == 1


def test_different_lengths_share_changeover_group():
    orders, rates, molds, mappings = inputs(2)
    orders.loc[1, "規格"] = 'ID 1/8" x OD 1/4" x 250 FT'
    mappings[mapping_key(orders.iloc[1])] = deepcopy(next(iter(mappings.values())))
    workbook, _, _ = prepare_import(orders, rates, molds, mappings)
    assert workbook["產品機台產速"]["換模群組"].nunique() == 1
    assert workbook["產品機台產速"]["每件長度_M"].nunique() == 2


def test_no_machine_has_exact_note_and_counts_in_threshold():
    orders, rates, molds, mappings = inputs(1)
    mappings[mapping_key(orders.iloc[0])] = {"no_machine": True, "machines": {}}
    _, issues, blocked = prepare_import(orders, rates, molds, mappings)
    assert issues.iloc[0]["缺漏原因"] == "無符合機台"
    assert blocked


def test_saved_mapping_cannot_reuse_other_database():
    _, _, _, mappings = inputs()
    blob = export_mappings("db1", mappings)
    assert load_mappings(blob, "db1") == mappings
    with pytest.raises(ValueError, match="資料庫不符"):
        load_mappings(blob, "db2")


def test_forged_mapping_cannot_use_backup_as_measured_rate():
    orders, rates, molds, mappings = inputs(1)
    mappings[mapping_key(orders.iloc[0])]["machines"] = {"C5": {"rate": "R2", "mold": "M2"}}
    workbook, issues, blocked = prepare_import(orders, rates, molds, mappings)
    assert workbook["待排工單"].empty
    assert blocked and "產速或模具條件不符" in issues.iloc[0]["缺漏原因"]


def test_database_reader_uses_actual_not_haul_speed(monkeypatch):
    frame = pd.DataFrame([["規格", "品名", "外徑", "內徑", "厚度", "拖台產速", "實際產速", "生產", "備用"],
                          ["規格", "品名", "mm", "mm", "mm", "M/分", "M/分", "機台", "機台"],
                          ["spec", "透明管", 6.35, 3.175, 1.59, 99, 20, "C2", "C5,6"]])
    monkeypatch.setattr("aps.import_catalog.raw_sheets", lambda *args: {"產能": frame})
    records, _ = read_capacity(b"", "test.xls")
    assert records[0]["speed"] == 20
    assert records[0]["machines"] == ["C2"]
    assert records[0]["backup"] == ["C5", "C6"]


def test_mold_row_machine_overrides_sheet_name(monkeypatch):
    frame = pd.DataFrame([["生產機台", "品名", "成品規格", "", ""],
                          ["", "", "", "", ""], ["", "", "", "", ""],
                          ["C6", "透明管", 3.18, "x", .79], ["C2,5", "透明管", 3.18, "x", .79]])
    monkeypatch.setattr("aps.import_catalog.raw_sheets", lambda *args: {"C2,5透明不包紗": frame})
    records, _ = read_molds(b"", "test.xls")
    assert records[0]["machines"] == ["C6"]
    assert records[1]["machines"] == ["C2", "C5"]


def test_invalid_fraction_is_reportable_not_an_exception():
    assert parse_spec('ID 1/0" x OD 1/4" x 100 FT') == {}
