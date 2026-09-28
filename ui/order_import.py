"""Session-local factory inputs and user-confirmed reusable matches."""
from hashlib import sha256

import pandas as pd
import streamlit as st

from aps.import_catalog import candidate_records, mapping_key, read_capacity, read_molds, parse_spec
from aps.import_orders import combine_order_batches, read_exported_orders, reveal_spec_columns
from aps.import_pipeline import database_signature, export_mappings, load_mappings, prepare_import, SUPPORTED
from aps.validator import validate_workbook


def invalidate_schedule():
    for key in ["workbook", "validation", "schedule_df", "kpis", "comparison_df", "last_version"]:
        st.session_state[key] = None


def record_label(record):
    return (f"{record['ref']}｜{record['name']}｜ID {record['inner']} mm｜"
            + (f"OD {record['outer']} mm｜{record['spec']}｜{record['speed']} M/分"
               if "speed" in record else f"厚 {record['thickness']} mm"))


def record_frame(records):
    return pd.DataFrame(records).rename(columns={"ref": "來源", "sheet": "工作表", "row": "列號", "name": "品名", "spec": "規格",
        "inner": "內徑_mm", "outer": "外徑_mm", "thickness": "厚度_mm", "speed": "實際產速_M每分",
        "machines": "生產或適用機台", "backup": "備用機台", "reason": "需確認原因", "detail": "原列資料"})


