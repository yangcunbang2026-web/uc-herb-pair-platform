from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.dashboard_data import (
    build_pair_detail,
    load_active_ingredients,
    load_current_dashboard,
    load_pair_explorer,
    ranking_display,
    read_csv,
)
from src.dashboard_ui import (
    inject_dashboard_theme,
    inject_reference_layout_theme,
    render_screening_funnel,
)
from src.reference_dashboard import render_reference_dashboard
from src.staged_formula_dashboard import render_staged_formula_dashboard
from src.remote_compute_ui import render_remote_compute_panel
from src.job_builder import (
    load_config,
    normalize_herbs,
    task_summary,
)
from src.task_launcher import create_task, launch_task, load_task_status
from src.database import (
    load_database_status,
    load_dataset_validation,
    load_docking_results,
    load_docking_validation,
    load_top_pairs,
)


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "scoring.json"
CONFIG = (
    json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if CONFIG_PATH.is_file()
    else {"top_n": 10, "disclaimer": "结果仅用于科研候选筛选，不构成临床建议。"}
)


@st.cache_data(ttl=10, show_spinner=False)
def load_dashboard(task_config: str | None = None) -> dict[str, Any]:
    return load_current_dashboard(PROJECT_ROOT, task_config=task_config)


@st.cache_data(ttl=30, show_spinner=False)
def load_pipeline_manifests() -> list[dict[str, Any]]:
    runs_root = PROJECT_ROOT / "runs"
    manifests: list[dict[str, Any]] = []
    if not runs_root.exists():
        return manifests
    for path in runs_root.glob("*/run_manifest.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        payload["_manifest_path"] = path
        manifests.append(payload)
    return sorted(manifests, key=lambda item: item.get("started_at", ""), reverse=True)


@st.cache_data(ttl=30, show_spinner=False)
def load_explorer(
    analysis_root: str | None = None,
    disease_targets_path: str | None = None,
) -> dict[str, Any]:
    return load_pair_explorer(
        PROJECT_ROOT,
        analysis_root=analysis_root,
        disease_targets_path=disease_targets_path,
    )


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except (OSError, ValueError):
        return str(path)


def file_size(path: Path) -> str:
    if not path.is_file():
        return ""
    size = path.stat().st_size
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def mime_type(path: Path) -> str:
    return {
        ".csv": "text/csv",
        ".json": "application/json",
        ".md": "text/markdown",
        ".png": "image/png",
        ".cys": "application/octet-stream",
        ".sha256": "text/plain",
    }.get(path.suffix.casefold(), "application/octet-stream")


def artifact_download(path: Path, label: str, prefix: str) -> None:
    if not path.is_file():
        st.caption(f"待生成：`{relative(path)}`")
        return
    key = hashlib.sha1(f"{prefix}:{path}".encode("utf-8")).hexdigest()
    st.download_button(
        f"下载{label} · {file_size(path)}",
        data=path.read_bytes(),
        file_name=path.name,
        mime=mime_type(path),
        key=key,
        width="stretch",
    )
    st.caption(f"`{relative(path)}`")


def render_evidence_step(
    *,
    title: str,
    status: str,
    summary: str,
    files: list[tuple[str, Path]],
    prefix: str,
) -> None:
    with st.expander(f"{title} · {status}"):
        st.write(summary)
        existing = [(label, path) for label, path in files if path.is_file()]
        missing = [(label, path) for label, path in files if not path.is_file()]
        if existing:
            columns = st.columns(min(3, len(existing)))
            for index, (label, path) in enumerate(existing):
                with columns[index % len(columns)]:
                    artifact_download(path, label, prefix)
        for label, path in missing:
            st.caption(f"{label}待生成：`{relative(path)}`")


def number_value(row: dict[str, Any], key: str, digits: int = 3) -> str:
    value = pd.to_numeric(pd.Series([row.get(key)]), errors="coerce").iloc[0]
    if pd.isna(value):
        return "待生成"
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.{digits}f}"


def dataframe_download(frame: pd.DataFrame, label: str, filename: str, key: str) -> None:
    if frame.empty:
        st.caption(f"{label}：无可下载记录")
        return
    st.download_button(
        label,
        frame.to_csv(index=False).encode("utf-8-sig"),
        file_name=filename,
        mime="text/csv",
        key=key,
        width="stretch",
    )


def selected_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    return frame[[column for column in columns if column in frame.columns]].copy()


