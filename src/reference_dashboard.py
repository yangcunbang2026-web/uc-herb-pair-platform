from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.dashboard_data import build_pair_detail, load_pair_explorer


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (OSError, UnicodeDecodeError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def _relative(project_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except (OSError, ValueError):
        return str(path)


def _file_size(path: Path) -> str:
    if not path.is_file():
        return ""
    size = path.stat().st_size
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _download(project_root: Path, path: Path, label: str, key: str) -> None:
    if not path.is_file():
        st.caption(f"待生成：`{_relative(project_root, path)}`")
        return
    mime = {
        ".csv": "text/csv",
        ".json": "application/json",
        ".png": "image/png",
        ".md": "text/markdown",
        ".cys": "application/octet-stream",
        ".pdbqt": "chemical/x-pdb",
    }.get(path.suffix.casefold(), "application/octet-stream")
    stable_key = hashlib.sha1(f"{key}:{path}".encode("utf-8")).hexdigest()
    st.download_button(
        f"下载{label} · {_file_size(path)}",
        data=path.read_bytes(),
        file_name=path.name,
        mime=mime,
        key=stable_key,
        width="stretch",
    )
    st.caption(f"`{_relative(project_root, path)}`")


def _download_grid(
    project_root: Path,
    files: list[tuple[str, Path]],
    prefix: str,
) -> None:
    columns = st.columns(3)
    for index, (label, path) in enumerate(files):
        with columns[index % 3]:
            _download(project_root, path, label, f"{prefix}-{index}")


def _chapter_card(
    *,
    index: int,
    label: str,
    title: str,
    action: str,
    result: str,
    hint: str,
    accent: str = "",
) -> bool:
    anchor = f"chapter-{index:02d}"
    st.markdown(f'<div id="{anchor}" class="chapter-anchor"></div>', unsafe_allow_html=True)
    state_key = f"reference_chapter_open_{index}"
    if state_key not in st.session_state:
        st.session_state[state_key] = False
    with st.container(border=True):
        columns = st.columns([0.08, 0.72, 0.20], vertical_alignment="center")
        with columns[0]:
            st.markdown(
                f'<div class="chapter-accent {html.escape(accent)}"></div>',
                unsafe_allow_html=True,
            )
        with columns[1]:
            st.markdown(
                f"""
                <div class="chapter-copy">
                    <div class="chapter-index">{index:02d} · {html.escape(label)}</div>
                    <div class="chapter-title">{html.escape(title)}</div>
                    <div class="chapter-line what"><strong>这一步干什么</strong><span>{html.escape(action)}</span></div>
                    <div class="chapter-line"><strong>直观看到的结果</strong><span>{html.escape(result)}</span></div>
                    <div class="chapter-hint">点开后：{html.escape(hint)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with columns[2]:
            label_text = "收起证据" if st.session_state[state_key] else "查看证据 →"
            if st.button(label_text, key=f"toggle-{index}", width="stretch"):
                st.session_state[state_key] = not st.session_state[state_key]
                st.rerun()
    return bool(st.session_state[state_key])


def _top10_html(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "<p>排名文件尚未生成。</p>"
    rows: list[str] = []
    for row in frame.sort_values("david_refined_rank").head(10).to_dict("records"):
        rank = int(float(row.get("david_refined_rank", 0) or 0))
        pair = html.escape(str(row.get("pair_key", "")))
        target = float(row.get("target_complementarity_score_adjusted", 0) or 0)
        pathway = float(row.get("david_pathway_synergy_score", 0) or 0)
        core = float(row.get("david_refined_core_score", 0) or 0)
        rows.append(
            "<tr>"
            f"<td>{rank}</td><td><strong>{pair}</strong></td>"
            f"<td>{target:.2f}</td><td>{pathway:.2f}</td><td>{core:.2f}</td>"
            "<td>可追溯</td>"
            "</tr>"
        )
    return (
        '<table class="top10-table"><thead><tr>'
        "<th>排名</th><th>药对</th><th>靶点互补</th><th>DAVID通路</th>"
        "<th>阶段核心分</th><th>详情</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _pair_detail_panel(
    *,
    project_root: Path,
    analysis_root: Path,
    disease_targets_path: Path,
    ranking_frame: pd.DataFrame,
) -> None:
    if ranking_frame.empty:
        return
    explorer = load_pair_explorer(
        project_root,
        analysis_root=str(analysis_root),
        disease_targets_path=str(disease_targets_path),
    )
    top_pairs = ranking_frame.sort_values("david_refined_rank").head(10)
    pair_keys = top_pairs["pair_key"].dropna().astype(str).tolist()
    if not pair_keys:
        return
    selected_pair = st.selectbox(
        "选择一组Top10药对查看完整证据",
        pair_keys,
        key="reference_top10_pair",
    )
    detail = build_pair_detail(explorer, selected_pair)
    tabs = st.tabs(["筛选轨迹", "靶点证据", "PPI证据", "DAVID证据", "原始文件"])
    with tabs[0]:
        st.dataframe(pd.DataFrame(detail["trace"]), hide_index=True, width="stretch")
        st.caption(detail["outcome"])
    with tabs[1]:
        metrics = st.columns(6)
        values = [
            (f"{detail['herb_a']}靶点", len(detail["genes_a"])),
            (f"{detail['herb_b']}靶点", len(detail["genes_b"])),
            ("共同靶点", len(detail["shared_genes"])),
            (f"{detail['herb_a']}独有", len(detail["unique_genes_a"])),
            (f"{detail['herb_b']}独有", len(detail["unique_genes_b"])),
            ("组合并集", len(detail["union_genes"])),
        ]
        for column, (label, value) in zip(metrics, values):
            column.metric(label, value)
        with st.expander("查看共同与独有靶点名单"):
            st.markdown(f"**共同靶点：** {'、'.join(detail['shared_genes']) or '无'}")
            st.markdown(
                f"**{detail['herb_a']}独有：** "
                f"{'、'.join(detail['unique_genes_a']) or '无'}"
            )
            st.markdown(
                f"**{detail['herb_b']}独有：** "
                f"{'、'.join(detail['unique_genes_b']) or '无'}"
            )
    with tabs[2]:
        if detail["ppi_edges"].empty:
            st.info("该药对没有进入PPI阶段，或没有可展示的组合内互作边。")
        else:
            st.dataframe(detail["ppi_edges"], hide_index=True, width="stretch")
        if not detail["topology"].empty:
            st.markdown("**组合靶点中的核心节点**")
            st.dataframe(detail["topology"].head(20), hide_index=True, width="stretch")
    with tabs[3]:
        if not detail["significant_kegg"].empty:
            st.markdown("**显著KEGG通路**")
            st.dataframe(detail["significant_kegg"].head(30), hide_index=True, width="stretch")
        if not detail["barrier_terms"].empty:
            focus_label = str(
                explorer.get("scoring", {}).get(
                    "pathway_focus_label", "疾病重点相关条目"
                )
            )
            st.markdown(f"**{focus_label}**")
            st.dataframe(detail["barrier_terms"].head(30), hide_index=True, width="stretch")
        if not detail["significant_go"].empty:
            with st.expander("查看显著GO前30条"):
                st.dataframe(detail["significant_go"].head(30), hide_index=True, width="stretch")
    with tabs[4]:
        _download_grid(
            project_root,
            [
                ("DAVID请求", detail["artifacts"]["david_request"]),
                ("DAVID逐对报告", detail["artifacts"]["david_report"]),
                ("DAVID富集表", detail["artifacts"]["david_table"]),
                ("DAVID API响应", detail["artifacts"]["david_api_response"]),
                ("DAVID网页响应", detail["artifacts"]["david_chart_response"]),
                ("相同输入复用证明", detail["artifacts"]["david_reuse_attestation"]),
            ],
            f"pair-{selected_pair}",
        )


def render_reference_dashboard(
    *,
    project_root: Path,
    selected_task_path: Path,
    selected_task: dict[str, Any],
    selected_summary: dict[str, Any],
    dashboard: dict[str, Any],
) -> None:
    scope = dashboard["scope"]
    ranking = dashboard["ranking"]
    david = dashboard["david"]
    david_single = dashboard["david_single"]
    quality_control = dashboard["quality_control"]
    docking = dashboard.get("docking", {})
    strict_root = Path(dashboard["strict_root"])
    paths = selected_task.get("paths", {})
    disease_label = (
        selected_summary.get("disease_cn")
        or selected_summary.get("disease_en")
        or "目标疾病"
    )
    disease_root = project_root / paths.get("disease_targets_root", "")
    disease_target_candidates = [
        disease_root / "disease_targets_all_normalized.csv",
        disease_root / "disease_targets_normalized.csv",
        disease_root / "uc_genecards_targets_all_normalized.csv",
        disease_root / "uc_genecards_targets_normalized.csv",
    ]
    disease_targets_path = next(
        (path for path in disease_target_candidates if path.is_file()),
        disease_target_candidates[1],
    )
    analysis_root = project_root / (
        paths.get("analysis_root") or paths.get("legacy_analysis_root") or ""
    )

    formal_report = _read_json(strict_root / "report.json")
    cytoscape_report = _read_json(strict_root / "cytoscape_report.json")
    ranking_frame = ranking.get("data", pd.DataFrame())
    if not isinstance(ranking_frame, pd.DataFrame):
        ranking_frame = pd.DataFrame()
    docking_matrix = docking.get("matrix", pd.DataFrame())
    if not isinstance(docking_matrix, pd.DataFrame):
        docking_matrix = pd.DataFrame()
    redocking = docking.get("redocking", {})
    redocking_table = docking.get("redocking_table", pd.DataFrame())
    if not isinstance(redocking_table, pd.DataFrame):
        redocking_table = pd.DataFrame()
    docking_top3 = docking.get("top3", pd.DataFrame())
    if not isinstance(docking_top3, pd.DataFrame):
        docking_top3 = pd.DataFrame()
    docking_attempts = docking.get("attempts", pd.DataFrame())
    if not isinstance(docking_attempts, pd.DataFrame):
        docking_attempts = pd.DataFrame()
    docking_queue = docking.get("queue", pd.DataFrame())
    if not isinstance(docking_queue, pd.DataFrame):
        docking_queue = pd.DataFrame()
    structure_manifest = docking.get("structure_manifest", pd.DataFrame())
    if not isinstance(structure_manifest, pd.DataFrame):
        structure_manifest = pd.DataFrame()

    herb_count = int(scope.get("candidate_herb_count") or selected_summary.get("herb_count") or 0)
    pair_count = int(scope.get("processed_pairs") or selected_summary.get("combination_count") or 0)
    eligible_count = int(scope.get("eligible_pairs") or 0)
    stopped_count = max(pair_count - eligible_count, 0)
    intersection_count = int(scope.get("intersection_targets") or 0)
    top_count = min(10, len(ranking_frame))

    st.markdown(
        """
        <nav class="portal-nav" aria-label="章节导航">
          <div class="portal-brand">中药药对证据看板</div>
          <div class="portal-links">
            <a href="#screening-overview">筛选总览</a>
            <a href="#chapter-01">数据基础</a>
            <a href="#chapter-03">药对筛选</a>
            <a href="#chapter-04">PPI证据</a>
            <a href="#chapter-05">通路证据</a>
            <a href="#chapter-06">Top10</a>
            <a href="#chapter-07">分子对接</a>
          </div>
        </nav>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <section class="portal-hero">
          <div class="hero-kicker">本次任务 · {html.escape(disease_label)} · 两味药组合</div>
          <h1>从原始数据到Top10，每一步都能点开看证据</h1>
          <p>{herb_count}味候选中药 · {pair_count}组两味药对 · 靶点、PPI、通路和分子对接逐步筛选</p>
          <div class="hero-actions">
            <a href="#screening-overview">查看完整筛选过程</a>
            <a class="secondary" href="#chapter-06">直接查看Top10</a>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div id="screening-overview" class="chapter-anchor"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-label">筛选漏斗 · 点击章节进入对应证据</div>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="metric-strip">
          <div class="metric-tile"><div class="metric-number">{herb_count}</div><div class="metric-text">味候选中药</div></div>
          <div class="metric-tile"><div class="metric-number">{pair_count}</div><div class="metric-text">组两味药对</div></div>
          <div class="metric-tile"><div class="metric-number">{eligible_count}</div><div class="metric-text">组通过靶点门槛</div></div>
          <div class="metric-tile"><div class="metric-number">{david.get('completed_pairs', 0)}</div><div class="metric-text">组完成PPI与DAVID</div></div>
          <div class="metric-tile final"><div class="metric-number">Top{top_count}</div><div class="metric-text">前3组完成分子对接</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<div class="section-label">章节导航</div>', unsafe_allow_html=True)
    st.markdown(
        """
        <div class="chapter-nav">
          <a href="#chapter-01">01 数据准备</a>
          <a href="#chapter-02">02 疾病靶点集</a>
          <a href="#chapter-03">03 药对初筛</a>
          <a href="#chapter-04">04 PPI验证</a>
          <a href="#chapter-05">05 DAVID通路</a>
          <a href="#chapter-06">06 Top10结果</a>
          <a href="#chapter-07">07 Top3对接</a>
        </div>
        """,
        unsafe_allow_html=True,
    )

    herb_audit_path = strict_root / "herb_target_audit.csv"
    herb_audit = _read_csv(herb_audit_path)
    source_counts = (
        herb_audit.get("data_source", pd.Series(dtype=str))
        .fillna("来源待核")
        .value_counts()
    )
    source_labels = {
        "TCMSP exact material": "TCMSP",
        "HERB 2.0 exact match HERB004401": "HERB 2.0",
        "ETCM 2.0 exact match LianZi": "ETCM 2.0",
    }
    source_summary = "；".join(
        f"{source_labels.get(str(source), str(source))} {count}味"
        for source, count in source_counts.items()
    ) or "来源统计待生成"
    if _chapter_card(
        index=1,
        label="数据准备",
        title=f"把{herb_count}味中药整理成可计算的标准成分和靶点数据",
        action="统一药材名称，筛选活性成分，标准化Gene Symbol，并保留药材、成分、靶点来源关系。",
        result=f"{herb_count}味全部获得来源记录。{source_summary}",
        hint="候选清单、每味成分数、标准化靶点数和来源文件。",
    ):
        with st.expander("01 数据准备证据详情", expanded=True):
            candidates = scope.get("candidates", pd.DataFrame())
            if isinstance(candidates, pd.DataFrame) and not candidates.empty:
                st.dataframe(candidates, hide_index=True, width="stretch")
            if not herb_audit.empty:
                st.markdown("**药材成分和靶点来源审计**")
                st.dataframe(herb_audit, hide_index=True, width="stretch")
            _download_grid(
                project_root,
                [
                    ("候选中药清单", Path(scope["candidate_path"])),
                    ("药材靶点审计", herb_audit_path),
                    ("分析总报告", strict_root / "report.json"),
                    ("任务配置", selected_task_path),
                    (
                        "TCMSP抓取报告",
                        project_root / paths.get("tcmsp_evidence_root", "") / "channel_test_report.json",
                    ),
                ],
                "chapter-01",
            )
    st.markdown('<div class="chapter-gap"></div>', unsafe_allow_html=True)

    disease_targets = _read_csv(disease_targets_path)
    disease_total = int(formal_report.get("genecards_targets", len(disease_targets)) or 0)
    if _chapter_card(
        index=2,
        label=f"{disease_label}靶点集",
        title=f"只留下每味药真正与{disease_label}相关的作用靶点",
        action="读取疾病靶点，按阈值过滤并与每味药的标准靶点取交集。",
        result=f"疾病靶点{disease_total}个，最终形成{intersection_count}个药物和疾病交集靶点。",
        hint="疾病靶点表、阈值、药材交集数量和基因名单。",
    ):
        with st.expander("02 疾病靶点与交集证据详情", expanded=True):
            columns = st.columns(3)
            columns[0].metric("GeneCards疾病靶点", disease_total)
            columns[1].metric("药物疾病交集靶点", intersection_count)
            columns[2].metric("物种", selected_summary.get("species", "Homo sapiens"))
            if not disease_targets.empty:
                st.dataframe(disease_targets, hide_index=True, width="stretch", height=420)
            _download_grid(
                project_root,
                [
                    ("GeneCards标准化靶点", disease_targets_path),
                    ("药材靶点审计", herb_audit_path),
                    ("正式分析报告", strict_root / "report.json"),
                ],
                "chapter-02",
            )
    st.markdown('<div class="chapter-gap"></div>', unsafe_allow_html=True)

    raw_pairs_path = strict_root / "pair_scores_raw_formula_audit.csv"
    eligible_pairs_path = strict_root / "pair_scores_provisional.csv"
    raw_pairs = _read_csv(raw_pairs_path)
    eligible_pairs = _read_csv(eligible_pairs_path)
    if _chapter_card(
        index=3,
        label="药对靶点初筛",
        title=f"{pair_count}组药对中，{eligible_count}组满足双方独立贡献门槛",
        action="计算共同靶点、两侧独有靶点、组合并集和贡献平衡，先用硬门槛减少后续工作量。",
        result=f"{eligible_count}组晋级，{stopped_count}组止于本关；每一组都有明确停止原因。",
        hint="Na、Nb、共同和独有靶点、公式代入值，以及为什么晋级或停止。",
        accent="orange",
    ):
        with st.expander("03 药对初筛证据详情", expanded=True):
            tabs = st.tabs(["全部组合", "通过门槛", "止于门槛", "原始文件"])
            with tabs[0]:
                st.dataframe(raw_pairs, hide_index=True, width="stretch", height=430)
            with tabs[1]:
                st.dataframe(eligible_pairs, hide_index=True, width="stretch", height=430)
            with tabs[2]:
                if not raw_pairs.empty and "eligible_both_herbs_contribute" in raw_pairs:
                    stopped = raw_pairs[~raw_pairs["eligible_both_herbs_contribute"].fillna(False)]
                    st.dataframe(stopped, hide_index=True, width="stretch", height=430)
                else:
                    st.info("淘汰明细尚未生成。")
            with tabs[3]:
                _download_grid(
                    project_root,
                    [
                        ("276组公式审计", raw_pairs_path),
                        ("153组合格药对", eligible_pairs_path),
                        ("来源偏差校正排名", strict_root / "pair_scores_source_bias_adjusted.csv"),
                        ("评分配置", project_root / paths.get("scoring_config", "config/scoring.json")),
                    ],
                    "chapter-03",
                )
    st.markdown('<div class="chapter-gap"></div>', unsafe_allow_html=True)

    ppi_nodes = int(cytoscape_report.get("nodes", 0) or 0)
    ppi_edges = int(cytoscape_report.get("edges", 0) or 0)
    ppi_dropped = int(
        cytoscape_report.get("dropped_edges_with_unmatched_string_preferred_names", 0) or 0
    )
    if _chapter_card(
        index=4,
        label="PPI网络证据",
        title="查看药对靶点在STRING全局网络中的连接情况",
        action="从疾病交集靶点构建STRING网络，再用Cytoscape计算节点度、互作边和核心节点。",
        result=f"保留{ppi_nodes}个节点和{ppi_edges}条边，另有{ppi_dropped}条名称端点不匹配边被单独记录。",
        hint="STRING原始响应、combined_score、网络图、拓扑表和Cytoscape会话。",
        accent="blue",
    ):
        with st.expander("04 PPI网络证据详情", expanded=True):
            network_image = strict_root / "formal_ppi_network.png"
            if network_image.is_file():
                st.image(str(network_image), caption="当前任务疾病交集靶点的STRING/Cytoscape全局PPI网络")
            topology = _read_csv(strict_root / "cytoscape_topology_metrics.csv")
            if not topology.empty:
                st.markdown("**拓扑排名前30个节点**")
                st.dataframe(topology.head(30), hide_index=True, width="stretch")
            _download_grid(
                project_root,
                [
                    ("STRING原始响应", strict_root / "string_network_raw.json"),
                    ("Cytoscape报告", strict_root / "cytoscape_report.json"),
                    ("PPI节点表", strict_root / "cytoscape_nodes.csv"),
                    ("PPI边表", strict_root / "cytoscape_edges.csv"),
                    ("剔除边记录", strict_root / "cytoscape_dropped_edges.csv"),
                    ("Cytoscape会话", strict_root / "formal_ppi_session.cys"),
                ],
                "chapter-04",
            )
    st.markdown('<div class="chapter-gap"></div>', unsafe_allow_html=True)

    if _chapter_card(
        index=5,
        label="DAVID通路证据",
        title=f"用GO和KEGG结果判断组合覆盖了哪些{disease_label}相关功能",
        action=f"对全部合格药对和全部单味药基线运行DAVID，比较组合新增通路和{disease_label}重点相关条目。",
        result=(
            f"药对{david.get('completed_pairs', 0)}/{david.get('requested_pairs', 0)}组，"
            f"单药{david_single.get('completed_pairs', 0)}/{david_single.get('requested_pairs', 0)}味完成。"
        ),
        hint="每组请求、响应、P值、Benjamini值、GO/KEGG和组合对单药比较。",
        accent="green",
    ):
        with st.expander("05 DAVID通路证据详情", expanded=True):
            st.progress(
                david.get("percent", 0),
                text=f"全部合格药对：{david.get('completed_pairs', 0)} / {david.get('requested_pairs', 0)}",
            )
            st.progress(
                david_single.get("percent", 0),
                text=(
                    f"全部单味药基线：{david_single.get('completed_pairs', 0)} / "
                    f"{david_single.get('requested_pairs', 0)}"
                ),
            )
            pathway_metrics = _read_csv(david["root"] / "pair_vs_single_pathway_metrics.csv")
            if not pathway_metrics.empty:
                focus_count_column = (
                    "pair_focus_term_count"
                    if "pair_focus_term_count" in pathway_metrics.columns
                    else "pair_barrier_term_count"
                )
                show_columns = [
                    column
                    for column in (
                        "pair_key",
                        "pair_significant_kegg_count",
                        "emergent_pair_kegg_count",
                        "emergent_pair_kegg_ratio",
                        focus_count_column,
                    )
                    if column in pathway_metrics.columns
                ]
                st.dataframe(pathway_metrics[show_columns].head(30), hide_index=True, width="stretch")
            _download_grid(
                project_root,
                [
                    ("DAVID药对总报告", Path(david["report_path"])),
                    ("单味基线总报告", Path(david_single["report_path"])),
                    ("通路评分报告", david["root"] / "david_pathway_scoring_report.json"),
                    ("组合对单药通路指标", david["root"] / "pair_vs_single_pathway_metrics.csv"),
                    ("UniProt映射审计", david["root"] / "uniprot_mapping_audit.csv"),
                ],
                "chapter-05",
            )
    st.markdown('<div class="chapter-gap"></div>', unsafe_allow_html=True)

    st.markdown('<div id="chapter-06" class="chapter-anchor"></div>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <section class="top10-panel">
          <div class="hero-kicker">06 · 最终生信结果</div>
          <h2>Top10 协同潜力排名</h2>
          <p>每个排名都能返回靶点、PPI、DAVID和原始数据库证据。当前是实验前优先验证顺序，不是临床药方。</p>
          {_top10_html(ranking_frame)}
          <div class="top10-foot">安全性和配伍文献仍待人工复核，因此页面明确标记为阶段性协同潜力排名。</div>
        </section>
        """,
        unsafe_allow_html=True,
    )
    with st.expander("查看Top10药对的逐步证据", expanded=False):
        _pair_detail_panel(
            project_root=project_root,
            analysis_root=analysis_root,
            disease_targets_path=disease_targets_path,
            ranking_frame=ranking_frame,
        )
        ranking_path = Path(ranking.get("path", ""))
        if ranking_path.is_file():
            _download(project_root, ranking_path, "Top10阶段排名", "chapter-06-ranking")
    st.markdown('<div class="chapter-gap"></div>', unsafe_allow_html=True)

    docking_count = (
        int(docking_matrix["pair_rank"].nunique())
        if not docking_matrix.empty and "pair_rank" in docking_matrix
        else 0
    )
    rmsd = float(redocking.get("symmetry_aware_heavy_atom_rmsd_angstrom", 0) or 0)
    best_energy = (
        float(pd.to_numeric(docking_matrix["best_affinity_kcal_mol"], errors="coerce").min())
        if not docking_matrix.empty and "best_affinity_kcal_mol" in docking_matrix
        else 0.0
    )
    if _chapter_card(
        index=7,
        label="Top3分子对接验证",
        title="核心成分与核心靶点的结合活性验证",
        action="Top3药对接入PDB、PubChem、Meeko和AutoDock Vina，先做共晶配体回对接质控。",
        result=f"{docking_count}/3组完成；回对接RMSD {rmsd:.4f} Å；最佳结合能 {best_energy:.3f} kcal/mol。",
        hint="PDB编号、PubChem CID、Grid Box、Vina参数、日志、构象和结合能矩阵。",
    ):
        with st.expander("07 Top3分子对接证据详情", expanded=True):
            if docking_matrix.empty:
                st.info("Top3分子对接尚未完成。")
            else:
                columns = st.columns(4)
                columns[0].metric("已完成药对", docking_count)
                columns[1].metric("对接成分", docking_matrix["ingredient_name"].nunique())
                columns[2].metric("回对接RMSD", f"{rmsd:.4f} Å")
                columns[3].metric("最佳结合能", f"{best_energy:.3f} kcal/mol")
                if redocking.get("passed"):
                    st.success("共晶配体回对接通过，所有正式结果均来自RMSD≤2.0 Å的结构。")
                tabs = st.tabs(["最终成功的3组", "回对接质控", "尝试与顺延", "结构与原始证据"])
                with tabs[0]:
                    if not docking_top3.empty:
                        top3_columns = [
                            column
                            for column in (
                                "pair_rank", "pair_key", "herb_a", "herb_b",
                                "target_gene", "structure_source",
                            )
                            if column in docking_top3.columns
                        ]
                        st.markdown("**原始协同潜力排名 → 最终完成对接的药对**")
                        st.dataframe(
                            docking_top3[top3_columns], hide_index=True, width="stretch"
                        )
                    display_columns = [
                        column
                        for column in (
                            "pair_rank",
                            "pair_key",
                            "herb_name",
                            "ingredient_name",
                            "target_gene",
                            "pdb_id",
                            "pubchem_cid",
                            "best_affinity_kcal_mol",
                        )
                        if column in docking_matrix.columns
                    ]
                    st.markdown("**每味药实际采用的成分、靶点和结合能**")
                    st.dataframe(
                        docking_matrix[display_columns], hide_index=True, width="stretch"
                    )
                with tabs[1]:
                    st.caption(
                        "先把PDB自带的共晶配体拿出来重新对接。只有重现原结合姿势且"
                        "RMSD≤2.0 Å，该结构才允许用于正式对接。"
                    )
                    if redocking_table.empty:
                        st.json(redocking)
                    else:
                        validation_columns = [
                            column
                            for column in (
                                "target_gene", "uniprot_id", "pdb_id", "receptor_chain",
                                "resolution_angstrom", "reference_ligand",
                                "best_affinity_kcal_mol",
                                "symmetry_aware_heavy_atom_rmsd_angstrom",
                                "threshold_angstrom", "passed",
                            )
                            if column in redocking_table.columns
                        ]
                        st.dataframe(
                            redocking_table[validation_columns],
                            hide_index=True,
                            width="stretch",
                        )
                with tabs[2]:
                    failed_attempts = (
                        docking_attempts[
                            docking_attempts.get("status", pd.Series(dtype=str))
                            .astype(str)
                            .ne("completed")
                        ]
                        if not docking_attempts.empty and "status" in docking_attempts
                        else pd.DataFrame()
                    )
                    metrics = st.columns(3)
                    metrics[0].metric("实际尝试次数", len(docking_attempts))
                    metrics[1].metric("失败后自动回退", len(failed_attempts))
                    metrics[2].metric(
                        "排队候选靶点/PDB入口",
                        len(docking_queue),
                    )
                    st.caption(
                        "系统按原排名向下处理；结构不合格就换PDB或靶点，成分失败就换成分，"
                        "整组仍失败才顺延下一药对。失败原因原样保留，不把失败结果包装成通过。"
                    )
                    if docking_attempts.empty:
                        st.info("本任务没有生成尝试审计表。")
                    else:
                        attempt_columns = [
                            column
                            for column in (
                                "pair_rank", "pair_key", "target_gene", "status", "reason"
                            )
                            if column in docking_attempts.columns
                        ]
                        st.dataframe(
                            docking_attempts[attempt_columns], hide_index=True, width="stretch"
                        )
                    if not docking_queue.empty:
                        with st.expander("查看尚未动用的后备靶点队列"):
                            queue_columns = [
                                column
                                for column in (
                                    "pair_rank", "pair_key", "target_gene",
                                    "target_priority", "structure_source",
                                )
                                if column in docking_queue.columns
                            ]
                            st.dataframe(
                                docking_queue[queue_columns], hide_index=True, width="stretch"
                            )
                with tabs[3]:
                    if not structure_manifest.empty:
                        st.markdown("**蛋白与配体来源、编号和文件哈希**")
                        st.dataframe(structure_manifest, hide_index=True, width="stretch")
                    evidence_root = strict_root / "docking_top3"
                    evidence_files = sorted(
                        path for path in evidence_root.glob("target_*/*")
                        if path.is_file() and path.suffix in {".log", ".pdbqt", ".pdb", ".sdf", ".json", ".txt"}
                    )
                    if evidence_files:
                        chosen_evidence = st.selectbox(
                            "选择原始结构、运行日志或对接构象",
                            evidence_files,
                            format_func=lambda path: path.relative_to(evidence_root).as_posix(),
                            key="docking_raw_evidence_file",
                        )
                        _download(project_root, chosen_evidence, "所选对接证据", "docking-raw-file")
                docking_root = strict_root / "docking_top3"
                _download_grid(
                    project_root,
                    [
                        ("Top3锁定表", docking_root / "top3_candidates.csv"),
                        ("全部尝试与失败原因", docking_root / "docking_attempt_audit.csv"),
                        ("后备靶点队列", docking_root / "docking_pair_queue.csv"),
                        ("候选选择报告", docking_root / "selection_report.json"),
                        ("结构来源清单", docking_root / "structure_manifest.csv"),
                        ("对接参数", docking_root / "docking_parameters.json"),
                        ("结合能矩阵", docking_root / "binding_energy_matrix.csv"),
                        ("回对接质控", docking_root / "redocking_validation.json"),
                        ("分子对接总报告", docking_root / "report.json"),
                    ],
                    "chapter-07",
                )

    st.markdown('<div class="section-label">点击“查看证据”后如何展开</div>', unsafe_allow_html=True)
    st.markdown(
        """
        <div class="evidence-flow">
          <div>输入数据</div><div>处理规则</div><div>直观结果</div>
          <div>保留/淘汰名单</div><div>原始证明文件</div><div>返回本章</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("运行记录与机器质控", expanded=False):
        if quality_control.get("available"):
            counts = quality_control["counts"]
            columns = st.columns(4)
            columns[0].metric("通过", counts.get("passed", 0))
            columns[1].metric("待人工", counts.get("pending", 0))
            columns[2].metric("警告", counts.get("warning", 0))
            columns[3].metric("错误", counts.get("failed", 0))
            checks = pd.DataFrame(quality_control.get("checks", []))
            if not checks.empty:
                columns_to_show = [
                    column for column in ("id", "status", "message") if column in checks
                ]
                st.dataframe(checks[columns_to_show], hide_index=True, width="stretch")
            _download(
                project_root,
                Path(quality_control["report_path"]),
                "机器质控报告",
                "machine-qc",
            )
        else:
            st.info("机器质控报告尚未生成。")

    st.markdown(
        '<div class="boundary-note"><strong>结果边界：</strong>'
        "网络药理学、PPI、DAVID和分子对接只形成协同潜力候选，不能单独证明1+1＞2，"
        "也不构成临床处方或治疗建议。</div>",
        unsafe_allow_html=True,
    )

