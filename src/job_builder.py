from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any


def safe_slug(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value.strip())
    return value.strip("-") or "research-task"


def normalize_herbs(value: str | list[str]) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[、,，;；\n]+", value)
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        herb = str(item).strip()
        if herb and herb not in seen:
            seen.add(herb)
            result.append(herb)
    return result


def task_summary(config: dict[str, Any]) -> dict[str, Any]:
    job = config.get("job", {})
    herbs = normalize_herbs(job.get("herbs", []))
    size = int(job.get("combination_size", 2))
    return {
        "disease_cn": job.get("disease", {}).get("name_cn", ""),
        "disease_en": job.get("disease", {}).get("name_en", ""),
        "species": job.get("disease", {}).get("species", "Homo sapiens"),
        "herbs": herbs,
        "herb_count": len(herbs),
        "combination_size": size,
        "combination_count": (
            len(herbs) * (len(herbs) - 1) // 2
            if size == 2 and len(herbs) >= 2
            else 0
        ),
        "top_n": int(job.get("top_n", 10)),
        "docking_top_n": int(job.get("docking_top_n", 3)),
    }


def build_task_config(
    template: dict[str, Any],
    *,
    disease_cn: str,
    disease_en: str,
    species: str,
    taxon_id: int,
    herbs: str | list[str],
    task_id: str | None = None,
) -> dict[str, Any]:
    """Create an isolated task config without mutating the source template."""
    result = copy.deepcopy(template)
    normalized = normalize_herbs(herbs)
    if len(normalized) < 2:
        raise ValueError("至少需要两味不重复的候选中药")
    task_id = safe_slug(task_id or f"{disease_en or disease_cn}-{len(normalized)}herbs")
    result["pipeline_profile"] = "parameterized_two_herb_evidence_v2"
    result.setdefault("supported_combination_sizes", [2])
    result["paths"]["candidate_herbs_csv"] = f"config/tasks/{task_id}/candidate_herbs.csv"
    result["paths"]["analysis_root"] = f"data/formal_analysis/{task_id}"
    result["paths"]["legacy_analysis_root"] = result["paths"]["analysis_root"]
    result["paths"]["david_pairs_root"] = f"data/formal_analysis/{task_id}/david_all_pairs_complete_uniprot"
    result["paths"]["david_single_root"] = f"data/formal_analysis/{task_id}/david_single_herb_baselines_all_complete_uniprot"
    result["paths"]["docking_root"] = f"data/formal_analysis/{task_id}/docking_top3"
    result["paths"]["disease_targets_root"] = f"data/formal_inputs/diseases/{task_id}"
    result["paths"]["tcmsp_evidence_root"] = f"data/channel_tests/tcmsp/{task_id}"
    result["paths"]["pipeline_config"] = f"config/tasks/{task_id}/pipeline.json"
    result["job"] = {
        "id": task_id,
        "name": f"{disease_cn or disease_en}_{len(normalized)}味中药_两味组合筛选",
        "disease": {
            "name_cn": disease_cn.strip(),
            "name_en": disease_en.strip(),
            "search_terms": [term for term in (disease_en.strip(), disease_cn.strip()) if term],
            "species": species.strip() or "Homo sapiens",
            "taxon_id": int(taxon_id),
        },
        "herbs": normalized,
        "combination_size": 2,
        "top_n": 10,
        "docking_top_n": 3,
        "endpoint": "疾病相关靶点、PPI和通路支持下的中药组合协同潜力",
    }
    disease_label = disease_en.strip() or disease_cn.strip()
    disease_text = f"{disease_en} {disease_cn}".casefold()
    is_uc = "ulcerative colitis" in disease_text or "溃疡性结肠炎" in disease_text
    if is_uc:
        focus_keywords = [
            "tight junction", "intestinal epithelial", "epithelial barrier",
            "mucus", "mucin", "wound healing",
        ]
        focus_label = "肠黏膜保护相关条目"
        keyword_mode = "UC预设：肠黏膜保护"
    else:
        english = disease_en.strip().casefold()
        stop_words = {"disease", "syndrome", "disorder", "type", "with", "without"}
        tokens = [
            token
            for token in re.findall(r"[a-z][a-z0-9-]{3,}", english)
            if token not in stop_words
        ]
        focus_keywords = list(dict.fromkeys(
            [value for value in (english, *tokens) if value]
        ))
        if not focus_keywords and disease_cn.strip():
            focus_keywords = [disease_cn.strip()]
        focus_label = f"{disease_label}重点相关条目"
        keyword_mode = "按疾病标准名自动生成；正式研究可在任务配置中复核调整"
    scoring = result.setdefault("scoring", {})
    scoring.update({
        "disease": disease_label,
        "pathway_focus_keywords": focus_keywords,
        "pathway_focus_label": focus_label,
        "pathway_keyword_mode": keyword_mode,
    })
    result["scope_policy"] = {
        "food_medicine_only": False,
        "material_type": "中药",
        "note": "不限制药食同源；候选范围完全由本任务输入决定。",
    }
    for source in result.get("data_sources", []):
        if source.get("name") == "GeneCards":
            source["purpose"] = f"{disease_label}疾病靶点及相关性分数"
    for step in result.get("steps", []):
        name = str(step.get("name", ""))
        step["name"] = name.replace("21味", f"{len(normalized)}味").replace("UC", disease_cn or disease_en)
        if step.get("id") == "02_herb_component_targets":
            step.update({
                "name": f"{len(normalized)}味中药成分—靶点原始证据",
                "type": "verify_artifacts",
                "required": True,
                "artifacts": [
                    "{paths.candidate_herbs_csv}",
                    "{paths.tcmsp_evidence_root}/channel_test_report.json",
                ],
                "action_required": "先运行候选中药数据库抓取；TCMSP缺失项须由精确HERB/ETCM材料记录补充。",
            })
        if step.get("id") == "01_disease_targets":
            step.update({
                "name": "疾病靶点授权导出与标准化",
                "type": "input_gate",
                "required": True,
                "artifacts": [
                    "{paths.disease_targets_root}/raw_export.*",
                    "{paths.disease_targets_root}/disease_targets_normalized.csv",
                    "{paths.disease_targets_root}/import_report.json",
                ],
                "action_required": "上传从GeneCards按疾病标准名导出的表格；系统不保存账号、密码或Cookie。",
            })
        if step.get("id") == "03_pair_enumeration_string":
            command = list(step.get("command", []))
            for index, value in enumerate(command):
                if value == "{paths.disease_targets_root}/uc_genecards_targets_normalized.csv":
                    command[index] = "{paths.disease_targets_root}/disease_targets_normalized.csv"
            if not is_uc and "--supplement-target-filename" not in command:
                command.extend([
                    "--supplement-target-filename", "ingredient_targets_active.csv"
                ])
            step["command"] = command
        if step.get("id") == "06_david_pair_enrichment":
            step.update({
                "name": "DAVID全部合格药对富集",
                "type": "command",
                "required": True,
                "reuse_if_artifacts_exist": True,
                "adopt_existing_artifacts": True,
                "command": [
                    "{python}", "scripts/run_david_enrichment_batch.py",
                    "--pair-path", "{paths.analysis_root}/pair_scores_source_bias_adjusted.csv",
                    "--output-root", "{paths.david_pairs_root}",
                    "--rank-column", "adjusted_interim_rank",
                    "--database-path", "{paths.database}",
                    "--id-type", "UNIPROT_ACCESSION",
                    "--resume", "--continue-on-error",
                ],
                "artifacts": [
                    "{paths.david_pairs_root}/all_enrichment_rows.csv",
                    "{paths.david_pairs_root}/uniprot_mapping_audit.csv",
                    "{paths.david_pairs_root}/report.json",
                ],
            })
        if step.get("id") == "08_pathway_refinement":
            step.update({
                "name": "全部药对相对单药的通路增益评分",
                "type": "command",
                "required": True,
                "reuse_if_artifacts_exist": True,
                "adopt_existing_artifacts": True,
                "command": [
                    "{python}", "scripts/recalculate_david_pathway_scores.py",
                    "--input-root", "{paths.analysis_root}",
                    "--pair-input", "{paths.david_pairs_root}/all_enrichment_rows.csv",
                    "--single-input", "{paths.david_single_root}/all_enrichment_rows.csv",
                    "--shortlist-input", "{paths.analysis_root}/pair_scores_source_bias_adjusted.csv",
                    "--output-root", "{paths.david_pairs_root}",
                    "--job-config", "{resolved_config}",
                    "--candidate-herbs-csv", "{paths.candidate_herbs_csv}",
                ],
                "artifacts": [
                    "{paths.david_pairs_root}/pair_vs_single_pathway_metrics.csv",
                    "{paths.david_pairs_root}/all_pairs_david_refined_ranking.csv",
                    "{paths.david_pairs_root}/top10_david_refined_with_cutoff_ties.csv",
                    "{paths.david_pairs_root}/david_pathway_scoring_report.json",
                ],
            })
        if step.get("id") == "07_david_single_baselines":
            step.update({
                "name": "DAVID全部单味药对照基线",
                "type": "command",
                "required": True,
                "reuse_if_artifacts_exist": True,
                "adopt_existing_artifacts": True,
                "command": [
                    "{python}", "scripts/run_david_enrichment_batch.py",
                    "--pair-path", "{paths.analysis_root}/david_single_herb_baselines_input.csv",
                    "--output-root", "{paths.david_single_root}",
                    "--rank-column", "analysis_rank",
                    "--database-path", "{paths.database}",
                    "--id-type", "UNIPROT_ACCESSION",
                    "--resume", "--continue-on-error",
                ],
                "artifacts": [
                    "{paths.analysis_root}/david_single_herb_baselines_input.csv",
                    "{paths.david_single_root}/all_enrichment_rows.csv",
                    "{paths.david_single_root}/report.json",
                ],
            })
        if step.get("id") in {"09_safety_evidence", "10_pair_literature_evidence"}:
            step["required"] = False
            step["halt_on_needs_input"] = False
            step["notes"] = "保留为科研人工复核项，不阻断Top3虚拟筛选与分子对接。"
    result["steps"] = [
        step for step in result.get("steps", [])
        if step.get("id") not in {
            "08a_machine_qc",
            "11_docking_final_candidates",
            "11_select_docking_candidates",
            "12_run_top3_docking",
            "13_machine_qc",
        }
    ]
    result["steps"].extend([
        {
            "id": "11_select_docking_candidates",
            "name": "Top3成分—靶点对接对象锁定",
            "type": "command",
            "required": True,
            "depends_on": ["08_pathway_refinement"],
            "reuse_if_artifacts_exist": True,
            "command": [
                "{python}", "scripts/select_top3_docking_candidates.py",
                "--analysis-root", "{paths.analysis_root}",
                "--tcmsp-root", "{paths.tcmsp_evidence_root}",
                "--herb2-root", "{paths.herb2_supplement_root}",
                "--etcm2-root", "{paths.etcm2_supplement_root}",
                "--database", "{paths.database}",
                "--output-root", "{paths.analysis_root}/docking_top3",
                "--supplement-target-filename", (
                    "ingredient_targets_active_uc_intersection.csv"
                    if is_uc else "ingredient_targets_active.csv"
                ),
            ],
            "artifacts": [
                "{paths.analysis_root}/docking_top3/top3_candidates.csv",
                "{paths.analysis_root}/docking_top3/ingredient_target_candidates.csv",
                "{paths.analysis_root}/docking_top3/selected_docking_candidates.csv",
                "{paths.analysis_root}/docking_top3/selection_report.json",
            ],
        },
        {
            "id": "12_run_top3_docking",
            "name": "Top3真实分子对接与回对接质控",
            "type": "command",
            "required": True,
            "depends_on": ["11_select_docking_candidates"],
            "reuse_if_artifacts_exist": True,
            "command": [
                "{python}", "scripts/run_generic_top3_docking.py",
                "--analysis-root", "{paths.analysis_root}",
                "--registry", "config/docking_targets.json",
                "--exhaustiveness", "16", "--seed", "20260927",
            ],
            "artifacts": [
                "{paths.analysis_root}/docking_top3/report.json",
                "{paths.analysis_root}/docking_top3/redocking_validation.json",
                "{paths.analysis_root}/docking_top3/binding_energy_matrix.csv",
                "{paths.analysis_root}/docking_top3/structure_manifest.csv",
                "{paths.analysis_root}/docking_top3/docking_parameters.json",
                "{paths.analysis_root}/docking_top3/docking_attempt_audit.csv",
                "{paths.analysis_root}/docking_top3/**/*.log",
                "{paths.analysis_root}/docking_top3/**/*_out.pdbqt",
            ],
        },
        {
            "id": "13_machine_qc",
            "name": "全流程机器质控与证据闭环",
            "type": "command",
            "required": True,
            "depends_on": ["12_run_top3_docking"],
            "command": [
                "{python}", "scripts/run_machine_qc.py",
                "--config", "{resolved_config}",
            ],
            "artifacts": ["{paths.analysis_root}/pipeline_qc_report.json"],
        },
    ])
    return result


def config_bytes(config: dict[str, Any]) -> bytes:
    return json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")


def candidate_csv_text(herbs: list[str]) -> str:
    lines = ["candidate_order,herb_name,research_role,scope_status,notes"]
    for index, herb in enumerate(normalize_herbs(herbs), 1):
        note = "按输入名称独立处理，不自动替换为近似药材" if herb == "槐花" else "不限制药食同源"
        lines.append(f"{index},{herb},候选中药,用户指定候选,{note}")
    return "\n".join(lines) + "\n"


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
