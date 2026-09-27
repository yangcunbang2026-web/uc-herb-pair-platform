from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except (OSError, UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def read_delimited(path: Path) -> pd.DataFrame:
    """Read a saved evidence table without raising into the dashboard."""
    if not path.is_file():
        return pd.DataFrame()
    separator = "\t" if path.suffix.casefold() in {".tsv", ".txt"} else ","
    try:
        return pd.read_csv(path, sep=separator)
    except (OSError, UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def value_is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return False
    return str(value).strip().casefold() in {"true", "1", "yes", "是", "通过"}


def split_symbols(value: Any) -> list[str]:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return []
    return sorted({item.strip() for item in str(value).split(";") if item.strip()})


def newest(paths: list[Path]) -> Path | None:
    existing = [path for path in paths if path.is_file()]
    return max(existing, key=lambda path: path.stat().st_mtime) if existing else None


def load_scope(
    project_root: Path,
    strict_root: Path,
    candidate_path: Path | None = None,
    identity_path: Path | None = None,
) -> dict[str, Any]:
    formal_report_path = strict_root / "report.json"
    formal_report = read_json(formal_report_path)
    candidate_path = candidate_path or project_root / "config" / "uc_candidate_herbs_20_strict.csv"
    identity_path = identity_path or project_root / "config" / "official_food_medicine_identity_21.csv"
    candidates = read_csv(candidate_path)
    identity = read_csv(identity_path)
    huangqin: dict[str, Any] = {}
    if not identity.empty and {"input_name"}.issubset(identity.columns):
        rows = identity[identity["input_name"].astype(str).eq("黄芩")]
        if not rows.empty:
            huangqin = rows.iloc[0].to_dict()
    herb_count = int(formal_report.get("candidate_herbs_in_this_run", 0))
    if not herb_count and not candidates.empty:
        herb_count = len(candidates)
    return {
        "formal_report": formal_report,
        "formal_report_path": formal_report_path,
        "candidate_path": candidate_path,
        "identity_path": identity_path,
        "candidates": candidates,
        "input_candidate_count": len(identity) if not identity.empty else herb_count,
        "candidate_herb_count": herb_count,
        "processed_pairs": int(formal_report.get("processed_pairs", 0)),
        "eligible_pairs": int(
            formal_report.get("eligible_pairs_after_both_contribute_gate", 0)
        ),
        "intersection_targets": int(
            formal_report.get("unique_drug_disease_intersection_targets", 0)
        ),
        "huangqin": huangqin,
        "huangqin_excluded": bool(huangqin)
        and str(huangqin.get("current_catalog_eligible", "")).strip().casefold()
        != "true",
    }


def build_screening_funnel(
    scope: dict[str, Any],
    ranking: dict[str, Any],
    david: dict[str, Any],
) -> list[dict[str, Any]]:
    """Describe the real gates without pretending every analysis step removes pairs."""
    input_herbs = int(scope.get("input_candidate_count", 0) or 0)
    formal_herbs = int(scope.get("candidate_herb_count", 0) or 0)
    generated_pairs = int(scope.get("processed_pairs", 0) or 0)
    eligible_pairs = int(scope.get("eligible_pairs", 0) or 0)
    completed_david = int(david.get("completed_pairs", 0) or 0)
    ranking_frame = ranking.get("data")
    shortlist_count = len(ranking_frame) if isinstance(ranking_frame, pd.DataFrame) else 0
    tie_note = "含截止分并列" if ranking.get("includes_cutoff_ties") else "按当前截止位"
    return [
        {
            "name": "药材范围",
            "value": f"{input_herbs} → {formal_herbs}味" if input_herbs else "待生成",
            "note": (
                f"排除{max(input_herbs - formal_herbs, 0)}味范围不符项"
                if input_herbs
                else "等待候选身份表"
            ),
            "state": "done" if formal_herbs else "pending",
        },
        {
            "name": "两味组合",
            "value": f"{formal_herbs}味 → {generated_pairs}组" if generated_pairs else "待生成",
            "note": "枚举组合，此步不作淘汰",
            "state": "done" if generated_pairs else "pending",
        },
        {
            "name": "独立贡献门槛",
            "value": f"{generated_pairs} → {eligible_pairs}组" if generated_pairs else "待生成",
            "note": (
                f"{max(generated_pairs - eligible_pairs, 0)}组止于本关"
                if generated_pairs
                else "等待靶点交集"
            ),
            "state": "done" if eligible_pairs else "pending",
        },
        {
            "name": "PPI重评分与DAVID",
            "value": f"{completed_david} / {eligible_pairs}组" if eligible_pairs else "待生成",
            "note": f"STRING全局网络一次构建，{eligible_pairs}组进入重评分",
            "state": "done" if eligible_pairs and completed_david >= eligible_pairs else "active",
        },
        {
            "name": "阶段候选",
            "value": f"{eligible_pairs} → {shortlist_count}组" if shortlist_count else "待生成",
            "note": f"Top10，{tie_note}" if shortlist_count else "等待阶段排名",
            "state": "done" if shortlist_count else "pending",
        },
        {
            "name": "正式分子对接",
            "value": "待锁定少量候选",
            "note": "安全与配伍证据复核后再进入",
            "state": "pending",
        },
    ]


def load_ranking(strict_root: Path) -> dict[str, Any]:
    # 科研展示优先保留截止分并列项；固定10行文件仅为兼容旧消费者。
    tied_candidates = list(
        strict_root.rglob("top10_david_refined_with_cutoff_ties.csv")
    )
    refined_candidates = list(strict_root.rglob("top10_david_refined.csv"))
    refined_path = newest(tied_candidates) or newest(refined_candidates)
    if refined_path is not None:
        frame = read_csv(refined_path)
        scoring_report_path = refined_path.parent / "david_pathway_scoring_report.json"
        scoring_report = read_json(scoring_report_path)
        if not frame.empty:
            includes_cutoff_ties = (
                refined_path.name == "top10_david_refined_with_cutoff_ties.csv"
            )
            return {
                "stage": "david_refined_interim",
                "stage_label": (
                    "DAVID全量复算后的阶段性候选顺序（保留截止分并列项）"
                    if includes_cutoff_ties
                    else "DAVID全量复算后的阶段性候选顺序"
                ),
                "data": frame,
                "path": refined_path,
                "report": scoring_report,
                "report_path": scoring_report_path,
                "is_final": bool(scoring_report.get("final_ranking_available", False)),
                "includes_cutoff_ties": includes_cutoff_ties,
            }

    source_bias_path = strict_root / "top10_source_bias_adjusted_interim.csv"
    source_bias = read_csv(source_bias_path)
    return {
        "stage": "source_bias_adjusted_pre_david",
        "stage_label": "来源偏差校正后的阶段性候选顺序（DAVID复算前）",
        "data": source_bias,
        "path": source_bias_path,
        "report": read_json(strict_root / "source_bias_adjustment_report.json"),
        "report_path": strict_root / "source_bias_adjustment_report.json",
        "is_final": False,
    }


def load_david_batch(root: Path, expected_pairs: int, item_label: str) -> dict[str, Any]:
    progress_path = root / "progress.json"
    report_path = root / "report.json"
    progress = read_json(progress_path)
    final_report = read_json(report_path)
    succeeded = 0
    failed = 0
    rank_directories = 0
    direct_api_results = 0
    cache_reuses = 0
    retry_history_count = 0
    if root.is_dir():
        for pair_dir in root.glob("rank_[0-9][0-9][0-9]"):
            if not pair_dir.is_dir():
                continue
            rank_directories += 1
            pair_report = read_json(pair_dir / "report.json")
            if pair_report.get("status") == "succeeded":
                succeeded += 1
                if pair_report.get("reused_identical_submission"):
                    cache_reuses += 1
                else:
                    direct_api_results += 1
            elif (pair_dir / "error.json").is_file():
                failed += 1
            if (pair_dir / "error.json").is_file():
                retry_history_count += 1

    requested = int(
        final_report.get(
            "requested_pair_count",
            progress.get("requested_pair_count", expected_pairs),
        )
        or expected_pairs
    )
    completed = max(
        succeeded,
        int(final_report.get("completed_pair_count", 0) or 0),
        int(progress.get("completed_pair_count", 0) or 0),
    )
    failed = max(
        failed,
        int(final_report.get("failed_pair_count", 0) or 0),
        int(progress.get("failed_pair_count", 0) or 0),
    )
    analysis_status = str(final_report.get("analysis_status", ""))
    if analysis_status == "completed" and completed >= requested:
        state = "completed"
        state_label = f"DAVID全部{item_label}富集已完成"
    elif completed >= requested and failed == 0:
        state = "finalizing"
        state_label = f"{requested}个{item_label}已完成，正在生成总报告"
    elif root.is_dir() and (progress or rank_directories):
        state = "in_progress_or_resumable"
        state_label = "DAVID正在运行或可断点续跑"
    else:
        state = "not_started"
        state_label = "DAVID尚未开始"
    return {
        "root": root,
        "progress_path": progress_path,
        "report_path": report_path,
        "progress": progress,
        "report": final_report,
        "state": state,
        "state_label": state_label,
        "requested_pairs": requested,
        "completed_pairs": min(completed, requested),
        "failed_pairs": failed,
        "rank_directories": rank_directories,
        "percent": min(completed / requested, 1.0) if requested else 0.0,
        "direct_api_results": direct_api_results
        if rank_directories
        else int(progress.get("api_hits_this_run", 0) or 0),
        "api_hits_this_run": direct_api_results
        if rank_directories
        else int(progress.get("api_hits_this_run", 0) or 0),
        "cache_reuses": cache_reuses
        if rank_directories
        else int(progress.get("identical_submission_cache_reuses", 0) or 0),
        "retry_history_count": retry_history_count,
        "updated_at": progress.get("updated_at") or final_report.get("generated_at"),
    }


def load_david(strict_root: Path, expected_pairs: int) -> dict[str, Any]:
    return load_david_batch(
        strict_root / "david_all_pairs_complete_uniprot",
        expected_pairs,
        "药对",
    )


def load_david_single_baselines(strict_root: Path, expected_count: int = 19) -> dict[str, Any]:
    return load_david_batch(
        strict_root / "david_single_herb_baselines_all_complete_uniprot",
        expected_count,
        "单味基线",
    )


def load_quality_control(strict_root: Path) -> dict[str, Any]:
    report_path = strict_root / "pipeline_qc_report.json"
    report = read_json(report_path)
    checks = report.get("checks", []) if isinstance(report.get("checks"), list) else []
    counts = report.get("status_counts", {}) if isinstance(report.get("status_counts"), dict) else {}
    if not counts and checks:
        counts = {
            status: sum(item.get("status") == status for item in checks)
            for status in ("passed", "pending", "warning", "failed")
        }
    return {
        "report_path": report_path,
        "report": report,
        "available": bool(report),
        "core_status": str(report.get("core_pipeline_status", "not_available")),
        "final_status": str(report.get("final_result_status", "not_available")),
        "counts": {status: int(counts.get(status, 0) or 0) for status in ("passed", "pending", "warning", "failed")},
        "checks": checks,
    }


def load_pubmed(strict_root: Path, expected_pairs: int) -> dict[str, Any]:
    source_root = strict_root / "pubmed_all_pair_evidence"
    finalized_root = strict_root / "pubmed_all_pair_evidence_finalized"
    source_report_path = source_root / "report.json"
    final_report_path = finalized_root / "finalization_report.json"
    source_report = read_json(source_report_path)
    final_report = read_json(final_report_path)
    completed_pairs = int(
        final_report.get("covered_pairs", source_report.get("pair_count", 0)) or 0
    )
    query_count = int(source_report.get("query_count", 0) or 0)
    return {
        "source_root": source_root,
        "finalized_root": finalized_root,
        "source_report": source_report,
        "final_report": final_report,
        "source_report_path": source_report_path,
        "final_report_path": final_report_path,
        "complete": completed_pairs == expected_pairs and query_count == expected_pairs * 2,
        "pair_count": completed_pairs,
        "query_count": query_count,
        "candidate_records": int(
            final_report.get(
                "candidate_records", source_report.get("candidate_articles", 0)
            )
            or 0
        ),
        "multiherb_exclusions": int(
            final_report.get(
                "known_multiherb_formula_exclusions",
                source_report.get("reviewed_multiherb_formula_exclusions", 0),
            )
            or 0
        ),
        "automatically_score_eligible": int(
            final_report.get(
                "automatically_score_eligible_records",
                source_report.get("accepted_evidence_articles", 0),
            )
            or 0
        ),
    }


def load_safety(strict_root: Path, expected_pairs: int) -> dict[str, Any]:
    root = strict_root / "safety_evidence_review"
    report_path = root / "report.json"
    report = read_json(report_path)
    pair_count = int(report.get("pair_count", 0) or 0)
    final_scores = int(report.get("final_numeric_safety_scores", 0) or 0)
    return {
        "root": root,
        "report_path": report_path,
        "report": report,
        "pair_count": pair_count,
        "covered": pair_count == expected_pairs,
        "final_numeric_scores": final_scores,
        "manual_review_required": report.get("status") == "manual_review_required"
        or final_scores < expected_pairs,
        "reason": str(report.get("reason", "")),
    }


def _with_pair_key(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "pair_key" not in result.columns and {"herb_a", "herb_b"}.issubset(result.columns):
        result["pair_key"] = (
            result["herb_a"].astype(str) + "＋" + result["herb_b"].astype(str)
        )
    if {"herb_a", "herb_b"}.issubset(result.columns):
        result["_pair_id"] = result.apply(
            lambda row: "||".join(sorted((str(row["herb_a"]), str(row["herb_b"])))),
            axis=1,
        )
    return result


def _numeric(value: Any, default: float = 0.0) -> float:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return default if pd.isna(number) else float(number)


def load_pair_explorer(
    project_root: Path | str = PROJECT_ROOT,
    analysis_root: Path | str | None = None,
    disease_targets_path: Path | str | None = None,
) -> dict[str, Any]:
    """Load the 190-pair catalogue and lightweight evidence indexes.

    The 47 MB combined DAVID table is intentionally not loaded here. A selected
    pair reads its own rank_NNN/david_chart.tsv in ``build_pair_detail``.
    """
    project_root = Path(project_root)
    strict_root = Path(analysis_root) if analysis_root else (
        project_root / "data" / "formal_analysis" / "genecards_multisource_pairs_20_strict"
    )
    raw_path = strict_root / "pair_scores_raw_formula_audit.csv"
    refined_path = (
        strict_root
        / "david_all_pairs_complete_uniprot"
        / "all_pairs_david_refined_ranking.csv"
    )
    raw = _with_pair_key(read_csv(raw_path))
    refined = _with_pair_key(read_csv(refined_path))

    if refined.empty:
        catalogue = raw.copy()
    else:
        refined_ids = set(refined["_pair_id"].astype(str))
        stopped = raw[~raw["_pair_id"].astype(str).isin(refined_ids)].copy()
        catalogue = pd.concat([refined, stopped], ignore_index=True, sort=False)

    if not catalogue.empty:
        catalogue["_eligible"] = catalogue.get(
            "eligible_both_herbs_contribute", pd.Series(False, index=catalogue.index)
        ).map(value_is_true)
        catalogue["_shortlisted"] = catalogue.get(
            "included_in_top_n_with_cutoff_ties", pd.Series(False, index=catalogue.index)
        ).map(value_is_true)
        refined_ids = set(refined.get("_pair_id", pd.Series(dtype=str)).astype(str))
        catalogue["_david_complete"] = catalogue["_pair_id"].astype(str).isin(refined_ids)

        def state_for(row: pd.Series) -> str:
            if bool(row["_shortlisted"]):
                return "阶段Top10（含并列）"
            if bool(row["_david_complete"]):
                return "已完成PPI与DAVID，暂未入围"
            if bool(row["_eligible"]):
                return "已过门槛，后续分析待完成"
            return "止于双方独立贡献门槛"

        catalogue["screening_state"] = catalogue.apply(state_for, axis=1)
        state_order = {
            "阶段Top10（含并列）": 0,
            "已完成PPI与DAVID，暂未入围": 1,
            "已过门槛，后续分析待完成": 2,
            "止于双方独立贡献门槛": 3,
        }
        catalogue["_state_order"] = catalogue["screening_state"].map(state_order)
        refined_rank = (
            pd.to_numeric(catalogue["david_refined_rank"], errors="coerce")
            if "david_refined_rank" in catalogue.columns
            else pd.Series(pd.NA, index=catalogue.index, dtype="Float64")
        )
        raw_rank = (
            pd.to_numeric(catalogue["raw_formula_rank"], errors="coerce")
            if "raw_formula_rank" in catalogue.columns
            else pd.Series(pd.NA, index=catalogue.index, dtype="Float64")
        )
        catalogue["_rank_order"] = refined_rank.fillna(raw_rank).fillna(999999)
        catalogue = catalogue.sort_values(
            ["_state_order", "_rank_order", "pair_key"], kind="stable"
        ).reset_index(drop=True)

    return {
        "project_root": project_root,
        "strict_root": strict_root,
        "raw_path": raw_path,
        "refined_path": refined_path,
        "catalogue": catalogue,
        "herb_audit": read_csv(strict_root / "herb_target_audit.csv"),
        "topology": read_csv(strict_root / "cytoscape_topology_metrics.csv"),
        "pubmed": _with_pair_key(
            read_csv(
                strict_root
                / "pubmed_all_pair_evidence_finalized"
                / "pair_pubmed_evidence_summary.csv"
            )
        ),
        "safety": _with_pair_key(
            read_csv(strict_root / "safety_evidence_review" / "pair_safety_review_template.csv")
        ),
        "scoring": (
            lambda report, fallback: {
                **fallback,
                "barrier_pathway_keywords": report.get(
                    "pathway_focus_keywords",
                    report.get(
                        "barrier_pathway_keywords",
                        fallback.get("barrier_pathway_keywords", []),
                    ),
                ),
                "pathway_focus_label": report.get(
                    "pathway_focus_label", "肠黏膜保护相关条目"
                ),
            }
        )(
            read_json(
                strict_root
                / "david_all_pairs_complete_uniprot"
                / "david_pathway_scoring_report.json"
            ),
            read_json(project_root / "config" / "scoring.json"),
        ),
        "paths": {
            "string_raw": strict_root / "string_network_raw.json",
            "cytoscape_report": strict_root / "cytoscape_report.json",
            "cytoscape_edges": strict_root / "cytoscape_edges.csv",
            "cytoscape_dropped_edges": strict_root / "cytoscape_dropped_edges.csv",
            "cytoscape_session": strict_root / "formal_ppi_session.cys",
            "ppi_image": strict_root / "formal_ppi_network.png",
            "gene_cards": Path(disease_targets_path) if disease_targets_path else (
                project_root / "data" / "formal_inputs" / "genecards_uc"
                / "uc_genecards_targets_all_normalized.csv"
            ),
        },
    }


def _row_for(frame: pd.DataFrame, column: str, value: str) -> dict[str, Any]:
    if frame.empty or column not in frame.columns:
        return {}
    rows = frame[frame[column].astype(str).eq(value)]
    return rows.iloc[0].to_dict() if not rows.empty else {}


def _row_for_pair(frame: pd.DataFrame, herb_a: str, herb_b: str) -> dict[str, Any]:
    if frame.empty:
        return {}
    pair_id = "||".join(sorted((herb_a, herb_b)))
    if "_pair_id" in frame.columns:
        rows = frame[frame["_pair_id"].astype(str).eq(pair_id)]
        if not rows.empty:
            return rows.iloc[0].to_dict()
    if {"herb_a", "herb_b"}.issubset(frame.columns):
        rows = frame[
            (
                frame["herb_a"].astype(str).eq(herb_a)
                & frame["herb_b"].astype(str).eq(herb_b)
            )
            | (
                frame["herb_a"].astype(str).eq(herb_b)
                & frame["herb_b"].astype(str).eq(herb_a)
            )
        ]
        if not rows.empty:
            return rows.iloc[0].to_dict()
    return {}


def _pair_stop_reason(row: dict[str, Any]) -> str:
    herb_a = str(row.get("herb_a", "药A"))
    herb_b = str(row.get("herb_b", "药B"))
    targets_a = int(_numeric(row.get("targets_a")))
    targets_b = int(_numeric(row.get("targets_b")))
    unique_a = int(_numeric(row.get("unique_targets_a")))
    unique_b = int(_numeric(row.get("unique_targets_b")))
    reasons: list[str] = []
    if targets_a == 0:
        reasons.append(f"{herb_a}没有进入本轮疾病交集靶点")
    elif unique_a == 0:
        reasons.append(f"{herb_a}没有提供区别于{herb_b}的独立疾病靶点")
    if targets_b == 0:
        reasons.append(f"{herb_b}没有进入本轮疾病交集靶点")
    elif unique_b == 0:
        reasons.append(f"{herb_b}没有提供区别于{herb_a}的独立疾病靶点")
    return "；".join(reasons) or "未通过双方均须提供至少1个独立疾病靶点的门槛"


def _significant_david_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or not {"PValue", "Benjamini"}.issubset(frame.columns):
        return pd.DataFrame()
    result = frame.copy()
    result["PValue"] = pd.to_numeric(result["PValue"], errors="coerce")
    result["Benjamini"] = pd.to_numeric(result["Benjamini"], errors="coerce")
    result = result[(result["PValue"] < 0.01) & (result["Benjamini"] < 0.05)]
    return result.sort_values(["PValue", "Benjamini"], kind="stable")


def build_pair_detail(explorer: dict[str, Any], pair_key: str) -> dict[str, Any]:
    catalogue: pd.DataFrame = explorer["catalogue"]
    row = _row_for(catalogue, "pair_key", pair_key)
    if not row:
        pair_names = [item.strip() for item in re.split(r"[＋+]", pair_key) if item.strip()]
        if len(pair_names) == 2:
            row = _row_for_pair(catalogue, pair_names[0], pair_names[1])
    if not row:
        return {}

    herb_a = str(row.get("herb_a", ""))
    herb_b = str(row.get("herb_b", ""))
    eligible = value_is_true(row.get("eligible_both_herbs_contribute"))
    shortlisted = value_is_true(row.get("included_in_top_n_with_cutoff_ties"))
    david_complete = bool(row.get("_david_complete", False))
    herb_audit: pd.DataFrame = explorer["herb_audit"]
    audit_a = _row_for(herb_audit, "herb_name", herb_a)
    audit_b = _row_for(herb_audit, "herb_name", herb_b)

    genes_a = set(split_symbols(audit_a.get("disease_gene_symbols")))
    genes_b = set(split_symbols(audit_b.get("disease_gene_symbols")))
    shared = set(split_symbols(row.get("shared_gene_symbols"))) or (genes_a & genes_b)
    union = set(split_symbols(row.get("union_gene_symbols"))) or (genes_a | genes_b)
    unique_a = genes_a - genes_b
    unique_b = genes_b - genes_a

    topology: pd.DataFrame = explorer["topology"]
    pair_topology = pd.DataFrame()
    if not topology.empty and "name" in topology.columns and union:
        pair_topology = topology[topology["name"].astype(str).isin(union)].copy()
        for column in ("Degree", "BetweennessCentrality", "ClosenessCentrality"):
            if column in pair_topology.columns:
                pair_topology[column] = pd.to_numeric(pair_topology[column], errors="coerce")
        sort_column = "Degree" if "Degree" in pair_topology.columns else "name"
        pair_topology = pair_topology.sort_values(sort_column, ascending=False, kind="stable")

    ppi_edges = read_csv(explorer["paths"]["cytoscape_edges"])
    if not ppi_edges.empty and {"source", "target"}.issubset(ppi_edges.columns):
        ppi_edges = ppi_edges[
            ppi_edges["source"].astype(str).isin(union)
            & ppi_edges["target"].astype(str).isin(union)
        ].copy()
        if "combined_score" in ppi_edges.columns:
            ppi_edges["combined_score"] = pd.to_numeric(
                ppi_edges["combined_score"], errors="coerce"
            )
            ppi_edges = ppi_edges.sort_values(
                "combined_score", ascending=False, kind="stable"
            )

    analysis_rank = int(_numeric(row.get("analysis_rank_before_david"), 0))
    david_dir = (
        explorer["strict_root"]
        / "david_all_pairs_complete_uniprot"
        / f"rank_{analysis_rank:03d}"
        if analysis_rank
        else explorer["strict_root"] / "david_all_pairs_complete_uniprot" / "not_run"
    )
    david_chart = read_delimited(david_dir / "david_chart.tsv")
    significant = _significant_david_rows(david_chart)
    kegg = (
        significant[significant["Category"].astype(str).eq("KEGG_PATHWAY")].copy()
        if not significant.empty and "Category" in significant.columns
        else pd.DataFrame()
    )
    go = (
        significant[significant["Category"].astype(str).str.startswith("GOTERM_")].copy()
        if not significant.empty and "Category" in significant.columns
        else pd.DataFrame()
    )
    keywords = [
        str(item).casefold()
        for item in explorer.get("scoring", {}).get("barrier_pathway_keywords", [])
    ]
    barrier = pd.DataFrame()
    if not significant.empty and "Term" in significant.columns and keywords:
        mask = significant["Term"].astype(str).str.casefold().map(
            lambda term: any(keyword in term for keyword in keywords)
        )
        barrier = significant[mask].copy()

    pubmed = _row_for_pair(explorer["pubmed"], herb_a, herb_b)
    safety = _row_for_pair(explorer["safety"], herb_a, herb_b)

    if shortlisted:
        outcome = (
            f"进入阶段Top10候选，当前顺序为第{int(_numeric(row.get('david_refined_rank')))}名。"
            "这是70分已完成维度的顺序，不是最终药效结论。"
        )
    elif david_complete:
        outcome = (
            f"已完成PPI和DAVID比较，当前顺序为第{int(_numeric(row.get('david_refined_rank')))}名，"
            "暂未进入阶段Top10。"
        )
    elif eligible:
        outcome = "已通过双方独立贡献门槛，后续PPI或DAVID分析尚未形成完整结果。"
    else:
        outcome = f"该组合止于双方独立贡献门槛：{_pair_stop_reason(row)}。"

    trace: list[dict[str, str]] = [
        {
            "步骤": "1. 药材范围",
            "状态": "通过",
            "判断依据": "两味药均来自本任务候选池",
        },
        {
            "步骤": "2. 疾病靶点交集",
            "状态": "通过" if genes_a and genes_b else "未通过",
            "判断依据": f"{herb_a} {len(genes_a)}个；{herb_b} {len(genes_b)}个疾病交集靶点",
        },
        {
            "步骤": "3. 双方独立贡献",
            "状态": "通过" if eligible else "止于本关",
            "判断依据": (
                f"独有靶点：{herb_a} {len(unique_a)}个；{herb_b} {len(unique_b)}个"
                if eligible
                else _pair_stop_reason(row)
            ),
        },
        {
            "步骤": "4. STRING/Cytoscape",
            "状态": "进入重评分" if david_complete else "仅保留原始指标" if not eligible else "待完成",
            "判断依据": (
                f"在128靶点高置信网络中计算药对内PPI和跨药独有靶点连边；跨药边{int(_numeric(row.get('cross_herb_ppi_edges')))}条"
                if david_complete
                else (
                    "全局STRING网络已一次性构建，可派生本药对子网；本组合不进入后续PPI特征重评分和DAVID"
                    if not eligible
                    else "等待网络分析"
                )
            ),
        },
        {
            "步骤": "5. DAVID药对-单药比较",
            "状态": "完成" if david_complete else "未进入" if not eligible else "待完成",
            "判断依据": (
                f"显著KEGG {int(_numeric(row.get('pair_significant_kegg_count')))}条；组合新增{int(_numeric(row.get('emergent_pair_kegg_count')))}条"
                if david_complete
                else "只有通过独立贡献门槛的组合才进入本步"
            ),
        },
        {
            "步骤": "6. 阶段候选顺序",
            "状态": "入围" if shortlisted else "暂未入围" if david_complete else "未进入",
            "判断依据": outcome,
        },
        {
            "步骤": "7. 安全与配伍证据",
            "状态": "待人工" if eligible else "未进入",
            "判断依据": (
                "安全15分和配伍证据15分尚未形成可用终分"
                if eligible
                else "组合已在前置关卡停止"
            ),
        },
        {
            "步骤": "8. 正式分子对接",
            "状态": "待筛定" if shortlisted else "未进入",
            "判断依据": "只对最终锁定的Top3核心成分和靶点执行，不对全部组合逐一对接",
        },
    ]

    return {
        "row": row,
        "pair_key": str(row.get("pair_key", pair_key)),
        "herb_a": herb_a,
        "herb_b": herb_b,
        "audit_a": audit_a,
        "audit_b": audit_b,
        "eligible": eligible,
        "shortlisted": shortlisted,
        "david_complete": david_complete,
        "outcome": outcome,
        "genes_a": sorted(genes_a),
        "genes_b": sorted(genes_b),
        "shared_genes": sorted(shared),
        "unique_genes_a": sorted(unique_a),
        "unique_genes_b": sorted(unique_b),
        "union_genes": sorted(union),
        "topology": pair_topology,
        "ppi_edges": ppi_edges,
        "david_chart": david_chart,
        "significant_kegg": kegg,
        "significant_go": go,
        "barrier_terms": barrier,
        "pubmed": pubmed,
        "safety": safety,
        "trace": trace,
        "david_dir": david_dir,
        "artifacts": {
            "david_request": david_dir / "request.json",
            "david_report": david_dir / "report.json",
            "david_table": david_dir / "david_chart.tsv",
            "david_api_response": david_dir / "01_api_response.html",
            "david_chart_response": david_dir / "02_chart_response.html",
            "david_reuse_attestation": david_dir / "reuse_attestation.json",
        },
    }


def load_active_ingredients(
    explorer: dict[str, Any], herb_name: str, source_label: str
) -> dict[str, Any]:
    """Return the exact active-ingredient table used by the formal input source."""
    root: Path = explorer["project_root"]
    source = str(source_label)
    if source.startswith("TCMSP"):
        matches = list((root / "data" / "channel_tests" / "tcmsp_uc21").glob(f"*_{herb_name}.json"))
        if not matches:
            return {"data": pd.DataFrame(), "path": Path(), "filter": "OB≥30%、DL≥0.18"}
        path = matches[0]
        payload = read_json(path)
        rows = []
        for item in payload.get("ingredients", []):
            if _numeric(item.get("ob")) >= 30.0 and _numeric(item.get("dl")) >= 0.18:
                rows.append(
                    {
                        "成分ID": item.get("MOL_ID"),
                        "成分名称": item.get("molecule_name"),
                        "OB": _numeric(item.get("ob")),
                        "DL": _numeric(item.get("dl")),
                    }
                )
        return {"data": pd.DataFrame(rows), "path": path, "filter": "OB≥30%、DL≥0.18"}

    herb2_match = re.search(r"(HERB\d+)", source)
    if herb2_match:
        herb_id = herb2_match.group(1)
        path = root / "data" / "formal_inputs" / "herb2_supplements" / herb_id / "ingredients.csv"
        frame = read_csv(path)
        if not frame.empty and "active_ob30_dl018" in frame.columns:
            frame = frame[frame["active_ob30_dl018"].map(value_is_true)].copy()
        columns = [
            column
            for column in ("ingredient_id", "ingredient_name", "ob", "dl", "pubchem_id")
            if column in frame.columns
        ]
        return {"data": frame[columns], "path": path, "filter": "HERB 2.0中OB≥30%、DL≥0.18"}

    if source.startswith("ETCM"):
        path = root / "data" / "formal_inputs" / "etcm2_supplements" / herb_name / "ingredients.csv"
        frame = read_csv(path)
        if not frame.empty and "etcm_standards_yes" in frame.columns:
            frame = frame[frame["etcm_standards_yes"].map(value_is_true)].copy()
        columns = [
            column
            for column in (
                "ingredient_name",
                "molecular_formula",
                "molecular_weight",
                "qed",
                "fdamdd",
            )
            if column in frame.columns
        ]
        return {"data": frame[columns], "path": path, "filter": "ETCM 2.0 standards=yes"}

    return {"data": pd.DataFrame(), "path": Path(), "filter": "来源筛选规则见正式报告"}


def load_current_dashboard(
    project_root: Path | str = PROJECT_ROOT,
    task_config: Path | str | None = None,
) -> dict[str, Any]:
    project_root = Path(project_root)
    task: dict[str, Any] = {}
    if task_config:
        config_path = Path(task_config)
        config_path = config_path if config_path.is_absolute() else project_root / config_path
        task = read_json(config_path)
    paths = task.get("paths", {})
    analysis_value = paths.get("analysis_root") or paths.get("legacy_analysis_root")
    strict_root = (
        (project_root / analysis_value) if analysis_value else
        project_root / "data" / "formal_analysis" / "genecards_multisource_pairs_20_strict"
    )
    candidate_value = paths.get("candidate_herbs_csv")
    candidate_path = project_root / candidate_value if candidate_value else None
    scope = load_scope(project_root, strict_root, candidate_path=candidate_path)
    if task.get("job"):
        scope["disease"] = task["job"].get("disease", {})
        scope["job"] = task["job"]
        scope["input_candidate_count"] = len(task["job"].get("herbs", []))
        if not scope["candidate_herb_count"]:
            scope["candidate_herb_count"] = len(task["job"].get("herbs", []))
        scope["huangqin_excluded"] = False
    expected_pairs = int(scope["eligible_pairs"] or 106)
    ranking = load_ranking(strict_root)
    david = load_david(strict_root, expected_pairs)
    david_single = load_david_single_baselines(strict_root)
    pubmed = load_pubmed(strict_root, expected_pairs)
    safety = load_safety(strict_root, expected_pairs)
    funnel = build_screening_funnel(scope, ranking, david)
    docking_report = read_json(strict_root / "docking_top3" / "report.json")
    if docking_report.get("status") == "completed":
        funnel[-1] = {
            "name": "Top3分子对接",
            "value": "3 / 3组已完成",
            "note": "共晶配体回对接通过，Vina日志与结合能矩阵已封存",
            "state": "done",
        }
    return {
        "project_root": project_root,
        "task": task,
        "strict_root": strict_root,
        "scope": scope,
        "ranking": ranking,
        "david": david,
        "david_single": david_single,
        "pubmed": pubmed,
        "safety": safety,
        "quality_control": load_quality_control(strict_root),
        "funnel": funnel,
        "docking": {
            "report": docking_report,
            "report_path": strict_root / "docking_top3" / "report.json",
            "matrix": read_csv(strict_root / "docking_top3" / "binding_energy_matrix.csv"),
            "matrix_path": strict_root / "docking_top3" / "binding_energy_matrix.csv",
            "redocking": read_json(strict_root / "docking_top3" / "redocking_validation.json"),
            "redocking_path": strict_root / "docking_top3" / "redocking_validation.json",
            "redocking_table": read_csv(
                strict_root / "docking_top3" / "redocking_validation.csv"
            ),
            "redocking_table_path": (
                strict_root / "docking_top3" / "redocking_validation.csv"
            ),
            "top3": read_csv(strict_root / "docking_top3" / "top3_candidates.csv"),
            "top3_path": strict_root / "docking_top3" / "top3_candidates.csv",
            "attempts": read_csv(
                strict_root / "docking_top3" / "docking_attempt_audit.csv"
            ),
            "attempts_path": (
                strict_root / "docking_top3" / "docking_attempt_audit.csv"
            ),
            "queue": read_csv(strict_root / "docking_top3" / "docking_pair_queue.csv"),
            "queue_path": strict_root / "docking_top3" / "docking_pair_queue.csv",
            "selection_report": read_json(
                strict_root / "docking_top3" / "selection_report.json"
            ),
            "selection_report_path": (
                strict_root / "docking_top3" / "selection_report.json"
            ),
            "structure_manifest": read_csv(
                strict_root / "docking_top3" / "structure_manifest.csv"
            ),
            "structure_manifest_path": (
                strict_root / "docking_top3" / "structure_manifest.csv"
            ),
        },
    }


def ranking_display(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    data = frame.copy()
    if "pair_key" not in data.columns and {"herb_a", "herb_b"}.issubset(data.columns):
        data["pair_key"] = data["herb_a"].astype(str) + "＋" + data["herb_b"].astype(str)
    rank_column = next(
        (
            column
            for column in (
                "david_refined_rank",
                "adjusted_interim_rank",
                "provisional_rank",
            )
            if column in data.columns
        ),
        None,
    )
    if rank_column is None:
        data["_display_rank"] = range(1, len(data) + 1)
        rank_column = "_display_rank"
    if "top_n_boundary_tie" in data.columns:
        boundary = (
            data["top_n_boundary_tie"]
            .astype(str)
            .str.strip()
            .str.casefold()
            .isin({"true", "1", "yes"})
        )
        if boundary.any():
            boundary_rank = int(
                pd.to_numeric(data.loc[boundary, rank_column], errors="coerce").min()
            )
            data["_display_rank_with_ties"] = data[rank_column].astype(str)
            data.loc[boundary, "_display_rank_with_ties"] = f"{boundary_rank}（并列）"
            rank_column = "_display_rank_with_ties"
    if "david_refined_core_score" in data.columns:
        data["_core_score"] = data["david_refined_core_score"]
    elif {
        "target_complementarity_score_adjusted",
        "pathway_synergy_score",
    }.issubset(data.columns):
        data["_core_score"] = (
            0.4 * pd.to_numeric(data["target_complementarity_score_adjusted"], errors="coerce")
            + 0.3 * pd.to_numeric(data["pathway_synergy_score"], errors="coerce")
        )
    else:
        data["_core_score"] = pd.NA

    selected: list[tuple[str, str]] = [(rank_column, "阶段顺序"), ("pair_key", "药对")]
    focus_count_column = (
        "pair_focus_term_count"
        if "pair_focus_term_count" in data.columns
        else "pair_barrier_term_count"
    )
    pathway_columns = (
        [("david_pathway_synergy_score", "DAVID通路协同潜力")]
        if "david_pathway_synergy_score" in data.columns
        else [("pathway_synergy_score", "预富集通路分")]
    )
    for source, label in (
        [("target_complementarity_score_adjusted", "校正后靶点互补")]
        + pathway_columns
        + [
        ("emergent_pair_kegg_count", "组合新增显著KEGG数"),
        (focus_count_column, "疾病重点条目数"),
        ("cross_herb_ppi_edges", "跨药材PPI边"),
        ("_core_score", "已完成两维核心分"),
        ("score_finality", "结论状态"),
        ]
    ):
        if source in data.columns and source not in {item[0] for item in selected}:
            selected.append((source, label))
    result = data[[source for source, _ in selected]].rename(
        columns={source: label for source, label in selected}
    )
    number_columns = result.select_dtypes(include="number").columns
    result[number_columns] = result[number_columns].round(3)
    # 输入文件本身已限定候选范围；这里不能再次head(10)，否则会把并列第10名截掉。
    return result
