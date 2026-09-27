from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from src.job_builder import (
    build_task_config,
    candidate_csv_text,
    load_config,
    normalize_herbs,
    safe_slug,
)
from src.research_pipeline import validate_config


ALLOWED_DISEASE_EXPORT_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xls"}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def make_task_id(disease_cn: str, disease_en: str, herb_count: int) -> str:
    disease_slug = safe_slug(disease_en or disease_cn).casefold()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return f"{disease_slug}-{herb_count}herbs-{stamp}"


def create_task(
    *,
    project_root: Path,
    template_path: Path,
    disease_cn: str,
    disease_en: str,
    species: str,
    herbs: str | list[str],
    disease_source: str = "open_targets",
    upload_name: str = "",
    upload_bytes: bytes = b"",
) -> dict[str, Any]:
    normalized_herbs = normalize_herbs(herbs)
    if not (disease_cn.strip() or disease_en.strip()):
        raise ValueError("至少填写一个疾病名称")
    if len(normalized_herbs) < 2:
        raise ValueError("至少输入两味不重复中药")
    if disease_source not in {"open_targets", "genecards_upload"}:
        raise ValueError("不支持的疾病靶点来源")
    suffix = Path(upload_name).suffix.casefold() if upload_name else ""
    if disease_source == "genecards_upload":
        if suffix not in ALLOWED_DISEASE_EXPORT_SUFFIXES:
            raise ValueError("GeneCards文件只支持 CSV、TSV、TXT、XLSX 或 XLS")
        if not upload_bytes:
            raise ValueError("GeneCards导出文件不能为空")

    task_id = make_task_id(disease_cn, disease_en, len(normalized_herbs))
    template = load_config(template_path)
    config = build_task_config(
        template,
        disease_cn=disease_cn,
        disease_en=disease_en,
        species=species,
        taxon_id=9606,
        herbs=normalized_herbs,
        task_id=task_id,
    )
    validate_config(config)
    config["job"]["disease_target_source"] = disease_source
    disease_step = next(
        step for step in config["steps"] if step.get("id") == "01_disease_targets"
    )
    if disease_source == "open_targets":
        disease_step.update({
            "name": "Open Targets疾病靶点自动获取与标准化",
            "artifacts": [
                "{paths.disease_targets_root}/open_targets_raw/search.json",
                "{paths.disease_targets_root}/open_targets_raw/page_*.json",
                "{paths.disease_targets_root}/disease_targets_normalized.csv",
                "{paths.disease_targets_root}/import_report.json",
            ],
            "action_required": "官方API暂时不可用时自动重试；不得用虚构靶点继续。",
        })
    validate_config(config)

    task_root = project_root / "config" / "tasks" / task_id
    disease_root = project_root / config["paths"]["disease_targets_root"]
    task_root.mkdir(parents=True, exist_ok=False)
    disease_root.mkdir(parents=True, exist_ok=True)
    config_path = task_root / "pipeline.json"
    candidate_path = task_root / "candidate_herbs.csv"
    source_path = disease_root / f"uploaded_genecards{suffix}" if upload_bytes else None
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    candidate_path.write_text(candidate_csv_text(normalized_herbs), encoding="utf-8-sig")
    if source_path is not None:
        source_path.write_bytes(upload_bytes)

    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{task_id}"
    status_path = project_root / "data" / "task_status" / f"{task_id}.json"
    log_path = project_root / "logs" / f"homepage_{task_id}.log"
    status = {
        "task_id": task_id,
        "run_id": run_id,
        "status": "queued",
        "stage": "等待后台进程启动",
        "config_path": str(config_path.relative_to(project_root)),
        "candidate_path": str(candidate_path.relative_to(project_root)),
        "disease_source": disease_source,
        "uploaded_file": str(source_path.relative_to(project_root)) if source_path else None,
        "status_path": str(status_path.relative_to(project_root)),
        "log_path": str(log_path.relative_to(project_root)),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(status_path, status)
    return status


def launch_task(project_root: Path, status: dict[str, Any]) -> int:
    status_path = project_root / str(status["status_path"])
    command = [
        sys.executable,
        str(project_root / "scripts" / "run_homepage_task.py"),
        "--status", str(status_path),
    ]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        cwd=str(project_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
        close_fds=os.name != "nt",
    )
    updated = load_task_status(project_root, str(status["task_id"])) or dict(status)
    updated["pid"] = process.pid
    if updated.get("status") == "queued":
        updated["status"] = "running"
        updated["stage"] = "后台进程已启动"
    write_json(status_path, updated)
    return process.pid


def load_task_status(project_root: Path, task_id: str) -> dict[str, Any]:
    path = project_root / "data" / "task_status" / f"{task_id}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

