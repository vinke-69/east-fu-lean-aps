from __future__ import annotations

from pathlib import Path
from typing import BinaryIO
import re
import unicodedata

import pandas as pd


REQUIRED_SHEETS = ["待排工單", "產品機台產速"]
DEFAULT_SCHEDULE_START = pd.Timestamp("2026-09-03 08:00")
DEFAULT_SCHEDULE_END = pd.Timestamp("2026-09-04 08:00")
CANONICAL_COLUMNS = {
    "工單編號": ["工單編號", "工單", "製令單號", "wo", "work_order", "work_order_no"],
    "產品": ["產品編號", "產品", "產品品號", "品號", "product", "product_code"],
    "數量": ["數量", "訂單數量", "預計產量", "quantity", "qty"],
    "單位": ["單位", "數量單位", "unit"],
    "交期": ["交期", "需求日期", "需求時間", "due date", "due_date"],
    "優先級": ["優先級", "優先順序", "工單急迫程度", "priority"],
    "允許機台": [
        "允許機台",
        "工單限定機台",
        "本單限用機台",
        "特殊指定機台",
        "限定機台",
        "限制機台",
        "可排機台",
        "可用機台",
        "指定機台",
        "指定可用機台",
        "只能使用機台",
        "允許生產機台",
        "allowed_machines",
        "eligible_machines",
    ],
    "換模群組": ["換模群組", "換線群組", "模具群組", "changeover_group", "setup_group"],
    "來源換模群組": ["來源換模群組", "前一換模群組", "from_group", "source_group"],
    "目標換模群組": ["目標換模群組", "下一換模群組", "to_group", "target_group"],
    "換模時間_分鐘": ["換模時間_分鐘", "換模時間", "換線時間", "setup_minutes", "changeover_minutes"],
    "機台": ["機台", "machine", "resource"],
    "可生產": ["可生產", "eligible", "can_produce"],
    "產速_PCS_per_hr": ["產速_pcs_per_hr", "pcs/hr", "pcs per hr", "rate", "rate_per_hour", "產速", "標準產速", "每小時產量"],
    "產速單位": ["產速單位", "rate_unit", "unit_per_hour"],
    "可用開始": ["可用開始", "可用起始時間", "available_from", "start", "開始時間"],
    "可用結束": ["可用結束", "可用結束時間", "available_until", "end", "結束時間"],
    "初始產品": ["初始產品", "目前產品", "initial_product", "initial setup", "initial_setup"],
}
SETTING_ITEM_ALIASES = {
    "排程起始時間": "排程開始",
    "排程開始時間": "排程開始",
    "排程開始": "排程開始",
    "排程結束時間": "排程結束",
    "排程結束": "排程結束",
    "時區": "時區",
    "timezone": "時區",
}


def load_workbook(source: str | Path | BinaryIO) -> dict[str, pd.DataFrame]:
    sheets = pd.read_excel(source, sheet_name=None, engine="openpyxl")
    return {name: sheets[name] for name in sheets}


def inspect_workbook_schema(source: str | Path | BinaryIO, sample_rows: int = 3) -> dict[str, dict[str, object]]:
    workbook = load_workbook(source)
    schema = {}
    for sheet_name, frame in workbook.items():
        schema[clean_label(sheet_name)] = {
            "row_count": int(len(frame)),
            "headers": [clean_label(col) for col in frame.columns],
            "dtypes": {clean_label(col): str(dtype) for col, dtype in frame.dtypes.items()},
            "sample_values": frame.head(sample_rows).where(pd.notna(frame), None).to_dict(orient="records"),
        }
    return schema