def render_order_import():
    state = st.session_state
    st.subheader("訂單拋轉匯入")
    st.caption("先上傳兩份資料庫，當次工作階段可重複匯入訂單。資料庫與對應檔不會寫入 GitHub。")
    cols = st.columns(2)
    capacity_file = cols[0].file_uploader("機台產能資料庫", type=["xls", "xlsx"], key="capacity_upload")
    mold_file = cols[1].file_uploader("模具規格資料庫", type=["xls", "xlsx"], key="mold_upload")
    files = st.file_uploader("訂單拋轉 Excel（可多選）", type=["xlsx"], accept_multiple_files=True, key="orders_upload")
    upload_key = tuple((f.name, sha256(f.getvalue()).hexdigest()) for f in [capacity_file, mold_file, *files] if f)
    if state.get("import_upload_key") != upload_key:
        invalidate_schedule()
        state.import_upload_key = upload_key
        state.pop("import_orders", None)
        state.pop("import_error", None)
    if not capacity_file or not mold_file:
        st.info("請上傳機台產能及模具規格兩份資料庫。")
        return
    signature = database_signature(capacity_file.getvalue(), mold_file.getvalue())
    try:
        if state.get("import_db_signature") != signature:
            capacity, rate_warnings = read_capacity(capacity_file.getvalue(), capacity_file.name)
            molds, mold_warnings = read_molds(mold_file.getvalue(), mold_file.name)
            state.import_capacity, state.import_molds = capacity, molds
            state.import_db_warnings = rate_warnings + mold_warnings
            state.import_mappings = {}
            state.import_db_signature = signature
        capacity, molds = state.import_capacity, state.import_molds
    except Exception as exc:
        st.error(f"資料庫讀取失敗：{exc}")
        return
    with st.expander("資料庫讀取結果與需確認的記錄"):
        st.write(f"產能 {len(capacity)} 筆；模具 {len(molds)} 筆。")
        for message in state.import_db_warnings:
            st.caption(message)
        invalid = [r for r in capacity if r["reason"] or not r["speed"]]
        if invalid:
            st.dataframe(record_frame(invalid), use_container_width=True)
    mapping_file = st.file_uploader("載入先前下載的對應檔（選填）", type=["json"], key="mapping_upload")
    if st.button("套用對應檔", disabled=mapping_file is None):
        try:
            state.import_mappings = load_mappings(mapping_file.getvalue(), signature)
            invalidate_schedule()
            st.success("對應檔已載入；仍會檢查來源記錄與規格。")
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    st.download_button("下載已確認的對應檔", export_mappings(signature, state.import_mappings),
                       "APS_產品對應.json", "application/json")
    if not files:
        st.info("資料庫已就緒，請上傳訂單。")
        return
    try:
        if "import_orders" not in state:
            frames = []
            for uploaded in files:
                if uploaded.name.startswith("~$"):
                    raise ValueError(f"{uploaded.name} 是 Excel 暫存鎖定檔，請移除")
                try:
                    frames.append(read_exported_orders(uploaded.getvalue(), uploaded.name))
                except Exception as exc:
                    raise ValueError(f"{uploaded.name}：{exc}") from exc
            state.import_orders = combine_order_batches(frames)
        orders = state.import_orders
        if orders.empty:
            st.warning("待排工單目前沒有資料，請上傳有訂單的檔案。")
            return
    except Exception as exc:
        st.error(f"訂單讀取失敗，整批尚未載入：{exc}")
        return
    st.caption("規格欄完整顯示（包括來源 Excel 隱藏欄）。生產完成日＝結關日前 3 個平日；長度不影響換模。")
    with st.expander("下載解除規格欄隱藏的原檔副本"):
        for file_index, file in enumerate(files):
            st.download_button(f"下載 {file.name}", reveal_spec_columns(file.getvalue()),
                               "顯示規格_" + file.name, key=f"unhide_{file_index}_{file.name}")
    st.caption("急單預設為結關日最早的前 10%（向上取整、同日全部納入）；若全批同日，整批會預設急單。")
    edited = st.data_editor(orders, disabled=[c for c in orders.columns if c != "優先級"],
                            column_config={"優先級": st.column_config.SelectboxColumn(options=["急單", "一般"])},
                            hide_index=True, use_container_width=True, key="import_priority_" + sha256(str(upload_key).encode()).hexdigest()[:12])
    if st.button("保存急迫程度修改"):
        state.import_orders = edited.copy()
        orders = state.import_orders
        invalidate_schedule()
        st.success("急迫程度已保存。")
    products = orders.drop_duplicates(["產品品號", "品名", "規格"])
    by_key = {mapping_key(row): row for _, row in products.iterrows()}
    unresolved_only = st.checkbox("僅顯示尚未確認的產品", value=True)
    keys = [k for k in by_key if not unresolved_only or k not in state.import_mappings]
    st.write(f"本批 {len(products)} 種產品／規格，已確認 {sum(k in state.import_mappings for k in by_key)} 種。")
    if keys:
        key = st.selectbox("選擇產品確認對應", keys, format_func=lambda k: f"{by_key[k]['產品品號']}｜{by_key[k]['品名']}｜{by_key[k]['規格']}")
        row = by_key[key]
        spec = parse_spec(row["規格"])
        st.write("規格換算：" + "；".join(f"{label} {spec.get(field, '缺少')}" for field, label in
                                         [("inner", "內徑 mm"), ("outer", "外徑 mm"), ("thickness", "厚度 mm"), ("length_m", "每件長度 M")]))
        rate_candidates = candidate_records(row, capacity)
        mold_candidates = candidate_records(row, molds, True)
        st.caption("以下依規格篩選、品名相似程度排序；請核對品名、用途與來源，不會自動選取近似資料。")
        with st.expander("查看候選資料、備用機台與來源"):
            st.dataframe(record_frame(rate_candidates), use_container_width=True)
            st.dataframe(record_frame(mold_candidates), use_container_width=True)
        previous = state.import_mappings.get(key, {})
        with st.form("confirm_" + key):
            no_machine = st.checkbox("確認此產品無符合機台（列入未排工單）", value=previous.get("no_machine", False))
            pairs = {}
            for machine in SUPPORTED:
                rc = {r["ref"]: r for r in rate_candidates if r["machines"] == [machine] and not r["reason"] and r["speed"]}
                mc = {r["ref"]: r for r in mold_candidates if machine in r["machines"]}
                cols = st.columns(2)
                old = previous.get("machines", {}).get(machine, {})
                ro, mo = [""] + list(rc), [""] + list(mc)
                selected_rate = cols[0].selectbox(f"{machine} 實際產速來源", ro,
                    index=ro.index(old.get("rate", "")) if old.get("rate", "") in ro else 0,
                    format_func=lambda r, lookup=rc: record_label(lookup[r]) if r else "不使用／尚未選取")
                selected_mold = cols[1].selectbox(f"{machine} 模具來源", mo,
                    index=mo.index(old.get("mold", "")) if old.get("mold", "") in mo else 0,
                    format_func=lambda r, lookup=mc: record_label(lookup[r]) if r else "不使用／尚未選取")
                if selected_rate or selected_mold:
                    pairs[machine] = {"rate": selected_rate, "mold": selected_mold}
                if not rc or not mc:
                    st.caption(f"{machine}：{'缺少該機台的實際產速；' if not rc else ''}{'缺少相符模具' if not mc else ''}")
            submit = st.form_submit_button("確認並保存此產品對應")
        if submit:
            if no_machine and pairs:
                st.error("無符合機台與已選機台來源衝突，請先清除機台選項。")
            elif not no_machine and (not pairs or any(not p["rate"] or not p["mold"] for p in pairs.values())):
                st.error("每台使用機台須同時選定實際產速與模具來源；無可用機台請明確勾選。")
            else:
                state.import_mappings[key] = {"no_machine": no_machine, "machines": pairs}
                invalidate_schedule()
                st.rerun()
    workbook, issues, blocked = prepare_import(orders, capacity, molds, state.import_mappings)
    ratio = len(issues) / len(orders)
    st.write(f"問題工單 {len(issues)}／{len(orders)}（{ratio:.1%}），完整工單 {len(workbook['待排工單'])} 筆。")
    if not issues.empty:
        st.dataframe(issues[["工單編號", "品名", "規格", "缺漏原因", "來源檔案", "來源列"]], use_container_width=True)
        with st.expander("各問題工單的候選資料"):
            st.dataframe(issues[["工單編號", "候選資料"]], use_container_width=True)
        st.download_button("下載缺漏與候選資料 CSV", issues.to_csv(index=False).encode("utf-8-sig"), "APS_缺漏回報.csv", "text/csv")
    if blocked:
        st.error("問題工單比例達 30%，整批停止。請補齊資料或確認對應後再排程。")
    else:
        st.info("可排程完整工單；問題工單會保留在未排工單、KPI 及匯出結果。")
    if st.button("完成匯入，套用至排程", disabled=blocked):
        validation = validate_workbook(workbook)
        if not validation[0]:
            st.error("；".join(validation[1]))
        else:
            state.workbook = workbook
            state.validation = validation
            state.upload_filename = "、".join(f.name for f in files)
            state.schedule_df = state.kpis = state.comparison_df = None
            st.success("已套用，請確認下方排程期間後按「確認設定並開始排程」。")
