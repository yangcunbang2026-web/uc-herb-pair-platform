"""Read-only integrity gate for the current staged Top3 docking evidence."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import re
from typing import Any


def load_current_docking(project_root: Path, output: Path) -> tuple[dict, list[str]]:
    """Return current verified report/errors; never run or rewrite analysis.

    A missing report is normal pending work. Invalid completed reports are
    demoted in the returned copy only, so callers cannot accidentally display a
    stale result as complete. Both Windows and POSIX relative paths are accepted.
    Every resolved source/download must stay inside the supplied project root.
    """
    root = project_root.resolve()
    stage = output.resolve()
    if not stage.is_relative_to(root):
        return {}, ["当前对接目录越出项目目录"]
    docking = stage / "docking_top3"
    report_path = docking / "report.json"
    if not report_path.is_file():
        return {}, []
    errors: list[str] = []
    hashes: dict[Path, str] = {}

    def fail(message: str) -> None:
        if message not in errors:
            errors.append(message)

    def resolve(value: Any, base: Path = root) -> Path | None:
        if not isinstance(value, str) or not value.strip():
            fail("证据路径缺失")
            return None
        normalized = value.replace("\\", "/")
        # Do not reinterpret a Windows absolute path as a Linux relative path.
        if PureWindowsPath(normalized).is_absolute() and not Path(normalized).is_absolute():
            fail(f"不能重定位外部绝对路径：{value}")
            return None
        path = (base / normalized).resolve()
        if not path.is_relative_to(root):
            fail(f"证据路径越出项目目录：{value}")
            return None
        return path

    def hash_file(path: Path | None, expected: Any, label: str) -> bool:
        if path is None:
            return False
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            fail(f"证据SHA-256缺失或无效：{label}")
            return False
        if not path.is_file():
            fail(f"证据文件不存在：{label}")
            return False
        try:
            if path not in hashes:
                hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
            if hashes[path] != expected.lower():
                fail(f"证据SHA-256不一致：{label}")
                return False
        except OSError as exc:
            fail(f"证据文件不能读取：{label}（{exc}）")
            return False
        return True

    def read_json(path: Path, label: str) -> Any:
        if not path.resolve().is_relative_to(root):
            fail(f"{label}越出项目目录")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            fail(f"{label}不能读取：{exc}")
            return None

    def numeric(value: Any, label: str) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            fail(f"数值无效：{label}")
            return None
        return float(value)

    def sources_walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if "path" in obj and "sha256" in obj:
                path = resolve(obj.get("relative_path") or obj["path"])
                hash_file(path, obj["sha256"], str(obj["path"]))
            for key, value in obj.items():
                if key in {"sources", "ingredient_sources", "source_files"}:
                    if not isinstance(value, list) or not value:
                        fail(f"来源清单缺失：{key}")
                    elif any(not isinstance(item, dict) or "path" not in item or "sha256" not in item for item in value):
                        fail(f"来源路径或指纹缺失：{key}")
                sources_walk(value)
        elif isinstance(obj, list):
            for value in obj:
                sources_walk(value)

    report = read_json(report_path, "分子对接报告")
    if not isinstance(report, dict):
        return {}, errors or ["分子对接报告不是对象"]
    if not stage.is_relative_to(root):
        fail("当前对接目录越出项目目录")
    if report.get("status") != "completed":
        return report, errors
    try:
        funnel = read_json(stage / "funnel_result.json", "当前筛选结果")
        if not isinstance(funnel, dict):
            raise ValueError("缺少当前筛选结果")
        top = funnel.get("top3_docking_candidates", [])
        expected_keys = [row["pair_key"] for row in top]
        if (len(expected_keys) != 3 or len(set(expected_keys)) != 3
                or expected_keys != [row["pair_key"] for row in funnel.get("top10", [])[:3]]):
            fail("当前筛选的Top3顺序或数量无效")
        for key in ("locked_pair_keys", "locked_top3"):
            if report.get(key) != expected_keys:
                fail(f"对接锁定药对与当前Top3不一致：{key}")
        if report.get("completed_pair_count") != 3 or report.get("top3_pair_count") != 3:
            fail("对接完成数量不是锁定的3对")
        if report.get("pair_fallback_allowed") is not False:
            fail("对接报告没有禁止药对顺延")
        parameters = report.get("parameters", {})
        if parameters.get("pair_fallback") is not False:
            fail("对接参数允许或未明确禁止药对顺延")
        source = report.get("funnel_source", {})
        source_path = resolve(source.get("path"))
        if source_path != (stage / "funnel_result.json").resolve():
            fail("对接报告引用的不是当前funnel文件")
        hash_file(source_path, source.get("sha256"), "当前funnel")
        sources_walk(report)
        sources_walk({"source_files": funnel.get("source_files", [])})
        qc = report.get("redocking", {})
        qc_file = read_json(docking / "redocking_validation.json", "回对接质控文件")
        if qc_file != qc:
            fail("报告与独立回对接质控文件不一致")
        rmsd = numeric(qc.get("fixed_receptor_heavy_atom_rmsd_angstrom"), "固定坐标RMSD")
        cutoff = numeric(qc.get("threshold_angstrom"), "回对接RMSD阈值")
        if (rmsd is None or cutoff is None or rmsd < 0 or cutoff != 2.0 or rmsd > cutoff
                or qc.get("passed") is not True or qc.get("alignment_applied") is not False
                or qc.get("status") != "verified"):
            fail("固定坐标回对接质控数值/通过标记不一致或未通过")
        qc_target = docking / f"target_{qc.get('target_gene')}_{qc.get('pdb_id')}"
        for item in qc.get("sources", []):
            source_path = resolve(item.get("path"))
            if source_path is not None:
                copied = qc_target / source_path.name
                if copied.is_file():
                    hash_file(copied, item.get("sha256"), f"本轮复制质控文件 {source_path.name}")
        pairs = report.get("pairs", [])
        if [row.get("pair_key") for row in pairs] != expected_keys:
            fail("逐药对完成明细与当前Top3不一致")
        unique_all = {}
        for pair_index, pair in enumerate(pairs, 1):
            if pair_index > len(top):
                fail("对接明细包含额外药对")
                continue
            current = top[pair_index - 1]
            if pair.get("status") != "docking_evidence_complete" or pair.get("pair_rank") != pair_index:
                fail(f"药对未完成或顺序错误：{pair.get('pair_key')}")
            if any(pair.get(field) != current.get(field) for field in ("herb_a", "herb_b")):
                fail("药对药材名称与筛选结果不一致")
            gene, pdb = pair.get("target_gene"), pair.get("pdb_id")
            if gene not in current.get("core_common_targets", []) or pair.get("target_is_pair_common_core") is not True:
                fail("对接靶点不属于本药对共同核心靶点")
            if gene != qc.get("target_gene") or pdb != qc.get("pdb_id"):
                fail("对接靶点/结构与通过质控的结构不一致")
            assignments = pair.get("assignments", [])
            if {row.get("herb_name") for row in assignments} != {current.get("herb_a"), current.get("herb_b")}:
                fail("药对没有覆盖双方药材的成分证据")
            unique = {}
            for row in assignments:
                if row.get("pair_key") != pair.get("pair_key") or row.get("target_gene") != gene or row.get("pdb_id") != pdb:
                    fail("成分对接归属药对或结构不一致")
                if not row.get("ingredient_sources"):
                    fail("成分缺少药材—成分—靶点来源")
                task_key = row.get("chemical_task_key")
                if task_key != f"{gene}:{pdb}:PubChem:{row.get('pubchem_cid')}":
                    fail("独立化学任务标识与PubChem结构不一致")
                value = numeric(row.get("best_affinity_kcal_mol"), "结合能")
                if task_key in unique_all and unique_all[task_key] != value:
                    fail("同一化学任务出现不一致的结合能")
                unique[task_key] = value
                unique_all[task_key] = value
                source_by_name = {}
                for item in row.get("sources", []):
                    item_path = resolve(item.get("path"))
                    if item_path is not None:
                        source_by_name[item_path.name] = item.get("sha256")
                copied_paths = {}
                for field in ("pose_file", "vina_log", "sdf_file"):
                    path = resolve(row.get(field), docking)
                    if path is not None:
                        if not path.is_relative_to(docking):
                            fail(f"下载文件不在本轮对接目录：{field}")
                        expected = source_by_name.get(path.name) or row.get({"pose_file": "pose_sha256", "vina_log": "log_sha256", "sdf_file": "sdf_sha256"}[field])
                        hash_file(path, expected, f"本轮复制文件 {field}")
                        copied_paths[field] = path
                pose_path = copied_paths.get("pose_file")
                if pose_path is not None:
                    target_dir = pose_path.parent
                    ligand = target_dir / pose_path.name.replace("_out.pdbqt", ".pdbqt")
                    hash_file(ligand, row.get("ligand_sha256"), "本轮配体PDBQT")
                    hash_file(target_dir / f"{pdb}_receptor.pdbqt", row.get("receptor_sha256"), "本轮受体")
                    hash_file(target_dir / f"{pdb}_vina_box.txt", row.get("grid_sha256"), "本轮网格")
            threshold = numeric(pair.get("strong_binding_threshold_kcal_mol"), "结合能统计阈值")
            if threshold != -7.0:
                fail("药对结合能统计阈值不是-7")
            strong = sum(value is not None and value <= -7.0 for value in unique.values())
            if (pair.get("unique_chemical_task_count") != len(unique)
                    or pair.get("strong_binding_denominator") != len(unique)
                    or pair.get("strong_binding_count") != strong):
                fail("药对独立化学任务或阈值计数不一致")
            fraction = numeric(pair.get("strong_binding_fraction"), "结合能阈值比例")
            if not unique or fraction is None or abs(fraction - strong / len(unique)) > 1e-12:
                fail("药对结合能阈值比例不一致")
            if pair.get("same_compound_shared_by_both_herbs") is not (len(unique) < len(assignments)):
                fail("共享成分提示与独立化学任务数量不一致")
            if pair.get("independent_herb_synergy_evidence") is not False:
                fail("对接不能标记为独立药对协同证据")
        if report.get("unique_chemical_task_count") != len(unique_all):
            fail("总独立化学任务计数不一致")
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
        fail(f"对接证据结构无效：{exc}")
    if errors:
        return {**report, "recorded_status": report.get("status"), "status": "evidence_invalid", "integrity_verified": False}, errors
    return {**report, "integrity_verified": True}, []