def clean_label(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\ufeff", "").replace("\u200b", "")
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    text = re.sub(r"\s*[*＊]+$", "", text)
    return text.strip()


def alias_key(value: object) -> str:
    return clean_label(value).lower().replace(" ", "_")


def detected_columns(frame: pd.DataFrame) -> str:
    return "、".join(clean_label(col) for col in frame.columns)


def normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    alias_to_column = {}
    for canonical, aliases in CANONICAL_COLUMNS.items():
        for alias in aliases + [canonical]:
            alias_to_column[alias_key(alias)] = canonical
    renamed = {}
    for col in frame.columns:
        cleaned = clean_label(col)
        renamed[col] = alias_to_column.get(alias_key(cleaned), cleaned)
    return frame.rename(columns=renamed)


def normalize_settings_frame(frame: pd.DataFrame) -> pd.DataFrame:
    settings = normalize_columns(frame.copy())
    if {"設定項目", "設定值"}.issubset(settings.columns):
        result = settings[["設定項目", "設定值"]].copy()
    else:
        header_row_index = None
        for idx, row in settings.iterrows():
            labels = [clean_label(value) for value in row.tolist()]
            if "設定項目" in labels:
                header_row_index = idx
                break
        if header_row_index is not None:
            raw_headers = [clean_label(value) for value in settings.loc[header_row_index].tolist()]
            body = settings.loc[header_row_index + 1 :].copy()
            body.columns = raw_headers
            body = normalize_columns(body)
            value_column = "設定值" if "設定值" in body.columns else "填寫內容" if "填寫內容" in body.columns else None
            result = body[["設定項目", value_column]].rename(columns={value_column: "設定值"}) if value_column else pd.DataFrame(columns=["設定項目", "設定值"])
        elif len(settings.columns) >= 2:
            result = settings.iloc[:, :2].copy()
            result.columns = ["設定項目", "設定值"]
        else:
            result = pd.DataFrame(columns=["設定項目", "設定值"])
    result = result.dropna(how="all").copy()
    result["設定項目"] = result["設定項目"].map(clean_label)
    result["設定項目"] = result["設定項目"].map(lambda value: SETTING_ITEM_ALIASES.get(alias_key(value), SETTING_ITEM_ALIASES.get(value, value)))
    return result[result["設定項目"] != ""].reset_index(drop=True)


def normalize_due_dates(series: pd.Series) -> pd.Series:
    if series.empty:
        return pd.to_datetime(series, errors="coerce")
    original = series.copy()
    try:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    except TypeError:
        parsed = pd.to_datetime(series, errors="coerce")

    def is_date_only(value: object) -> bool:
        if pd.isna(value):
            return False
        if isinstance(value, str):
            text = value.strip()
            return bool(text) and not re.search(r"\d{1,2}:\d{2}", text)
        timestamp = pd.Timestamp(value)
        return timestamp.hour == 0 and timestamp.minute == 0 and timestamp.second == 0 and timestamp.microsecond == 0

    date_only_mask = original.map(is_date_only) & parsed.notna()
    parsed.loc[date_only_mask] = parsed.loc[date_only_mask].dt.normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    return parsed


def default_settings_frame(start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> pd.DataFrame:
    start_value = pd.to_datetime(start, errors="coerce")
    if pd.isna(start_value):
        start_value = DEFAULT_SCHEDULE_START
    end_value = pd.to_datetime(end, errors="coerce")
    if pd.isna(end_value) or end_value <= start_value:
        end_value = start_value + pd.Timedelta(hours=24)
    return pd.DataFrame(
        [["排程開始", start_value], ["排程結束", end_value], ["時區", "Asia/Taipei"]],
        columns=["設定項目", "設定值"],
    )


def infer_settings_from_availability(availability: pd.DataFrame | None) -> pd.DataFrame:
    if availability is None or availability.empty or not {"可用開始", "可用結束"}.issubset(availability.columns):
        return default_settings_frame()
    start = availability["可用開始"].dropna().min()
    end = availability["可用結束"].dropna().max()
    return default_settings_frame(start, end)


def _upsert_setting(settings: pd.DataFrame, item: str, value: object) -> pd.DataFrame:
    result = settings.copy()
    if "設定項目" not in result.columns or "設定值" not in result.columns:
        result = pd.DataFrame(columns=["設定項目", "設定值"])
    mask = result["設定項目"] == item
    if mask.any():
        result.loc[mask, "設定值"] = value
    else:
        result = pd.concat([result, pd.DataFrame([[item, value]], columns=["設定項目", "設定值"])], ignore_index=True)
    return result


def normalize_workbook(workbook: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    normalized = {clean_label(name): normalize_columns(frame.copy()) for name, frame in workbook.items()}
    if "待排工單" in normalized:
        orders = normalized["待排工單"]
        if "交期" in orders.columns:
            orders["交期"] = normalize_due_dates(orders["交期"])
        if "數量" in orders.columns:
            orders["數量"] = pd.to_numeric(orders["數量"], errors="coerce")
        if "單位" not in orders.columns:
            orders["單位"] = "PCS"
        orders["_原始順序"] = range(1, len(orders) + 1)
        normalized["待排工單"] = orders
    if "產品機台產速" in normalized:
        rates = normalized["產品機台產速"]
        if "可生產" in rates.columns:
            yes_values = {"是", "yes", "y", "true", "1", "可", "可生產"}
            rates = rates[rates["可生產"].astype(str).map(lambda value: alias_key(value) in yes_values or clean_label(value) in yes_values)].copy()
        if "產速_PCS_per_hr" in rates.columns:
            rates["產速_PCS_per_hr"] = pd.to_numeric(rates["產速_PCS_per_hr"], errors="coerce")
        normalized["產品機台產速"] = rates
    if "換模時間" in normalized:
        changeovers = normalized["換模時間"].copy()
        if "換模時間_分鐘" in changeovers.columns:
            changeovers["換模時間_分鐘"] = pd.to_numeric(changeovers["換模時間_分鐘"], errors="coerce")
        normalized["換模時間"] = changeovers
    if "機台可用時間" in normalized:
        availability = normalized["機台可用時間"]
        if "可用開始" in availability.columns:
            availability["可用開始"] = pd.to_datetime(availability["可用開始"], errors="coerce")
        if "可用結束" in availability.columns:
            availability["可用結束"] = pd.to_datetime(availability["可用結束"], errors="coerce")
        normalized["機台可用時間"] = availability
    if "機台初始狀態" in normalized:
        initial = normalized["機台初始狀態"].copy()
        if "初始產品" not in initial.columns and "產品" in initial.columns:
            initial["初始產品"] = initial["產品"]
        normalized["機台初始狀態"] = initial
    inferred_settings = infer_settings_from_availability(normalized.get("機台可用時間"))
    if "排程基本設定" in workbook:
        settings = normalize_settings_frame(workbook["排程基本設定"])
        start, end, timezone = get_schedule_window(settings)
        inferred_start, inferred_end, _ = get_schedule_window(inferred_settings)
        if pd.isna(start):
            settings = _upsert_setting(settings, "排程開始", inferred_start)
        if pd.isna(end) or pd.notna(start) and end <= start:
            settings = _upsert_setting(settings, "排程結束", inferred_end)
        if not timezone or timezone == "nan":
            settings = _upsert_setting(settings, "時區", "Asia/Taipei")
        normalized["排程基本設定"] = settings
    else:
        normalized["排程基本設定"] = inferred_settings
    return normalized


def get_schedule_window(settings: pd.DataFrame, horizon_start: pd.Timestamp | None = None, horizon_end: pd.Timestamp | None = None) -> tuple[pd.Timestamp, pd.Timestamp, str]:
    settings = normalize_settings_frame(settings)
    values = dict(zip(settings["設定項目"], settings["設定值"]))
    start = pd.to_datetime(horizon_start if horizon_start is not None else values.get("排程開始"), errors="coerce")
    end = pd.to_datetime(horizon_end if horizon_end is not None else values.get("排程結束"), errors="coerce")
    timezone = str(values.get("時區", "Asia/Taipei"))
    return start, end, timezone
