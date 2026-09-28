"""Convert confirmed source matches to the existing single-machine scheduler."""
from __future__ import annotations

from hashlib import sha256
import json

import pandas as pd

from .import_catalog import candidate_records, mapping_key, matching_dimensions, parse_spec, rounded
from .import_orders import basic_order_issues, batch_must_stop

SUPPORTED = ("C2", "C4", "C5")


def database_signature(capacity_bytes, mold_bytes):
    return sha256(capacity_bytes).hexdigest() + ":" + sha256(mold_bytes).hexdigest()


def export_mappings(signature, mappings):
    return json.dumps({"version": 1, "databases": signature, "mappings": mappings}, ensure_ascii=False, indent=2).encode("utf-8")


def load_mappings(content, signature):
    data = json.loads(content)
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("databases") != signature:
        raise ValueError("對應檔版本或資料庫不符；資料庫變更後須重新確認對應")
    mappings = data.get("mappings")
    if not isinstance(mappings, dict):
        raise ValueError("對應檔格式錯誤")
    for key, value in mappings.items():
        if not isinstance(key, str) or not isinstance(value, dict) or not isinstance(value.get("machines", {}), dict):
            raise ValueError("對應檔內容錯誤")
        if value.get("no_machine") not in (None, True, False):
            raise ValueError("對應檔機台標記錯誤")
        for machine, pair in value.get("machines", {}).items():
            if machine not in SUPPORTED or not isinstance(pair, dict) or not all(isinstance(pair.get(k), str) for k in ("rate", "mold")):
                raise ValueError("對應檔機台來源格式錯誤")
    return mappings


def prepare_import(orders, capacity, molds, mappings):
    basic = basic_order_issues(orders)
    rates_by_ref = {r["ref"]: r for r in capacity}
    molds_by_ref = {r["ref"]: r for r in molds}
    accepted, rates, issues = [], {}, []
    for index, row in orders.iterrows():
        reasons = list(basic.get(index, []))
        spec = parse_spec(row["規格"])
        key = mapping_key(row)
        selected = mappings.get(key)
        rate_candidates = candidate_records(row, capacity)
        mold_candidates = candidate_records(row, molds, True)
        if selected and selected.get("no_machine"):
            reasons.append("無符合機台")
        else:
            if not all(spec.get(k, 0) > 0 for k in ["inner", "outer", "thickness", "length_m"]):
                reasons.append("規格缺少可辨識的內徑、外徑／厚度或每件長度")
            if not selected:
                reasons.append("尚未確認品名／規格與資料庫對應")
                if not rate_candidates:
                    reasons.append("查無相符管徑的產能資料")
                if not mold_candidates:
                    reasons.append("查無相符規格的模具資料")
                if rate_candidates and not any(r["machines"] == [m] and not r["reason"] and r["speed"] for r in rate_candidates for m in SUPPORTED):
                    reasons.append("C2／C4／C5 查無可直接使用的個別機台實際產速")
            valid_rates = []
            for machine, pair in (selected or {}).get("machines", {}).items():
                rate, mold = rates_by_ref.get(pair.get("rate")), molds_by_ref.get(pair.get("mold"))
                if machine not in SUPPORTED or not rate or not mold:
                    reasons.append(f"{machine}：對應來源不存在，請重新確認")
                    continue
                if (rate["machines"] != [machine] or rate["reason"] or not rate["speed"]
                        or machine not in mold["machines"]
                        or not matching_dimensions(spec, rate) or not matching_dimensions(spec, mold, True)):
                    reasons.append(f"{machine}：產速或模具條件不符，請重新確認")
                    continue
                if not spec.get("length_m"):
                    continue
                valid_rates.append({"產品": key, "機台": machine,
                                    "產速_PCS_per_hr": rate["speed"] * 60 / spec["length_m"],
                                    "換模群組": f"ID{rounded(spec['inner'])}-T{rounded(spec['thickness'])}",
                                    "實際產速_M每分": rate["speed"], "每件長度_M": spec["length_m"],
                                    "產速來源": rate["ref"], "模具來源": mold["ref"]})
            if selected and not selected.get("machines"):
                reasons.append("無符合機台")
            if not reasons and valid_rates:
                accepted.append({"工單編號": row["製令單號"], "產品": key,
                                 "數量": float(row["預計產量"]), "單位": "PCS", "優先級": row["優先級"],
                                 "交期": pd.Timestamp(row["生產完成日"]) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1),
                                 "允許機台": ",".join(r["機台"] for r in valid_rates),
                                 "來源產品品號": row["產品品號"], "品名": row["品名"], "規格": row["規格"],
                                 "結關日": row["結關日"], "生產完成日": row["生產完成日"],
                                 "來源檔案": row["來源檔案"], "來源列": row["來源列"]})
                rates.update({(key, r["機台"]): r for r in valid_rates})
        if reasons:
            issues.append({**row.to_dict(), "工單編號": row["製令單號"], "缺漏原因": "；".join(dict.fromkeys(reasons)),
                           "候選資料": json.dumps({"產能": rate_candidates, "模具": mold_candidates}, ensure_ascii=False)})
    blocked = batch_must_stop(len(orders), len(issues))
    workbook = {"待排工單": pd.DataFrame(accepted), "產品機台產速": pd.DataFrame(rates.values()),
                "匯入未排工單": pd.DataFrame(issues)}
    return workbook, pd.DataFrame(issues), blocked


def attach_import_results(result, workbook):
    orders = workbook["待排工單"]
    metadata = [c for c in ["來源產品品號", "品名", "規格", "結關日", "生產完成日", "來源檔案", "來源列"] if c in orders]
    if metadata:
        result = result.merge(orders[["工單編號"] + metadata], on="工單編號", how="left", validate="one_to_one")
    pending = workbook.get("匯入未排工單", pd.DataFrame())
    rows = []
    for _, row in pending.iterrows():
        rows.append({"排程順序": len(result) + len(rows) + 1, "工單編號": row["工單編號"],
                     "產品": row["產品品號"], "數量": pd.to_numeric(row["預計產量"], errors="coerce"),
                     "單位": row["單位"], "優先級": row["優先級"], "交期": pd.Timestamp(row["生產完成日"]),
                     "指派機台": "未排入", "產速": 0., "加工時間（小時）": 0., "換模時間（小時）": 0.,
                     "開始時間": pd.NaT, "結束時間": pd.NaT, "狀態": "匯入資料待處理", "是否遲交": False,
                     "遲交時間（小時）": 0., "等待時間（小時）": 0., "備註": row["缺漏原因"],
                     "候選資料": row["候選資料"], **{c: row["產品品號"] if c == "來源產品品號" else row[c] for c in metadata}})
    if rows:
        result = pd.concat([result, pd.DataFrame(rows)], ignore_index=True)
    # Internal keys distinguish identical product codes with different specifications.
    if "來源產品品號" in result:
        result["產品"] = result["來源產品品號"].fillna(result["產品"])
    return result