st.set_page_config(
    page_title="中药组合协同潜力筛选平台",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

inject_dashboard_theme()
inject_reference_layout_theme()

DEFAULT_TASK_PATH = PROJECT_ROOT / "config" / "tasks" / "uc-24-herbs-v1" / "pipeline.json"
LEGACY_TASK_PATH = PROJECT_ROOT / "config" / "pipeline_uc_20_strict.json"


def discover_tasks() -> dict[str, Path]:
    paths = [DEFAULT_TASK_PATH]
    paths.extend(sorted((PROJECT_ROOT / "config" / "tasks").glob("*/pipeline.json"), reverse=True))
    paths.append(LEGACY_TASK_PATH)
    tasks: dict[str, Path] = {}
    seen: set[Path] = set()
    for path in paths:
        if not path.is_file() or path.resolve() in seen:
            continue
        if not (path.parent / "staged_formula.json").is_file():
            continue
        seen.add(path.resolve())
        try:
            summary = task_summary(load_config(path))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        disease = summary["disease_cn"] or summary["disease_en"] or "未命名疾病"
        task_id = path.parent.name if path.name == "pipeline.json" else path.stem
        label = f"{disease}｜{summary['herb_count']}味｜{task_id}"
        tasks[label] = path
    return tasks


default_task = load_config(DEFAULT_TASK_PATH)
default_summary = task_summary(default_task)
deployment_path = PROJECT_ROOT / "config" / "deployment.json"
deployment = json.loads(deployment_path.read_text(encoding="utf-8")) if deployment_path.is_file() else {}
remote_selection = render_remote_compute_panel(PROJECT_ROOT)
if remote_selection is not None:
    render_staged_formula_dashboard(*remote_selection)
    st.stop()

available_tasks = discover_tasks()
task_paths = [str(path) for path in available_tasks.values()]
task_labels = {str(path): label for label, path in available_tasks.items()}
if st.session_state.get("selected_task_config") not in task_paths:
    st.session_state["selected_task_config"] = str(DEFAULT_TASK_PATH)
selected_task_value = st.sidebar.selectbox(
    "查看任务",
    task_paths,
    format_func=lambda value: task_labels.get(value, Path(value).stem),
    key="selected_task_config",
)
selected_task_path = Path(selected_task_value)
selected_task = load_config(selected_task_path)
selected_summary = task_summary(selected_task)

# Only the screenshot method is an active research workflow. Historical data
# stays on disk for provenance; the old weighted dashboard is not loaded.
staged_rules_path = selected_task_path.parent / "staged_formula.json"
if staged_rules_path.is_file():
    staged_paths = selected_task.get("paths", {})
    staged_analysis_root = PROJECT_ROOT / (
        staged_paths.get("analysis_root") or staged_paths.get("legacy_analysis_root") or ""
    )
    render_staged_formula_dashboard(PROJECT_ROOT, staged_rules_path, staged_analysis_root)
else:
    st.info("本任务尚未配置图片逐级评分规则，不显示旧排名或借用其他任务结果。")
st.stop()

selected_task_id = str(selected_task.get("job", {}).get("id", ""))
staged_rules_path = selected_task_path.parent / "staged_formula.json"
selected_status = (
    load_task_status(PROJECT_ROOT, selected_task_id)
    if selected_task_id and not staged_rules_path.is_file() else {}
)
if selected_status:
    state = str(selected_status.get("status", ""))
    stage = str(selected_status.get("stage", "等待运行"))
    if state in {"completed", "passed"}:
        st.success(f"当前任务已完成：{stage}")
    elif state in {"failed", "needs_input", "blocked"}:
        st.warning(f"当前任务已暂停：{stage}")
        if selected_status.get("error"):
            st.caption(str(selected_status["error"]))
    else:
        st.info(f"当前任务正在后台运行：{stage}。刷新页面可查看最新结果。")
    log_value = selected_status.get("log_path")
    if log_value:
        log_path = PROJECT_ROOT / str(log_value)
        if log_path.is_file():
            with st.expander("查看本次后台运行日志"):
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
                st.code(log_text[-12000:] or "日志刚创建，尚无输出。", language="text")

dashboard = load_dashboard(str(selected_task_path))
scope = dashboard["scope"]
ranking = dashboard["ranking"]
david = dashboard["david"]
david_single = dashboard["david_single"]
pubmed = dashboard["pubmed"]
safety = dashboard["safety"]
quality_control = dashboard["quality_control"]
funnel = dashboard["funnel"]
docking = dashboard.get("docking", {})
strict_root: Path = dashboard["strict_root"]
task_paths = selected_task.get("paths", {})
disease_label = selected_summary["disease_cn"] or selected_summary["disease_en"] or "目标疾病"
total_pair_count = selected_summary["combination_count"]
disease_root = PROJECT_ROOT / task_paths.get("disease_targets_root", "data/formal_inputs/genecards_uc")
disease_targets_path = disease_root / "uc_genecards_targets_all_normalized.csv"
if not disease_targets_path.exists():
    disease_targets_path = disease_root / "uc_genecards_targets_normalized.csv"

if staged_rules_path.is_file():
    render_staged_formula_dashboard(PROJECT_ROOT, staged_rules_path, strict_root)
else:
    st.info("本任务尚未配置图片逐级评分规则。旧算法已停用，不显示旧排名或借用UC结果。")
st.stop()

st.title("中药组合协同潜力筛选平台")
st.caption(
    f"当前任务：{selected_summary['disease_cn'] or selected_summary['disease_en']} · "
    f"{selected_summary['herb_count']}味候选中药 · "
    f"{selected_summary['combination_count']}个两味组合"
)
st.info(
    "本系统输出的是科研用协同潜力候选，不是临床药方。"
    "“1+1>2”必须由后续单药A、单药B和联合AB实验确认。"
)

st.subheader("筛选漏斗")
st.caption(
    "先用硬门槛减少组合，再对留下的药对做证据分析。STRING和DAVID属于分析与重评分，"
    "不会为了让漏斗好看而虚构淘汰数量。"
)
render_screening_funnel(funnel)
if ranking["data"].empty:
    st.markdown(
        '<div class="evidence-callout"><strong>当前真实终点：</strong>'
        "新任务已经建立，但本任务的真实数据库证据尚未跑完；后续步骤显示待生成，"
        "不会借用历史任务结果。</div>",
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<div class="evidence-callout"><strong>当前真实终点：</strong>'
        "页面只展示本任务已经生成的结果；Top3必须完成真实分子对接后才能交给实验人员。</div>",
        unsafe_allow_html=True,
    )

st.subheader("本轮数据范围")
scope_columns = st.columns(4)
scope_columns[0].metric("正式候选药材", scope["candidate_herb_count"] or "待生成")
scope_columns[1].metric("全部两味组合", scope["processed_pairs"] or "待生成")
scope_columns[2].metric("双方均有贡献", scope["eligible_pairs"] or "待生成")
scope_columns[3].metric("疾病交集靶点", scope["intersection_targets"] or "待生成")

if scope["huangqin_excluded"] and selected_task_path == LEGACY_TASK_PATH:
    huangqin = scope["huangqin"]
    st.warning(
        "黄芩已从严格药食同源候选池排除。现行国家食药物质目录未命中黄芩，"
        "它只可单列作非食药物质标杆，不能混进20味正式排名。"
    )
    with st.expander("查看20味清单与黄芩排除依据"):
        candidates = scope["candidates"]
        if not candidates.empty:
            show_columns = [
                column
                for column in ("candidate_order", "herb_name", "official_scope_status", "notes")
                if column in candidates.columns
            ]
            st.dataframe(candidates[show_columns], hide_index=True, width="stretch")
        st.write(
            {
                "黄芩目录资格": huangqin.get("current_catalog_eligible"),
                "研究角色": huangqin.get("research_role"),
                "排除说明": huangqin.get("evidence_note"),
                "官方来源": huangqin.get("official_url"),
            }
        )
elif selected_task_path == DEFAULT_TASK_PATH:
    st.success("本任务不限制药食同源，24味中药全部按用户输入进入候选池；槐花不替换成槐米。")

st.subheader("阶段性候选顺序")
st.warning(
    "当前展示仅为阶段性候选顺序。安全性15分仍待人工复核，配伍证据15分当前自动计分为0；"
    "数据库排序只决定优先验证次序，药效关系仍须实验确认。"
)
ranking_frame = ranking_display(ranking["data"])
if ranking_frame.empty:
    st.info("阶段性排名文件尚未生成。完成来源偏差校正后，这里会自动显示。")
else:
    st.markdown(f"**当前依据：{ranking['stage_label']}**")
    st.dataframe(ranking_frame, hide_index=True, width="stretch")
    st.caption(
        "“已完成两维核心分”只包含靶点互补40%与通路协同30%的已完成部分；"
        "没有把旧的统一安全占位值计入。"
    )
    if Path(ranking["path"]).is_file():
        artifact_download(Path(ranking["path"]), "当前阶段排名CSV", "ranking")

st.subheader("Top3分子对接验证")
docking_matrix = docking.get("matrix", pd.DataFrame())
redocking = docking.get("redocking", {})
if isinstance(docking_matrix, pd.DataFrame) and not docking_matrix.empty:
    docking_columns = st.columns(4)
    docking_columns[0].metric("已完成药对", docking_matrix["pair_rank"].nunique())
    docking_columns[1].metric("实际对接成分", docking_matrix["ingredient_name"].nunique())
    docking_columns[2].metric(
        "回对接RMSD",
        f"{float(redocking.get('symmetry_aware_heavy_atom_rmsd_angstrom', 0)):.4f} Å",
    )
    docking_columns[3].metric(
        "最佳结合能",
        f"{float(pd.to_numeric(docking_matrix['best_affinity_kcal_mol']).min()):.3f} kcal/mol",
    )
    if redocking.get("passed"):
        st.success("5IKR共晶配体回对接通过（RMSD≤2.0 Å），Top3正式Vina对接已完成。")
    display_docking = docking_matrix[[
        column for column in (
            "pair_rank", "pair_key", "herb_name", "ingredient_name",
            "target_gene", "pdb_id", "pubchem_cid", "best_affinity_kcal_mol",
        ) if column in docking_matrix.columns
    ]].rename(columns={
        "pair_rank": "排名", "pair_key": "药对", "herb_name": "中药",
        "ingredient_name": "核心成分", "target_gene": "靶点", "pdb_id": "PDB",
        "pubchem_cid": "PubChem CID", "best_affinity_kcal_mol": "最佳结合能 kcal/mol",
    })
    st.dataframe(display_docking, hide_index=True, width="stretch")
    artifact_download(Path(docking["matrix_path"]), "结合能矩阵", "docking-matrix")
    artifact_download(Path(docking["redocking_path"]), "回对接质控", "docking-redocking")
    st.caption("分子对接只支持潜在结合，不等于已经证明药对协同或临床疗效。")
else:
    st.info("Top3尚未完成真实分子对接；旧版对接记录不会在这里显示。")

st.divider()
st.subheader("药对筛选详情")
st.caption(
    f"这里覆盖本任务全部{total_pair_count}组。每一组都按同一套规则展示通过、停止、"
    "暂未入围或待人工复核的原因。"
)

analysis_value = task_paths.get("analysis_root") or task_paths.get("legacy_analysis_root")
analysis_root = str(PROJECT_ROOT / analysis_value) if analysis_value else None
explorer = load_explorer(analysis_root, str(disease_targets_path))
catalogue: pd.DataFrame = explorer["catalogue"]
if catalogue.empty:
    st.info(f"{total_pair_count}组药对审计表尚未生成，详情区会在全量组合完成后出现。")
else:
    shortlist_count = int(catalogue["_shortlisted"].sum())
    eligible_count = int(catalogue["_eligible"].sum())
    stopped_count = len(catalogue) - eligible_count
    stopped_rows = catalogue[~catalogue["_eligible"]].copy()
    stopped_targets_a = pd.to_numeric(stopped_rows.get("targets_a"), errors="coerce").fillna(0)
    stopped_targets_b = pd.to_numeric(stopped_rows.get("targets_b"), errors="coerce").fillna(0)
    stopped_unique_a = pd.to_numeric(
        stopped_rows.get("unique_targets_a"), errors="coerce"
    ).fillna(0)
    stopped_unique_b = pd.to_numeric(
        stopped_rows.get("unique_targets_b"), errors="coerce"
    ).fillna(0)
    zero_target_count = int(((stopped_targets_a == 0) | (stopped_targets_b == 0)).sum())
    identical_target_count = int(
        (
            (stopped_targets_a > 0)
            & (stopped_targets_b > 0)
            & (stopped_unique_a == 0)
            & (stopped_unique_b == 0)
        ).sum()
    )
    one_side_covered_count = stopped_count - zero_target_count - identical_target_count
    with st.expander(f"查看{stopped_count}组为什么止于第一道药对门槛"):
        stop_columns = st.columns(3)
        stop_columns[0].metric("至少一味无疾病交集", zero_target_count)
        stop_columns[1].metric("一味靶点被完全覆盖", one_side_covered_count)
        stop_columns[2].metric("两味靶点集合相同", identical_target_count)
        st.caption(
            "以上原因完全由targets_a、targets_b、unique_targets_a和unique_targets_b确定，"
            "不是AI根据药名自由解释。"
        )
    filter_labels = [
        f"阶段候选（{shortlist_count}组）",
        f"进入后续分析（{eligible_count}组）",
        f"止于独立贡献门槛（{stopped_count}组）",
        f"全部组合（{len(catalogue)}组）",
    ]
    selected_filter = st.radio(
        "查看范围",
        filter_labels,
        index=0 if shortlist_count else 1,
        horizontal=True,
        key="pair_detail_scope",
    )
    if selected_filter.startswith("阶段候选"):
        filtered_catalogue = catalogue[catalogue["_shortlisted"]]
    elif selected_filter.startswith("进入后续"):
        filtered_catalogue = catalogue[catalogue["_eligible"]]
    elif selected_filter.startswith("止于"):
        filtered_catalogue = catalogue[~catalogue["_eligible"]]
    else:
        filtered_catalogue = catalogue

    pair_rows = {
        str(row["pair_key"]): row for _, row in filtered_catalogue.iterrows()
    }

    def pair_option_label(pair_key: str) -> str:
        row = pair_rows[pair_key]
        state = str(row.get("screening_state", ""))
        rank = pd.to_numeric(pd.Series([row.get("david_refined_rank")]), errors="coerce").iloc[0]
        if not pd.isna(rank):
            return f"第{int(rank)}名｜{pair_key}｜{state}"
        return f"{pair_key}｜{state}"

    selected_pair = st.selectbox(
        "选择一组药对，可输入药名搜索",
        list(pair_rows),
        format_func=pair_option_label,
        key="pair_detail_selection",
    ) if pair_rows else None
    if selected_pair is None:
        st.info("这个范围当前没有药对，请切换到其他范围。")
        st.stop()
    detail = build_pair_detail(explorer, selected_pair)
    row = detail["row"]
    if detail["shortlisted"]:
        st.success(detail["outcome"])
    elif detail["eligible"]:
        st.info(detail["outcome"])
    else:
        st.warning(detail["outcome"])

    if detail["david_complete"]:
        target_score = float(
            pd.to_numeric(
                pd.Series([row.get("target_complementarity_score_adjusted")]),
                errors="coerce",
            ).fillna(0).iloc[0]
        )
        pathway_score = float(
            pd.to_numeric(
                pd.Series([row.get("david_pathway_synergy_score")]),
                errors="coerce",
            ).fillna(0).iloc[0]
        )
        score_rows = pd.DataFrame(
            [
                {
                    "评分维度": "靶点互补",
                    "维度原始分": target_score,
                    "权重": "40%",
                    "当前贡献": round(target_score * 0.40, 6),
                    "状态": "已完成",
                },
                {
                    "评分维度": "DAVID通路协同潜力",
                    "维度原始分": pathway_score,
                    "权重": "30%",
                    "当前贡献": round(pathway_score * 0.30, 6),
                    "状态": "已完成",
                },
                {
                    "评分维度": "安全证据",
                    "维度原始分": pd.NA,
                    "权重": "15%",
                    "当前贡献": pd.NA,
                    "状态": "待人工复核，不使用历史占位60分",
                },
                {
                    "评分维度": "两药合用证据",
                    "维度原始分": pd.NA,
                    "权重": "15%",
                    "当前贡献": pd.NA,
                    "状态": "待全文复核，不自动记0分",
                },
            ]
        )
        with st.expander("查看当前分数是怎样算出来的", expanded=True):
            st.dataframe(score_rows, hide_index=True, width="stretch")
            st.caption(
                f"当前70分制核心分：{number_value(row, 'david_refined_core_score', 6)}。"
                "只有后两项完成审核后，才能形成100分制最终总分。"
            )
            target_parts = [
                ("药对靶点并集广度百分位", "union_breadth_percentile", 0.20),
                ("两药非重叠靶点比例", "nonoverlap_ratio", 0.25),
                ("两侧独有靶点贡献平衡", "unique_contribution_balance", 0.30),
                ("较弱一侧贡献百分位", "weaker_side_contribution_percentile", 0.15),
                ("跨药PPI边密度百分位", "cross_herb_ppi_density_percentile", 0.10),
            ]
            pathway_parts = [
                ("药对显著KEGG数量百分位", "pair_kegg_count_percentile", 0.30),
                ("组合新增KEGG比例百分位", "emergent_ratio_percentile", 0.30),
                ("两侧特异通路保留平衡", "retained_unique_pathway_balance", 0.20),
                ("肠黏膜条目数量百分位", "barrier_term_count_percentile", 0.20),
            ]

            def internal_score_table(parts: list[tuple[str, str, float]]) -> pd.DataFrame:
                records = []
                for label, field, weight in parts:
                    value = pd.to_numeric(
                        pd.Series([row.get(field)]), errors="coerce"
                    ).iloc[0]
                    records.append(
                        {
                            "内部指标": label,
                            "当前值": pd.NA if pd.isna(value) else round(float(value), 6),
                            "内部权重": f"{int(weight * 100)}%",
                            "对维度100分的贡献": (
                                pd.NA
                                if pd.isna(value)
                                else round(float(value) * weight * 100, 6)
                            ),
                        }
                    )
                return pd.DataFrame(records)

            internal_columns = st.columns(2)
            with internal_columns[0]:
                st.markdown("**靶点互补维度内部公式**")
                st.dataframe(
                    internal_score_table(target_parts), hide_index=True, width="stretch"
                )
            with internal_columns[1]:
                st.markdown("**DAVID通路维度内部公式**")
                st.dataframe(
                    internal_score_table(pathway_parts), hide_index=True, width="stretch"
                )

    gate_tab, ppi_tab, david_detail_tab, review_tab, raw_tab = st.tabs(
        ["门槛与靶点", "PPI网络", "DAVID与单药对照", "文献与安全", "原始证明"]
    )

    with gate_tab:
        st.markdown("**逐步筛选轨迹**")
        st.dataframe(pd.DataFrame(detail["trace"]), hide_index=True, width="stretch")

        target_columns = st.columns(6)
        target_columns[0].metric(
            f"{detail['herb_a']}疾病靶点", len(detail["genes_a"])
        )
        target_columns[1].metric(
            f"{detail['herb_b']}疾病靶点", len(detail["genes_b"])
        )
        target_columns[2].metric("共同靶点", len(detail["shared_genes"]))
        target_columns[3].metric(
            f"{detail['herb_a']}独有", len(detail["unique_genes_a"])
        )
        target_columns[4].metric(
            f"{detail['herb_b']}独有", len(detail["unique_genes_b"])
        )
        target_columns[5].metric("组合并集", len(detail["union_genes"]))

        st.markdown("**药材来源与活性成分**")
        source_columns = st.columns(2)
        ingredient_evidence: dict[str, dict[str, Any]] = {}
        for column, herb, audit in (
            (source_columns[0], detail["herb_a"], detail["audit_a"]),
            (source_columns[1], detail["herb_b"], detail["audit_b"]),
        ):
            with column:
                source_label = str(audit.get("data_source", "来源待确认"))
                ingredient = load_active_ingredients(explorer, herb, source_label)
                ingredient_evidence[herb] = ingredient
                st.markdown(f"**{herb}**")
                st.write(
                    {
                        "数据库": source_label,
                        "活性成分数": int(float(audit.get("active_ingredient_count", 0) or 0)),
                        "原始靶点名称数": int(float(audit.get("raw_target_name_count", 0) or 0)),
                        "标准化基因数": int(float(audit.get("mapped_gene_count", 0) or 0)),
                        "筛选条件": ingredient["filter"],
                    }
                )
                if ingredient["data"].empty:
                    st.caption("活性成分明细尚未形成可展示表。")
                else:
                    st.dataframe(
                        ingredient["data"],
                        hide_index=True,
                        width="stretch",
                        height=min(260, 38 * (len(ingredient["data"]) + 1)),
                    )

        with st.expander("查看共同和独有靶点名单"):
            st.markdown(f"**共同靶点（{len(detail['shared_genes'])}）**")
            st.write("、".join(detail["shared_genes"]) or "无")
            st.markdown(
                f"**{detail['herb_a']}独有靶点（{len(detail['unique_genes_a'])}）**"
            )
            st.write("、".join(detail["unique_genes_a"]) or "无")
            st.markdown(
                f"**{detail['herb_b']}独有靶点（{len(detail['unique_genes_b'])}）**"
            )
            st.write("、".join(detail["unique_genes_b"]) or "无")

    with ppi_tab:
        if not detail["eligible"]:
            st.info(
                "该药对没有进入PPI特征重评分和DAVID；下面仍可查看从一次性STRING全局网络"
                "派生出的原始子网，便于复核前置门槛。"
            )
        st.caption(
            f"STRING只对本轮{scope['intersection_targets']}个疾病交集靶点请求一次高置信网络；"
            "本页按当前药对并集筛出子网络。"
            "Cytoscape拓扑值来自128节点全局网络，不冒充药对子网络重新计算值。"
        )
        ppi_columns = st.columns(4)
        ppi_columns[0].metric("组合内PPI边", number_value(row, "ppi_edges_within_union"))
        ppi_columns[1].metric("跨药独有靶点边", number_value(row, "cross_herb_ppi_edges"))
        ppi_columns[2].metric("跨药边密度", number_value(row, "cross_herb_ppi_density", 6))
        ppi_columns[3].metric(
            "跨药边平均分（仅展示）",
            number_value(row, "cross_herb_ppi_mean_score", 4),
        )
        if detail["eligible"]:
            st.caption("现行靶点互补评分使用跨药边密度百分位，不使用平均互作分直接计分。")
        else:
            st.caption("该药对未进入来源偏差校正，跨药边密度不参与后续排名。")

        ppi_edges = selected_columns(
            detail["ppi_edges"],
            [
                "source",
                "target",
                "combined_score",
                "experimental_score",
                "database_score",
                "textmining_score",
            ],
        ).head(30)
        if not ppi_edges.empty:
            st.markdown("**当前药对子网络中得分最高的30条边**")
            st.dataframe(ppi_edges, hide_index=True, width="stretch")

        topology = selected_columns(
            detail["topology"],
            ["name", "Degree", "BetweennessCentrality", "ClosenessCentrality", "herbs"],
        ).head(20)
        if not topology.empty:
            st.markdown("**药对并集靶点在全局PPI网络中的前20个核心节点**")
            st.dataframe(topology, hide_index=True, width="stretch")

        ppi_image = Path(explorer["paths"]["ppi_image"])
        if ppi_image.is_file():
            with st.expander("查看128节点全局PPI网络图"):
                st.image(str(ppi_image), caption="本任务疾病交集靶点全局PPI网络，不是单个药对专属图")

    with david_detail_tab:
        if not detail["david_complete"]:
            reason = (
                "该药对按设计未进入DAVID。"
                if not detail["eligible"]
                else "该药对的DAVID结果尚未完成。"
            )
            st.info(reason)
        else:
            st.caption("显著性门槛固定为 PValue < 0.01 且 Benjamini < 0.05。")
            david_metrics = st.columns(5)
            david_metrics[0].metric(
                "药对显著KEGG", number_value(row, "pair_significant_kegg_count")
            )
            david_metrics[1].metric(
                f"{detail['herb_a']}单药", number_value(row, "single_a_significant_kegg_count")
            )
            david_metrics[2].metric(
                f"{detail['herb_b']}单药", number_value(row, "single_b_significant_kegg_count")
            )
            david_metrics[3].metric(
                "组合新增KEGG", number_value(row, "emergent_pair_kegg_count")
            )
            david_metrics[4].metric(
                "肠黏膜相关条目", number_value(row, "pair_barrier_term_count")
            )

            emergent_terms = [
                term.strip()
                for term in str(row.get("emergent_pair_kegg_terms", "")).split("|")
                if term.strip()
            ]
            if emergent_terms:
                st.markdown("**药对达到显著、而两味单药均未达到显著的KEGG条目**")
                st.write(emergent_terms)
                st.caption("这是富集阈值比较，不等同于实验药效的协同效应量。")

            kegg = selected_columns(
                detail["significant_kegg"],
                ["Term", "Count", "PValue", "Benjamini", "Fold Enrichment"],
            ).head(20)
            if not kegg.empty:
                st.markdown("**显著KEGG前20条**")
                st.dataframe(kegg, hide_index=True, width="stretch")

            barrier = selected_columns(
                detail["barrier_terms"],
                ["Category", "Term", "Count", "PValue", "Benjamini", "Fold Enrichment"],
            ).head(30)
            if not barrier.empty:
                st.markdown("**命中既定肠黏膜关键词的显著条目**")
                st.dataframe(barrier, hide_index=True, width="stretch")

            go = selected_columns(
                detail["significant_go"],
                ["Category", "Term", "Count", "PValue", "Benjamini", "Fold Enrichment"],
            ).head(30)
            if not go.empty:
                with st.expander("查看显著GO前30条"):
                    st.dataframe(go, hide_index=True, width="stretch")

    with review_tab:
        if not detail["eligible"]:
            st.info("该药对已在前置门槛停止，没有继续占用PubMed逐对检索和安全复核资源。")
        else:
            pubmed_row = detail["pubmed"]
            if pubmed_row:
                st.markdown("**PubMed两层检索**")
                pubmed_metrics = st.columns(4)
                pubmed_metrics[0].metric(
                    "严格检索命中", number_value(pubmed_row, "strict_reported_hit_count")
                )
                pubmed_metrics[1].metric(
                    "宽松检索命中", number_value(pubmed_row, "broad_reported_hit_count")
                )
                pubmed_metrics[2].metric(
                    "去重候选记录", number_value(pubmed_row, "retrieved_unique_candidate_count")
                )
                automatic_scoring_forbidden = str(
                    pubmed_row.get("automatic_scoring_forbidden", "")
                ).strip().casefold() in {"true", "1", "yes"}
                pubmed_metrics[3].metric(
                    "自动计分",
                    "禁止" if automatic_scoring_forbidden else "待人工确认",
                )
                st.write(str(pubmed_row.get("interpretation", "等待人工解释")))
                st.warning(
                    "未检出候选不等于证明没有证据；候选文献也必须阅读全文确认确实是这两味药直接合用。"
                )
                with st.expander("查看严格与宽松检索式"):
                    st.markdown("**严格检索式**")
                    st.code(str(pubmed_row.get("strict_query", "")), language=None)
                    st.markdown("**宽松检索式**")
                    st.code(str(pubmed_row.get("broad_query", "")), language=None)
            else:
                st.info("该药对的PubMed检索汇总尚未生成。")

            safety_row = detail["safety"]
            st.markdown("**安全证据门禁**")
            if safety_row:
                safety_view = pd.DataFrame(
                    [
                        {
                            "目录资格": safety_row.get("official_catalog_eligibility"),
                            "当前硬门槛状态": safety_row.get("hard_gate_status"),
                            "仍需核对": safety_row.get("hard_gate_reasons"),
                            "安全分": safety_row.get("official_safety_evidence_score"),
                            "审核人": safety_row.get("reviewer"),
                            "结论状态": safety_row.get("score_finality"),
                        }
                    ]
                )
                st.dataframe(safety_view, hide_index=True, width="stretch")
                st.warning("目录资格只说明可以进入候选池，不代表任意剂量、部位和人群都安全。")
            else:
                st.info("该药对的安全复核模板尚未生成。")

    with raw_tab:
        st.caption(
            "下载的是当前药对对应的数据库记录和原始请求。页面没有让AI补写缺失字段。"
        )
        download_columns = st.columns(3)
        with download_columns[0]:
            dataframe_download(
                pd.DataFrame([detail["row"]]),
                "下载本药对全量评分行",
                f"{selected_pair}_评分与门槛.csv",
                f"pair-row-{selected_pair}",
            )
        with download_columns[1]:
            herb_rows = [item for item in (detail["audit_a"], detail["audit_b"]) if item]
            dataframe_download(
                pd.DataFrame(herb_rows),
                "下载两味药靶点审计",
                f"{selected_pair}_单药靶点审计.csv",
                f"pair-herb-audit-{selected_pair}",
            )
        with download_columns[2]:
            dataframe_download(
                detail["ppi_edges"],
                "下载页面派生PPI子网络",
                f"{selected_pair}_PPI子网络.csv",
                f"pair-ppi-{selected_pair}",
            )

        st.markdown("**两味药原始成分来源**")
        ingredient_columns = st.columns(2)
        for index, herb in enumerate((detail["herb_a"], detail["herb_b"])):
            evidence = ingredient_evidence[herb]
            with ingredient_columns[index]:
                artifact_download(
                    Path(evidence["path"]),
                    f"{herb}成分原始文件",
                    f"ingredient-source-{selected_pair}-{herb}",
                )

        if detail["david_complete"]:
            st.markdown("**本药对DAVID逐次请求证明**")
            david_artifacts = [
                ("请求参数", detail["artifacts"]["david_request"]),
                ("执行报告与SHA-256", detail["artifacts"]["david_report"]),
                ("富集明细", detail["artifacts"]["david_table"]),
                ("API原始响应", detail["artifacts"]["david_api_response"]),
                ("网页表格原始响应", detail["artifacts"]["david_chart_response"]),
            ]
            artifact_columns = st.columns(3)
            for index, (label, path) in enumerate(david_artifacts):
                with artifact_columns[index % 3]:
                    artifact_download(path, label, f"pair-david-{selected_pair}-{index}")
            reuse_path = detail["artifacts"]["david_reuse_attestation"]
            if reuse_path.is_file():
                artifact_download(
                    reuse_path,
                    "相同输入复用证明",
                    f"pair-david-reuse-{selected_pair}",
                )
        else:
            st.info("该药对没有DAVID文件，这是前置门槛停止造成的正常结果，不是文件丢失。")

        st.markdown("**全局数据库证明**")
        global_columns = st.columns(3)
        global_artifacts = [
            (f"GeneCards标准化{disease_label}靶点", explorer["paths"]["gene_cards"]),
            ("STRING原始响应", explorer["paths"]["string_raw"]),
            ("Cytoscape报告", explorer["paths"]["cytoscape_report"]),
            ("Cytoscape会话", explorer["paths"]["cytoscape_session"]),
            ("Cytoscape剔除边记录", explorer["paths"]["cytoscape_dropped_edges"]),
        ]
        for index, (label, path) in enumerate(global_artifacts):
            with global_columns[index % 3]:
                artifact_download(Path(path), label, f"pair-global-{selected_pair}-{index}")

st.subheader("数据库任务与人工检查点")
david_tab, pubmed_tab, safety_tab = st.tabs(["DAVID富集", "PubMed证据", "安全证据"])

with david_tab:
    st.markdown(f"**{david['state_label']}**")
    st.progress(
        david["percent"],
        text=f"药对已完成 {david['completed_pairs']} / {david['requested_pairs']} 组",
    )
    st.progress(
        david_single["percent"],
        text=(
            f"单味基线已完成 {david_single['completed_pairs']} / "
            f"{david_single['requested_pairs']} 味"
        ),
    )
    david_columns = st.columns(5)
    david_columns[0].metric("已完成药对", david["completed_pairs"])
    david_columns[1].metric("单味基线", david_single["completed_pairs"])
    david_columns[2].metric("直接API结果", david["direct_api_results"])
    david_columns[3].metric("相同输入复用", david["cache_reuses"])
    david_columns[4].metric("成功重试记录", david["retry_history_count"])
    if david["failed_pairs"]:
        st.warning(
            f"当前记录到 {david['failed_pairs']} 组失败或待重试。批处理支持断点续跑，"
            "失败组完成前不会把全量DAVID阶段标成完成。"
        )
    elif (
        david["state"] == "completed"
        and david_single["state"] == "completed"
        and ranking["stage"] == "david_refined_interim"
    ):
        st.success(
            f"{david['requested_pairs']}组药对和{david_single['requested_pairs']}味单药基线均已完成，"
            "DAVID通路二次评分已生成。"
        )
    elif david["state"] == "completed":
        st.success("全量药对DAVID富集已完成，正在等待单味基线或通路评分复算。")
    elif david["state"] == "not_started":
        st.info("DAVID输出目录尚不存在，页面会在任务开始后自动显示进度。")
    else:
        st.caption("页面只读取进度文件，不会干预正在运行的DAVID任务。")
    current_david_report = (
        david["report_path"] if Path(david["report_path"]).is_file() else david["progress_path"]
    )
    artifact_download(Path(current_david_report), "DAVID进度或总报告", "david-status")

with pubmed_tab:
    if pubmed["complete"]:
        st.success(f"PubMed全量检索已覆盖{pubmed['pair_count']}组药对。")
    else:
        st.info(f"PubMed全量检索尚未覆盖本任务{scope['eligible_pairs']}组合格药对。")
    pubmed_columns = st.columns(4)
    pubmed_columns[0].metric("已检索药对", pubmed["pair_count"])
    pubmed_columns[1].metric("检索式", pubmed["query_count"])
    pubmed_columns[2].metric("候选记录", pubmed["candidate_records"])
    pubmed_columns[3].metric("自动可计分", pubmed["automatically_score_eligible"])
    st.warning(
        f"检出的 {pubmed['candidate_records']} 篇候选中，"
        f"{pubmed['multiherb_exclusions']} 篇属于多味复方，不能当成两味药直接合用证据。"
        "当前配伍证据自动计分为0。"
    )
    queue_path = pubmed["finalized_root"] / "candidate_manual_full_text_review_queue.csv"
    queue = read_csv(queue_path)
    if not queue.empty:
        queue_columns = [
            column
            for column in (
                "pair_name",
                "pmid",
                "doi",
                "title",
                "evidence_class",
                "human_review_status",
                "review_instruction",
            )
            if column in queue.columns
        ]
        st.dataframe(queue[queue_columns], hide_index=True, width="stretch")
    artifact_download(queue_path, "人工全文复核队列", "pubmed-queue")

with safety_tab:
    if safety["manual_review_required"]:
        st.warning(
            f"{safety['pair_count']}组目录资格已核对，但0组获得最终安全分。实际材料基源和部位、药典禁忌、"
            "拟用剂量、加工方式及批次质量仍需科研人员人工复核。"
        )
    elif safety["covered"]:
        st.success("安全证据复核已覆盖全部合格药对。")
    else:
        st.info("安全证据复核模板尚未生成。")
    safety_columns = st.columns(3)
    safety_columns[0].metric("模板覆盖药对", safety["pair_count"])
    safety_columns[1].metric("已复核安全分", safety["final_numeric_scores"])
    safety_columns[2].metric("状态", "待人工" if safety["manual_review_required"] else "已复核")
    if safety["reason"]:
        st.caption(safety["reason"])
    artifact_download(
        safety["root"] / "pair_safety_review_template.csv",
        "药对安全人工复核表",
        "safety-template",
    )

st.divider()
st.subheader("机器质控")
if quality_control["available"]:
    qc_counts = quality_control["counts"]
    qc_columns = st.columns(4)
    qc_columns[0].metric("通过", qc_counts["passed"])
    qc_columns[1].metric("进行中", qc_counts["pending"])
    qc_columns[2].metric("需人工", qc_counts["warning"])
    qc_columns[3].metric("错误", qc_counts["failed"])
    if qc_counts["failed"]:
        st.error("机器质控发现数据矛盾或证据闭环错误，当前结果不得继续使用。")
    elif qc_counts["pending"] or qc_counts["warning"]:
        st.warning(
            "自动计算链路没有发现数据错误；仍有未完成步骤或人工复核项，"
            "所以页面只称“阶段性协同潜力排名”。"
        )
    else:
        st.success("机器质控全部通过。")
    qc_rows = []
    status_labels = {
        "passed": "通过",
        "pending": "进行中",
        "warning": "需人工",
        "failed": "错误",
    }
    for item in quality_control["checks"]:
        qc_rows.append(
            {
                "检查项": item.get("id"),
                "状态": status_labels.get(item.get("status"), item.get("status")),
                "说明": item.get("message"),
                "证据文件": "；".join(item.get("evidence_paths", [])),
            }
        )
    if qc_rows:
        st.dataframe(pd.DataFrame(qc_rows), hide_index=True, width="stretch")
    artifact_download(
        quality_control["report_path"],
        "机器质控JSON",
        "machine-qc",
    )
else:
    st.info("机器质控报告尚未生成。")

st.divider()
st.subheader("全过程证明")
st.caption("按步骤查看输入、原始响应、参数、剔除记录和复核模板。缺失文件会显示“待生成”，不会导致页面报错。")

render_evidence_step(
    title="1. 疾病与候选中药",
    status=f"{scope['candidate_herb_count']}味候选已确认" if scope["candidate_herb_count"] else "待完善",
    summary=f"本任务研究{disease_label}，候选中药由任务输入确定，不限制药食同源。",
    files=[
        ("候选中药清单", scope["candidate_path"]),
        ("任务配置", selected_task_path),
        ("正式分析总报告", scope["formal_report_path"]),
    ],
    prefix="scope",
)

render_evidence_step(
    title="2. 成分靶点与疾病交集",
    status=f"{scope['intersection_targets']}个交集靶点" if scope["intersection_targets"] else "待生成",
    summary=f"保留药材、成分、靶点来源关系，并用GeneCards正式导出建立{disease_label}疾病靶点集合。",
    files=[
        ("药材靶点审计", strict_root / "herb_target_audit.csv"),
        ("GeneCards标准化靶点", disease_targets_path),
        ("组合公式审计", strict_root / "pair_scores_raw_formula_audit.csv"),
    ],
    prefix="targets",
)

render_evidence_step(
    title="3. STRING与Cytoscape网络",
    status="网络与拓扑文件已生成" if (strict_root / "cytoscape_report.json").is_file() else "待生成",
    summary="STRING 12.0高置信互作进入Cytoscape；错误名称映射边另表剔除，网络会话可再次打开。",
    files=[
        ("STRING原始响应", strict_root / "string_network_raw.json"),
        ("Cytoscape报告", strict_root / "cytoscape_report.json"),
        ("错误映射边", strict_root / "cytoscape_dropped_edges.csv"),
        ("PPI网络图", strict_root / "formal_ppi_network.png"),
        ("Cytoscape会话", strict_root / "formal_ppi_session.cys"),
    ],
    prefix="ppi",
)

render_evidence_step(
    title="4. DAVID组合富集",
    status=(
        f"药对{david['completed_pairs']}/{david['requested_pairs']}组；"
        f"单药{david_single['completed_pairs']}/{david_single['requested_pairs']}味"
    ),
    summary=(
        "每组保存请求、响应、富集表和哈希；完全相同的提交只复用有签名证明的结果。"
        "历史网络错误保留为重试证据，最终成功报告才决定完成状态。"
    ),
    files=[
        ("DAVID进度", david["progress_path"]),
        ("DAVID总报告", david["report_path"]),
        ("UniProt映射报告", david["root"] / "uniprot_mapping_report.json"),
        ("UniProt映射审计", david["root"] / "uniprot_mapping_audit.csv"),
        ("单味基线总报告", david_single["report_path"]),
        ("通路二次评分报告", david["root"] / "david_pathway_scoring_report.json"),
    ],
    prefix="david-proof",
)

render_evidence_step(
    title="5. PubMed配伍证据",
    status=f"{pubmed['pair_count']}组证据已封存" if pubmed["complete"] else "检索中或待封存",
    summary="严格层和宽松层检索式、PMID、DOI及原始响应均保留；多味复方不自动算两味药证据。",
    files=[
        ("检索总报告", pubmed["source_report_path"]),
        ("收尾报告", pubmed["final_report_path"]),
        ("逐对检索汇总", pubmed["finalized_root"] / "pair_pubmed_evidence_summary.csv"),
        ("全文复核队列", pubmed["finalized_root"] / "candidate_manual_full_text_review_queue.csv"),
        ("文件校验值", pubmed["finalized_root"] / "checksums.sha256"),
    ],
    prefix="pubmed-proof",
)

render_evidence_step(
    title="6. 安全证据",
    status="待人工复核" if safety["manual_review_required"] else "已复核",
    summary="目录资格只是入口，不等于任意剂量安全。缺失证据保持为空，不自动记0或给满分。",
    files=[
        ("安全状态报告", safety["report_path"]),
        ("单味身份复核", safety["root"] / "herb_identity_review.csv"),
        ("药对安全复核表", safety["root"] / "pair_safety_review_template.csv"),
        ("15分规则", PROJECT_ROOT / "data" / "formal_inputs" / "official_food_medicine_catalog" / "safety_evidence_rubric_15.json"),
    ],
    prefix="safety-proof",
)

docking_root = strict_root / "docking_top3"
render_evidence_step(
    title="7. Top3分子对接验证",
    status="已完成" if (docking_root / "report.json").is_file() else "待Top10复算后锁定并运行",
    summary=(
        "只对最终Top3选取核心成分和核心靶点，保存PubChem/PDB来源、预处理文件、"
        "Grid Box、Vina日志、结合能矩阵和回对接质控。未生成的结果不会用旧对接记录代替。"
    ),
    files=[
        ("Top3锁定表", docking_root / "top3_candidates.csv"),
        ("结构来源清单", docking_root / "structure_manifest.csv"),
        ("对接参数", docking_root / "docking_parameters.json"),
        ("结合能矩阵", docking_root / "binding_energy_matrix.csv"),
        ("回对接质控", docking_root / "redocking_validation.csv"),
        ("分子对接总报告", docking_root / "report.json"),
    ],
    prefix="docking-proof",
)

pipeline_runs = load_pipeline_manifests()
with st.expander("统一流水线运行记录"):
    if not pipeline_runs:
        st.info("还没有统一流水线运行清单。当前各步骤的独立证明仍可在上方下载。")
    else:
        overview_rows = []
        for run in pipeline_runs:
            disease = run.get("job", {}).get("disease", {})
            steps = run.get("steps", [])
            overview_rows.append(
                {
                    "运行编号": run.get("run_id"),
                    "疾病": disease.get("name_cn") or disease.get("name_en"),
                    "状态": run.get("status"),
                    "结果性质": run.get("results_finality", "interim"),
                    "已通过或复用": sum(
                        step.get("status") in {"passed", "completed", "reused"}
                        for step in steps
                    ),
                    "待人工": sum(step.get("status") == "needs_input" for step in steps),
                    "开始时间": run.get("started_at"),
                }
            )
        st.dataframe(pd.DataFrame(overview_rows), hide_index=True, width="stretch")
        selected_run_id = st.selectbox(
            "查看一次运行",
            [str(run.get("run_id")) for run in pipeline_runs],
        )
        selected_run = next(
            run for run in pipeline_runs if str(run.get("run_id")) == selected_run_id
        )
        step_rows = []
        for step in selected_run.get("steps", []):
            existing_artifacts = [
                artifact for artifact in step.get("artifacts", []) if artifact.get("exists")
            ]
            step_rows.append(
                {
                    "步骤": step.get("name"),
                    "状态": step.get("status"),
                    "用时（秒）": step.get("duration_seconds"),
                    "证明文件数": len(existing_artifacts),
                    "需要处理": step.get("action_required", ""),
                }
            )
        st.dataframe(pd.DataFrame(step_rows), hide_index=True, width="stretch")
        manifest_path = Path(selected_run["_manifest_path"])
        artifact_download(manifest_path, "运行总清单JSON", "run-manifest")
        report_value = selected_run.get("report", "")
        if report_value:
            artifact_download(
                PROJECT_ROOT / str(report_value),
                "全过程证据报告",
                "run-report",
            )
        st.caption("账号、密码、Cookie和验证码不进入运行清单。")

st.divider()
with st.expander("历史原型与旧对接记录（不参与当前任务排名）"):
    st.caption(
        "这里保留旧版数据库、跨GEO对照和早期分子对接结果，方便追溯开发历史。"
        "它们不能覆盖上方当前任务的正式链路。"
    )
    try:
        historical_status = load_database_status()
        history_columns = st.columns(4)
        history_columns[0].metric("旧版可筛药材", historical_status.get("eligible_herbs", 0))
        history_columns[1].metric("旧版成分", historical_status.get("ingredients", 0))
        history_columns[2].metric("旧版靶点", historical_status.get("targets", 0))
        history_columns[3].metric("旧版药对", historical_status.get("pairs", 0))

        historical_pairs = load_top_pairs(int(CONFIG.get("top_n", 10)))
        if historical_pairs.empty:
            st.info("旧版SQLite当前没有可展示排名。")
        else:
            st.markdown("**旧版协同潜力结果**")
            st.dataframe(historical_pairs, hide_index=True, width="stretch")
            st.download_button(
                "下载旧版排名CSV",
                historical_pairs.to_csv(index=False).encode("utf-8-sig"),
                file_name="旧版SQLite_UC药对排名.csv",
                mime="text/csv",
            )

        validation = load_dataset_validation()
        if not validation.empty:
            st.markdown("**旧版跨患者数据集对照**")
            st.dataframe(validation, hide_index=True, width="stretch")

        docking = load_docking_results()
        if not docking.empty:
            st.markdown("**旧版分子对接方法学记录**")
            st.dataframe(docking, hide_index=True, width="stretch")
            docking_validation = load_docking_validation()
            if not docking_validation.empty:
                st.dataframe(docking_validation, hide_index=True, width="stretch")
            st.caption("该对接记录不证明当前任务候选中的药对协同，也不进入当前排名。")
    except Exception as error:  # 页面历史区不能影响正式链路展示
        st.info(f"旧版SQLite历史区暂时无法读取：{type(error).__name__}")

st.caption(CONFIG.get("disclaimer", "结果仅用于科研候选筛选，不构成临床建议。"))
