from __future__ import annotations

import pandas as pd

from .parser import REQUIRED_SHEETS, clean_label, detected_columns, get_schedule_window, normalize_workbook


VALID_PRIORITIES = {"急單", "一般", "低優先"}
EMPTY_ORDERS_MESSAGE = "待排工單目前沒有資料，請至少填寫一筆完整工單後重新上傳，或載入東福標準排程資料。"
PRIORITY_ALIASES = {
    "urgent": "急單",
    "rush": "急單",
    "high": "急單",
    "急": "急單",
    "急單": "急單",
    "normal": "一般",
    "medium": "一般",
    "一般": "一般",
    "low": "低優先",
    "低": "低優先",
    "低優先": "低優先",
}


def normalize_priority(value: object) -> str:
    key = str(value).strip().lower()
    return PRIORITY_ALIASES.get(key, str(value).strip())


def validate_workbook(workbook: dict[str, pd.DataFrame]) -> tuple[bool, list[str], dict[str, pd.DataFrame] | None]:
    issues: list[str] = []
    cleaned_sheet_names = {clean_label(name) for name in workbook}
    missing = [sheet for sheet in REQUIRED_SHEETS if sheet not in cleaned_sheet_names]
    for sheet in missing:
        issues.append(f"缺少必要工作表：{sheet}")
    if missing:
        return False, issues, None

    data = normalize_workbook(workbook)
    orders = data["待排工單"]
    rates = data["產品機台產速"]
    settings = data["排程基本設定"]

    if orders.empty:
        return False, [EMPTY_ORDERS_MESSAGE], None

    required_order_cols = ["工單編號", "產品", "數量", "單位", "優先級", "交期"]
    required_rate_cols = ["產品", "機台", "產速_PCS_per_hr"]
    for col in required_order_cols:
        if col not in orders.columns:
            issues.append(f"Excel 訂單資料缺少必要欄位：{col}。目前偵測到欄位：{detected_columns(orders)}")
    for col in required_rate_cols:
        if col not in rates.columns:
            issues.append(f"產品機台產速缺少必要欄位：{col}。目前偵測到欄位：{detected_columns(rates)}")
    if issues:
        return False, issues, None

    orders["優先級"] = orders["優先級"].apply(normalize_priority)

    if orders["工單編號"].isna().any() or (orders["工單編號"].astype(str).str.strip() == "").any():
        issues.append("工單編號不可空白")
    duplicate_ids = orders["工單編號"][orders["工單編號"].duplicated()].dropna().astype(str).unique()
    for work_order_id in duplicate_ids:
        issues.append(f"工單編號不可重複：{work_order_id}")
    if orders["產品"].isna().any() or (orders["產品"].astype(str).str.strip() == "").any():
        issues.append("產品不可空白")
    bad_quantity = orders[orders["數量"].isna() | (orders["數量"] <= 0)]
    for _, row in bad_quantity.iterrows():
        issues.append(f"數量必須大於 0：{row.get('工單編號', '未知工單')}")
    bad_due = orders[orders["交期"].isna()]
    for _, row in bad_due.iterrows():
        issues.append(f"交期無效：{row.get('工單編號', '未知工單')}")
    bad_priority = orders[~orders["優先級"].isin(VALID_PRIORITIES)]
    for _, row in bad_priority.iterrows():
        issues.append(f"優先級無效：{row.get('工單編號', '未知工單')}，必須為 急單 / 一般 / 低優先")

    bad_rates = rates[rates["產速_PCS_per_hr"].isna() | (rates["產速_PCS_per_hr"] <= 0)]
    for _, row in bad_rates.iterrows():
        issues.append(f"產速必須大於 0：{row.get('產品', '未知產品')} / {row.get('機台', '未知機台')}")
    products_with_rates = set(rates["產品"].dropna().astype(str))
    if "產速單位" in rates.columns:
        bad_units = rates[~rates["產速單位"].fillna("PCS/hr").astype(str).str.lower().isin(["pcs/hr", "pcs per hr", "pcs_per_hr"])]
        for _, row in bad_units.iterrows():
            issues.append(f"產速單位目前僅支援 PCS/hr：{row.get('產品', '未知產品')} / {row.get('機台', '未知機台')}")
    if "換模時間" in data:
        changeovers = data["換模時間"]
        required_changeover_cols = ["來源換模群組", "目標換模群組", "換模時間_分鐘"]
        missing_changeover_cols = [col for col in required_changeover_cols if col not in changeovers.columns]
        for col in missing_changeover_cols:
            issues.append(f"換模時間缺少必要欄位：{col}。目前偵測到欄位：{detected_columns(changeovers)}")
        if not missing_changeover_cols:
            bad_changeovers = changeovers[changeovers["換模時間_分鐘"].isna() | (changeovers["換模時間_分鐘"] < 0)]
            for _, row in bad_changeovers.iterrows():
                issues.append(f"換模時間不可小於 0：{row.get('來源換模群組', '未知群組')} -> {row.get('目標換模群組', '未知群組')}")
    for product in orders["產品"].dropna().astype(str).unique():
        if product not in products_with_rates:
            issues.append(f"產品 {product} 尚未設定機台產速")

    start, end, _ = get_schedule_window(settings)
    if pd.isna(start) or pd.isna(end) or start >= end:
        issues.append("排程開始時間必須早於排程結束時間")

    return len(issues) == 0, issues, data if len(issues) == 0 else None
