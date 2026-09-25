from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from src.database import (
    load_database_status,
    load_dataset_validation,
    load_docking_validation,
    load_docking_results,
    load_top_pairs,
)


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((PROJECT_ROOT / "config" / "scoring.json").read_text(encoding="utf-8"))

st.set_page_config(
    page_title="UC 药对协同潜力排名",
    page_icon="🧬",
    layout="wide",
)

st.title("UC 药食同源药对协同潜力排名")
st.caption(CONFIG["disclaimer"])

status = load_database_status()
col1, col2, col3, col4 = st.columns(4)
col1.metric("可参与筛选药材", status.get("eligible_herbs", 0))
col2.metric("已收录成分", status.get("ingredients", 0))
col3.metric("标准靶点", status.get("targets", 0))
col4.metric("本批候选药对", status.get("pairs", 0))

results = load_top_pairs(CONFIG["top_n"])

if results.empty:
    st.info("当前真实数据还不足以形成排名。请先在后台完成数据同步和评分。")
else:
    st.subheader("协同潜力前 10 名")
    event = st.dataframe(
        results,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
    )
    st.caption(
        "前10稳定率表示四项权重各自上下扰动20%时的排名稳定性，不是药效概率；"
        "“去掉配伍证据后名次”用于识别结果是否依赖少数文献；"
        "数据完整度只表示靶点名称和成分结构的可标准化比例，不表示疗效；"
        "安全基础分仅来自药食同源资格，不能替代毒理和相互作用评价。"
    )

    selected_rows = event.selection.rows
    if selected_rows:
        selected = results.iloc[selected_rows[0]]
        st.subheader(selected["药对"])
        st.write(
            f"综合分 {selected['综合分']}，证据可信度：{selected['证据可信度']}。"
        )
        st.caption("“初步”表示目前缺少已核验的药对配伍证据或完整安全性数据。")

    st.download_button(
        "下载排名 CSV",
        results.to_csv(index=False).encode("utf-8-sig"),
        file_name="UC药对协同潜力前10名.csv",
        mime="text/csv",
    )

    validation = load_dataset_validation()
    if not validation.empty:
        st.subheader("跨患者数据集验证")
        st.dataframe(validation, hide_index=True, width="stretch")
        st.caption("三套结果独立使用GSE75214、GSE87466及二者共识差异靶点；目前第一名一致。")

    docking = load_docking_results()
    if not docking.empty:
        st.subheader("分子对接二次验证")
        st.dataframe(docking, hide_index=True, width="stretch")
        docking_validation = load_docking_validation()
        if not docking_validation.empty:
            st.markdown("共晶配体回对接质控")
            st.dataframe(docking_validation, hide_index=True, width="stretch")
        st.caption(
            "结合能越低表示虚拟结合越有利；共晶配体回对接RMSD≤2.0埃视为方法学验证通过。"
            "这仍不能证明药对协同或临床疗效，也暂不计入综合分。"
        )
