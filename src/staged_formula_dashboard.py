"""Separate, evidence-only view of the screenshot's staged scoring method.

Never fill this view with historical weighted-score rankings or docking files.
An incomplete definition is displayed as a missing input, not a computed result.
"""
from __future__ import annotations

import json
import hashlib
import html
import math
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.reference_dashboard import _chapter_card
from src.staged_docking_evidence import load_current_docking


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _download(path: Path, label: str, key: str) -> None:
    if path.is_file():
        st.download_button(label, path.read_bytes(), file_name=path.name, key=key)


def _frame(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (OSError, ValueError):
        return pd.DataFrame()


def _display_scores(frame: pd.DataFrame) -> None:
    labels = {
        "rank": "本关排名", "pair_key": "药对", "score_initial": "初筛分",
        "score_ppi": "PPI复筛分", "score_enrichment": "通路精筛分",
        "weighted_common": "共同靶点加权分", "complement": "靶点互补率",
        "n_inter": "共同靶点数", "n_union": "并集靶点数",
        "core_common_count": "共同核心靶点数", "network_gain": "网络增益率",
        "shared_core_pathway_count": "共同核心通路数",
        "exclusive_core_pathway_count": "互补核心通路数",
        "selected": "本关晋级", "cutoff_tie": "边界同分",
    }
    columns = [key for key in labels if key in frame.columns]
    display = frame[columns].rename(columns=labels).copy()
    if "药对" in display:
        display["药对"] = display["药对"].str.replace("__", "＋", regex=False)
    st.dataframe(display, hide_index=True, width="stretch")


def _integrity_errors(project_root: Path, report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    project_root = project_root.resolve()
    checked: dict[Path, str] = {}
    # Check both the outputs and their declared inputs. A changed rule/target/
    # pathway table cannot be displayed alongside an old successful ranking.
    # Only the manifest's file entries are resolved: absolute paths occurring
    # inside immutable historical JSON/CSV evidence remain provenance text.
    for field, label in (("output_files", "产物"), ("source_files", "来源文件")):
        entries = report.get(field)
        if not isinstance(entries, list) or not entries:
            errors.append(f"{label}指纹清单缺失，无法确认排名对应本轮数据。")
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                errors.append(f"{label}指纹记录格式无效")
                continue
            # Prefer the portable path even if the original machine's absolute
            # path is retained as a separate provenance field.
            value = entry.get("relative_path") or entry.get("path")
            expected_hash = entry.get("sha256")
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{label}路径缺失")
                continue
            if (not isinstance(expected_hash, str) or len(expected_hash) != 64
                    or any(character not in "0123456789abcdefABCDEF" for character in expected_hash)):
                errors.append(f"{label}指纹缺失或格式无效：{value}")
                continue
            expected_hash = expected_hash.lower()
            try:
                path = (project_root / value.replace("\\", "/")).resolve()
                if not path.is_relative_to(project_root) or not path.is_file():
                    errors.append(f"{label}不存在或不在项目目录内：{value}")
                    continue
                if path in checked:
                    if checked[path] != expected_hash:
                        errors.append(f"{label}存在冲突指纹：{value}")
                    continue
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                checked[path] = expected_hash
                if digest.hexdigest() != expected_hash:
                    errors.append(f"{label}指纹不一致：{value}")
            except (OSError, ValueError) as exc:
                errors.append(f"{label}无法核验：{value}（{type(exc).__name__}）")
    return errors


def _evidence_chart(section: str, evidence: dict[str, Any]) -> None:
    if section == "ppi":
        graph = evidence.get("pair", {})
        edges = graph.get("edges", [])
        degree: dict[str, int] = {}
        for edge in edges:
            for node in (edge["source"], edge["target"]):
                degree[node] = degree.get(node, 0) + 1
        visible = set(sorted(degree, key=lambda node: (-degree[node], node))[:20])
        dot = ['graph PPI {', 'node [shape=ellipse, style=filled, fillcolor="#e1eeee"];']
        for node in sorted(visible):
            dot.append(f'{json.dumps(node)};')
        for edge in edges:
            if edge["source"] in visible and edge["target"] in visible:
                dot.append(f'{json.dumps(edge["source"])} -- {json.dumps(edge["target"])};')
        dot.append('}')
        if visible:
            st.graphviz_chart('\n'.join(dot))
            st.caption(f"图中只显示节点度前{len(visible)}个蛋白，方便阅读；评分仍使用该药对全部{len(edges)}条真实边，不按这张局部图重算。")
        else:
            st.caption("没有可绘制的PPI边，不生成示意互作。")
    else:
        paths = evidence.get("pair", {}).get("core_pathway_p_values", {})
        points = [{"通路": key.split('|')[-1], "−log10(P)": -math.log10(value)}
                  for key, value in paths.items() if isinstance(value, (int, float)) and 0 < value < 0.01]
        if points:
            st.bar_chart(pd.DataFrame(points).set_index("通路"))
            st.caption("柱高是该组合核心通路的富集显著性；图仅作证据，不额外加入原图评分公式。")


def _render_docking(output: Path, docking: dict[str, Any], errors: list[str]) -> None:
    if errors:
        st.error("本轮对接证据未通过一致性核对，暂不显示完成状态。")
        for error in errors:
            st.write(error)
        return
    if docking.get("status") != "completed":
        st.info("本轮Top3已锁定，对接证据尚未完成，不能用旧排名的药对代替。")
        if docking.get("pairs"):
            st.dataframe(pd.DataFrame([{"药对": row.get("pair_key", "").replace("__", "＋"), "状态": row.get("status", ""), "需要处理": row.get("reason", "")} for row in docking["pairs"]]), hide_index=True, width="stretch")
            _download(output / "docking_top3" / "report.json", "下载对接进度与失败证据", "staged-dock-pending")
        return
    root = output / "docking_top3"
    unique_count = docking["unique_chemical_task_count"]
    compound_names = {item["ingredient_name"] for pair in docking["pairs"] for item in pair["assignments"]}
    shared = all(pair["same_compound_shared_by_both_herbs"] for pair in docking["pairs"])
    shared_note = ""
    if shared and len(compound_names) == 1:
        compound = next(iter(compound_names))
        compound_label = "槲皮素" if compound == "quercetin" else compound
        shared_note = f"本轮两味药各自选中的成分均为{compound_label}，共享对接证据；"
    st.warning(
        f"这{len(docking['pairs'])}组药对共涉及{unique_count}个独立的成分—靶点对接任务。"
        f"{shared_note}不能据此确定药对的协同效果，也不能证明1+1>2。"
    )
    rows = []
    assignments = []
    for pair in docking["pairs"]:
        selected = pair["assignments"]
        rows.append({
            "排名": pair["pair_rank"], "候选药对": pair["pair_key"].replace("__", "＋"),
            "通路精筛分": pair["score_enrichment"], "靶点": pair["target_gene"],
            "蛋白结构": pair["pdb_id"],
            "对接成分": " / ".join(sorted({item["ingredient_name"] for item in selected})),
            "最佳结合能（kcal/mol）": min(item["best_affinity_kcal_mol"] for item in selected),
            "≤−7的任务数/独立任务数": f"{pair['strong_binding_count']}/{pair['strong_binding_denominator']}",
        })
        for item in selected:
            assignments.append({
                "药对": pair["pair_key"].replace("__", "＋"), "来源药材": item["herb_name"],
                "成分": item["ingredient_name"], "成分编号": item["ingredient_id"],
                "PubChem CID": item["pubchem_cid"], "共同核心靶点命中数": item.get("core_hit_count", len(item.get("core_hits", []))),
                "OB": item.get("ob"), "DL": item.get("dl"),
            })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if len(rows) > 1 and rows[0]["通路精筛分"] == rows[1]["通路精筛分"]:
        st.caption("第1、2名评分相同，名称排序不表示疗效差异。")
    st.caption("结合能阈值只是本项目的计算判据，不是有效治疗或药对协同的判据。")
    qc = docking["redocking"]
    for target_qc in qc.get("targets", [qc]):
        st.success(
            f"{target_qc.get('target_gene', '')}/{target_qc.get('pdb_id', '')}参考配体回对接质控通过："
            f"固定受体坐标下RMSD {target_qc['fixed_receptor_heavy_atom_rmsd_angstrom']:.4f} Å"
            f"（阈值≤{target_qc['threshold_angstrom']:.1f} Å），未对配体再做叠合。"
        )
    st.caption(
        f"本轮复用{docking['reused_chemical_task_count']}个化学任务，新计算{docking['new_chemical_task_count']}个；"
        "已核对受体、配体、网格和计算参数一致。评分完成与对接核对是分别留档的两个阶段。"
    )
    with st.expander("查看药材—成分归属及选择依据"):
        st.dataframe(pd.DataFrame(assignments), hide_index=True, width="stretch")
        st.write("按记录的共同核心靶点覆盖和成分选择规则选取；一个化合物归属于多味药时，不重复算成多次独立对接。具体靶点与结构尝试顺序见原始记录。")
        _download(root / "ingredient_candidate_evidence.json", "下载完整成分候选与来源", "staged-dock-ingredients")
    with st.expander("查看回对接质控、参数和尝试记录"):
        st.json(qc, expanded=False)
        st.json(docking["parameters"], expanded=False)
        for name, label in (
            ("redocking_validation.json", "回对接质控与原子映射"),
            ("docking_parameters.json", "对接参数"),
            ("docking_attempt_audit.csv", "计算与复用记录"),
            ("run_attempt_history.json", "执行尝试及失败记录"),
        ):
            _download(root / name, f"下载{label}", f"staged-dock-{name}")
    with st.expander("查看并下载对接结构与原始日志"):
        files = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix in {".pdb", ".pdbqt", ".sdf", ".log", ".cif", ".txt"})
        if files:
            selected_file = st.selectbox("选择结构或日志", files, format_func=lambda path: path.relative_to(root).as_posix(), key="staged-dock-file")
            _download(selected_file, "下载所选原始文件", "staged-dock-raw")
    _download(root / "report.json", "下载本轮Top3对接总报告", "staged-dock-report")
    _download(root / "binding_energy_matrix.csv", "下载结合能及药材归属表", "staged-dock-matrix")


def render_staged_formula_dashboard(
    project_root: Path, rules_path: Path, analysis_root: Path
) -> None:
    output = analysis_root / "image_formula_v1"
    report = _json(output / "readiness_report.json")
    rules = _json(rules_path)
    task_config = _json(rules_path.parent / "pipeline.json")
    disease = task_config.get("job", {}).get("disease", {})
    disease_label = disease.get("name_cn") or disease.get("name_en") or rules.get("disease") or "溃疡性结肠炎"
    herbs = _frame(analysis_root / "herb_target_audit.csv")
    herb_count = len(herbs)
    pair_count = herb_count * (herb_count - 1) // 2
    st.markdown('''
        <nav class="portal-nav" aria-label="章节导航">
          <div class="portal-brand">中药药对证据看板</div>
          <div class="portal-links">
            <a href="#screening-overview">筛选总览</a><a href="#chapter-01">数据基础</a>
            <a href="#chapter-03">药对筛选</a><a href="#chapter-04">PPI证据</a>
            <a href="#chapter-05">通路证据</a><a href="#chapter-06">Top10</a>
            <a href="#chapter-07">分子对接</a>
          </div>
        </nav>''', unsafe_allow_html=True)
    st.markdown(f'''
        <section class="portal-hero">
          <div class="hero-kicker">本次任务 · {html.escape(str(disease_label))} · 两味药组合</div>
          <h1>从原始数据到Top10，每一步都能点开看证据</h1>
          <p>{herb_count}味候选中药 · {pair_count}组两味药对 · 靶点、PPI、通路和分子对接逐步筛选</p>
          <div class="hero-actions"><a href="#screening-overview">查看完整筛选过程</a>
            <a class="secondary" href="#chapter-06">直接查看Top10</a></div>
        </section>''', unsafe_allow_html=True)
    status = str(report.get("status", "not_checked"))
    integrity_errors = _integrity_errors(project_root, report) if status == "scored" else []
    if integrity_errors:
        status = "blocked"
        report = {**report, "blockers": list(report.get("blockers", [])) + integrity_errors}
    docking, docking_errors = load_current_docking(project_root, output) if status == "scored" else ({}, [])
    docking_complete = docking.get("status") == "completed" and not docking_errors
    if status == "scored":
        st.success("新公式评分已生成；本轮Top3对接证据已核对。" if docking_complete else "新公式评分已生成；分子对接须另行核对本轮候选和计算证据。")
    elif status == "ready":
        st.info("输入检查已通过，尚未生成新排名。")
    elif status == "blocked":
        st.warning("尚未生成新排名：请先补齐下面的科学定义或证据。")
    else:
        st.info("新公式已独立实现，尚未完成本任务的输入检查。")
    blockers = report.get("blockers", [])
    for blocker in blockers:
        if isinstance(blocker, dict):
            text = blocker.get("message") or blocker.get("detail") or str(blocker)
        else:
            text = str(blocker)
        st.write(f"• {text}")

    counts = report.get("stage_counts", [])
    st.markdown('<div id="screening-overview" class="chapter-anchor"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-label">筛选漏斗 · 点击章节进入对应证据</div>', unsafe_allow_html=True)
    if status == "scored" and len(counts) == 5:
        labels = ["组两味药对", "组通过靶点初筛", "组通过PPI复筛", "组进入Top10", "组锁定对接候选"]
        tiles = ''.join(f'<div class="metric-tile"><div class="metric-number">{int(value)}</div><div class="metric-text">{label}</div></div>' for value, label in zip(counts, labels))
        st.markdown(f'<div class="metric-strip">{tiles}</div>', unsafe_allow_html=True)
    else:
        st.caption("目标关卡：全部药对 → Top30 → Top15 → Top10 → Top3；未完成的关卡不填写虚构数量。")
    st.markdown('''<div class="chapter-nav">
        <a href="#chapter-01">01 数据准备</a><a href="#chapter-02">02 疾病靶点集</a>
        <a href="#chapter-03">03 药对初筛</a><a href="#chapter-04">04 PPI验证</a>
        <a href="#chapter-05">05 DAVID通路</a><a href="#chapter-06">06 Top10结果</a>
        <a href="#chapter-07">07 Top3对接</a></div>''', unsafe_allow_html=True)
    if _chapter_card(index=1, label="数据准备", title=f"把{herb_count}味中药整理成可计算的成分和靶点数据",
                     action="沿用真实药材、活性成分和标准化靶点来源，保留每味药的对应关系。",
                     result=f"本轮读取{herb_count}味药材，枚举{pair_count}组药对。", hint="药材清单、成分数量、疾病交集靶点和来源文件。"):
        if not herbs.empty:
            columns = [key for key in ("herb_name", "data_source", "active_ingredient_count", "disease_target_count", "disease_gene_symbols") if key in herbs]
            st.dataframe(herbs[columns], hide_index=True, width="stretch")
        _download(analysis_root / "herb_target_audit.csv", "下载药材与疾病交集靶点", "staged-herb-input")
        st.caption("成分与靶点保留真实来源及OB/DL筛选记录；快照复用不等于重新联网采集，不添加未执行的筛选项目。")

    if _chapter_card(index=2, label="疾病靶点与核心通路", title="确定哪些疾病靶点和通路参与加分",
                     action="疾病关联分数分档，核心、重要、普通靶点分别按5、2、1计权；通路清单预先固定。",
                     result="使用本轮分级表和明确KEGG通路清单；未定义项不自动补分。",
                     hint="完整靶点分级、分档阈值、核心通路编号和选择依据。"):
        st.write("每个疾病相关靶点必须明确标为核心、重要或普通，并记录分级来源；不得把未分类靶点自动算成普通。")
        st.write("疾病核心通路使用明确的数据库类别和通路编号。屏障关键词、富集显著性不自动等同于疾病核心通路。")
        for field, label in (
            ("target_grades_csv", "下载本轮靶点分级表"),
            ("core_pathways_csv", "下载本轮核心通路清单"),
        ):
            value = rules.get(field)
            if isinstance(value, str) and value:
                path = (project_root / value).resolve()
                if path.is_relative_to(project_root.resolve()):
                    frame = _frame(path)
                    if not frame.empty:
                        st.dataframe(frame, hide_index=True, width="stretch")
                    _download(path, label, f"staged-{field}")
        policy = _json(output / "definitions" / "operational_policy.json")
        if policy:
            if "gene_grades" in policy:
                st.write("使用本任务记录的疾病靶点分级规则和预先定义的核心通路，不将其他疾病的通路直接套用。")
                st.json(policy, expanded=False)
            else:
                st.write("默认分级使用511个已选疾病基因的GeneCards分数：最高10%为核心，随后20%为重要，其余为普通；同分同级。10条KEGG通路按UC屏障、炎症与修复范围预先选定。")
            st.caption("这是本项目的操作性定义，不是GeneCards或KEGG官方认定的‘核心’名单；规则不随药对排名调整。")
            _download(output / "definitions" / "operational_policy.json", "下载分级与通路选择依据", "staged-operational-policy")
        if status != "scored":
            st.caption("未完成检查前，下方Top30/15/10是关卡目标，不是已算出的数量。")

    definitions = [
        (
            "第一关：靶点初筛，保留Top30",
            "初筛分 = 加权共同靶点分 × (1 + 0.5 × 靶点互补率)",
            "共同靶点按核心×5、重要×2、普通×1求和；互补率=(并集数−交集数)/两味药中较少的靶点数。新方法不沿用旧版‘双方必须有独有靶点’门槛。",
            ["stage1_all.csv", "stage1_top30.csv"],
        ),
        (
            "第二关：PPI复筛，Top30中保留Top15",
            "复筛分 = 初筛分 × (1 + 0.8 × 网络增益率) + 核心共同靶点数 × 10",
            "STRING阈值0.4；网络增益率=药对网络平均边分/两味单药网络中较高的平均边分−1。相同网络快照中提取单药和药对子图，不补不存在的边，分母缺失或为0时明确停止该项计算。",
            ["stage2_all30.csv", "stage2_top15.csv"],
        ),
        (
            "第三关：通路精筛，Top15中保留Top10",
            "精筛分 = 复筛分 × (1 + 0.3 × 共同核心通路数) × (1 + 0.2 × 互补核心通路数)",
            "按原图P<0.01判显著；共同通路是两味单药均显著的疾病核心通路，互补通路是恰好一味显著的疾病核心通路。组合核心通路的平均−log10(P)保留为证据，不额外乘进原图公式。",
            ["stage3_all15.csv", "stage3_top10.csv"],
        ),
        (
            "第四关：Top3分子对接证据",
            "按独立成分—靶点任务统计结合能≤−7 kcal/mol的占比，共享成分不重复计数",
            "只有成分、靶点、结构、网格及计算参数都匹配时，才能复用旧对接。按图统计结合能≤−7 kcal/mol的占比，未达到不能标记通过，也不能据此宣称已证明1+1>2。",
            ["top3_candidates.csv"],
        ),
    ]
    for stage_index, (title, formula, explanation, names) in enumerate(definitions):
        index = [3, 4, 5, 7][stage_index]
        if index == 7:
            st.markdown('<div id="chapter-06"></div>', unsafe_allow_html=True)
            st.subheader("06 · Top10协同潜力排名")
            top10 = _frame(output / "stage3_top10.csv") if status == "scored" else pd.DataFrame()
            if not top10.empty:
                _display_scores(top10)
                st.caption("只按新公式排序；高分表示这套规则下的优先研究顺序，不是疗效大小。")
            else:
                st.info("本轮Top10尚未生成。")
        stage_result = "尚未生成本关结果"
        if status == "scored" and len(counts) == 5:
            stage_result = (
                f"{counts[stage_index]}组进入，{counts[stage_index + 1]}组晋级。"
                if stage_index < 3 else f"已锁定{counts[4]}组候选；分子对接需单独核对，不代表已经通过。"
            )
            if stage_index == 3 and docking_complete:
                stage_result = (
                    f"{docking['completed_pair_count']}组候选的对接证据已核对；"
                    f"共{docking['unique_chemical_task_count']}个独立化学任务，不代表药对协同已验证。"
                )
        expanded = _chapter_card(
            index=index, label=["药对初筛", "PPI网络证据", "DAVID通路证据", "Top3分子对接"][stage_index],
            title=title, action=explanation,
            result=stage_result, hint="计算代入值、晋级与淘汰名单、原始证据及下载。",
        )
        if expanded:
            st.code(formula, language="text")
            st.write(explanation)
            if status == "scored":
                found = False
                for name in names:
                    path = output / name
                    if path.is_file():
                        found = True
                        try:
                            frame = pd.read_csv(path)
                            _display_scores(frame)
                        except (OSError, ValueError):
                            st.error(f"无法读取本轮证据：{name}")
                        _download(path, f"下载{name}", f"staged-stage-{index}-{name}")
                if not found:
                    st.caption("本关可展示文件尚未生成，不能以历史结果代替。")
                if stage_index == 2:
                    stage_rows = _frame(output / "stage3_all15.csv")
                    shared = stage_rows.get("shared_core_pathway_count", pd.Series(dtype=float))
                    exclusive = stage_rows.get("exclusive_core_pathway_count", pd.Series(dtype=float))
                    if not shared.empty and shared.nunique() == 1 and exclusive.nunique() == 1:
                        st.info(f"本轮{len(stage_rows)}组均命中{int(shared.iloc[0])}条共同核心通路、{int(exclusive.iloc[0])}条互补核心通路，本关乘数相同，没有进一步拉开排名。这是原始富集结果，不人为补造差异。")
                if stage_index == 3:
                    st.caption("上方候选名单记录评分阶段的状态；后续对接是否完成，以本轮对接总报告及下方核对结果为准。")
                    _render_docking(output, docking, docking_errors)
            else:
                st.caption("尚未生成本关的新公式结果。")
            if stage_index in (1, 2) and status == "scored":
                choices = sorted((output / "evidence").glob("*.json"))
                if choices:
                    selected = st.selectbox("选择药对查看本关原始证据", choices,
                                            format_func=lambda path: path.stem.replace("__", "＋"),
                                            key=f"staged-pair-evidence-{index}")
                    evidence = _json(selected)
                    section = "ppi" if stage_index == 1 else "enrichment"
                    if section in evidence:
                        _evidence_chart(section, evidence[section])
                        st.json(evidence[section], expanded=False)
                        _download(selected, "下载该药对逐步证据", f"staged-pair-json-{index}")
                        if section == "enrichment":
                            for part, label in (("herb_a", "单药A富集原表"), ("herb_b", "单药B富集原表"), ("pair", "组合富集原表")):
                                table_name = evidence[section].get(part, {}).get("source_table", "")
                                table_path = (project_root / table_name).resolve()
                                if table_path.is_relative_to(project_root.resolve()):
                                    _download(table_path, f"下载{label}", f"staged-raw-david-{part}")
                        else:
                            for field, label in (("ppi_edges_csv", "STRING全量边表"), ("ppi_metadata_json", "STRING请求与映射依据")):
                                path = (project_root / str(rules.get(field, ""))).resolve()
                                if path.is_relative_to(project_root.resolve()):
                                    _download(path, f"下载{label}", f"staged-{field}")
                    else:
                        st.caption("该药对未进入本关，没有本关计算结果。")
    with st.expander("查看输入检查、计算规则和数据指纹"):
        if report:
            st.json(report, expanded=False)
        _download(output / "readiness_report.json", "下载输入检查报告", "staged-readiness")
        _download(rules_path, "下载本轮评分规则", "staged-rules")
        _download(project_root / "src" / "staged_formula.py", "下载评分计算代码", "staged-scoring-source")
        if status == "scored":
            _download(output / "funnel_result.json", "下载逐关计算证据", "staged-funnel")
        sensitivity = _json(output / "sensitivity_initial" / "report.json")
        if sensitivity:
            st.write("已检查三组靶点分档阈值对初筛的影响。此检查只覆盖Top30初筛，不代表最终排名或实验结论稳定。")
            st.json(sensitivity, expanded=False)
            _download(output / "sensitivity_initial" / "report.json", "下载初筛敏感性报告", "staged-sensitivity")
    st.caption("同分按规范药对名称确定顺序，记录截断点同分，不凑数；所有排名仅代表待实验的协同潜力。")
