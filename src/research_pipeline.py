from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_SUCCESS = {"passed", "completed", "reused", "adopted_existing"}
TEMPLATE_PATTERN = re.compile(r"\{([A-Za-z0-9_.-]+)\}")
RESERVED_TEMPLATE_PREFIXES = (
    "project_root",
    "python",
    "run_id",
    "run_root",
    "run_artifacts_root",
    "config_path",
    "config_snapshot",
    "resolved_config",
    "job.",
    "paths.",
    "variables.",
    "scoring.",
)
FORBIDDEN_CONFIG_KEY_FRAGMENTS = ("password", "passwd", "cookie", "secret", "access_token")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_slug(value: str) -> str:
    result = "".join(character.lower() if character.isalnum() else "-" for character in value)
    return "-".join(part for part in result.split("-") if part) or "research-run"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def resolve_path(project_root: Path, value: str) -> Path:
    expanded = value.replace("{project_root}", str(project_root))
    path = Path(expanded)
    return path if path.is_absolute() else project_root / path


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def render_command(project_root: Path, values: list[str]) -> list[str]:
    replacements = {
        "{python}": sys.executable,
        "{project_root}": str(project_root),
    }
    rendered: list[str] = []
    for value in values:
        result = value
        for key, replacement in replacements.items():
            result = result.replace(key, replacement)
        rendered.append(result)
    return rendered


def expand_artifact(project_root: Path, specification: str) -> list[Path]:
    resolved = resolve_path(project_root, specification)
    if any(symbol in specification for symbol in "*?["):
        anchor = project_root if not Path(specification).is_absolute() else Path(resolved.anchor)
        try:
            pattern = str(resolved.relative_to(anchor))
            return sorted(path for path in anchor.glob(pattern) if path.is_file())
        except ValueError:
            return []
    if resolved.is_file():
        return [resolved]
    if resolved.is_dir():
        return sorted(path for path in resolved.rglob("*") if path.is_file())
    return []


def artifact_records(project_root: Path, specifications: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for specification in specifications:
        matches = expand_artifact(project_root, specification)
        if not matches:
            records.append({"specification": specification, "exists": False})
            continue
        for path in matches:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                relative = str(resolved.relative_to(project_root.resolve()))
            except ValueError:
                relative = str(resolved)
            stat = resolved.stat()
            records.append({
                "specification": specification,
                "exists": True,
                "path": relative,
                "absolute_path": str(resolved),
                "bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "sha256": sha256(resolved),
            })
    return records


def artifacts_complete(records: list[dict[str, Any]]) -> bool:
    return bool(records) and all(record.get("exists") for record in records)


def artifact_signature(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                # A copied run snapshot must compare equal to the original project artifact.
                # This keeps resume/reuse compatible with validation-v2, whose status files
                # predate run-local snapshots.
                "path": (
                    record.get("origin_path")
                    or record.get("path")
                    or record.get("absolute_path")
                ),
                "bytes": record.get("bytes"),
                "sha256": record.get("sha256"),
            }
            for record in records
            if record.get("exists")
        ],
        key=lambda item: str(item.get("path")),
    )


def validate_config(config: dict[str, Any]) -> None:
    def reject_embedded_credentials(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = str(key).casefold()
                if any(fragment in normalized for fragment in FORBIDDEN_CONFIG_KEY_FRAGMENTS):
                    raise ValueError(
                        f"任务配置不得保存密码、Cookie或访问令牌：{prefix}{key}"
                    )
                reject_embedded_credentials(nested, f"{prefix}{key}.")
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                reject_embedded_credentials(nested, f"{prefix}{index}.")

    reject_embedded_credentials(config)
    job = config.get("job", {})
    disease = job.get("disease", {})
    herbs = job.get("herbs", [])
    if not disease.get("name_en") and not disease.get("name_cn"):
        raise ValueError("任务配置缺少疾病名称")
    if len(herbs) < 2:
        raise ValueError("至少需要两味候选药材")
    if len(herbs) != len(set(herbs)):
        raise ValueError("候选药材中存在重复名称")
    combination_size = int(job.get("combination_size", 2))
    supported_sizes = config.get("supported_combination_sizes", [2])
    if combination_size not in supported_sizes:
        raise ValueError(f"当前流程配置只支持组合大小：{supported_sizes}")
    scoring = config.get("scoring", {})
    weights = scoring.get("weights", {})
    if weights:
        total = sum(float(value) for value in weights.values())
        if abs(total - 1.0) > 1e-8:
            raise ValueError(f"评分权重之和必须为1，当前为{total}")
    step_ids = [step.get("id") for step in config.get("steps", [])]
    if not step_ids or any(not value for value in step_ids):
        raise ValueError("任务配置至少需要一个带id的步骤")
    if len(step_ids) != len(set(step_ids)):
        raise ValueError("步骤id不能重复")
    artifact_policy = config.get("artifact_policy", {})
    if artifact_policy.get("mode", "copy") not in {"copy", "reference"}:
        raise ValueError("artifact_policy.mode 只支持 copy 或 reference")


class ResearchPipeline:
    def __init__(
        self,
        project_root: Path,
        config_path: Path,
        run_id: str | None = None,
        resume: bool = False,
        force_steps: set[str] | None = None,
        dry_run: bool = False,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config_path = config_path.resolve()
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        validate_config(self.config)
        job_name = self.config.get("job", {}).get("name", "research-run")
        generated_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{safe_slug(job_name)}"
        self.run_id = run_id or generated_id
        if (
            not self.run_id
            or self.run_id in {".", ".."}
            or "/" in self.run_id
            or "\\" in self.run_id
        ):
            raise ValueError("运行编号不得包含路径分隔符或路径跳转")
        runs_root = (self.project_root / "runs").resolve()
        self.run_root = (runs_root / self.run_id).resolve()
        if not is_relative_to(self.run_root, runs_root):
            raise ValueError("运行目录必须位于项目 runs 目录内")
        self.logs_root = self.run_root / "logs"
        self.manifest_path = self.run_root / "run_manifest.json"
        self.config_snapshot_path = self.run_root / "job_config_snapshot.json"
        self.resolved_config_path = self.run_root / "job_config_resolved.json"
        self.template_values = self.build_template_values()
        self.resume = resume
        self.force_steps = force_steps or set()
        self.dry_run = dry_run
        self.manifest: dict[str, Any]

    def build_template_values(self) -> dict[str, str]:
        values: dict[str, str] = {
            "python": sys.executable,
            "project_root": str(self.project_root),
            "run_id": self.run_id,
            "run_root": str(self.run_root),
            "run_artifacts_root": str(self.run_root / "artifacts"),
            "config_path": str(self.config_path),
            "config_snapshot": str(self.config_snapshot_path),
            "resolved_config": str(self.resolved_config_path),
        }

        def add(prefix: str, value: Any) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    add(f"{prefix}.{key}" if prefix else str(key), nested)
            elif isinstance(value, list):
                values[prefix] = json.dumps(value, ensure_ascii=False)
                if all(not isinstance(item, (dict, list)) for item in value):
                    values[f"{prefix}_csv"] = ",".join(str(item) for item in value)
            elif value is None:
                values[prefix] = ""
            else:
                values[prefix] = str(value)

        for section in ("job", "paths", "variables", "scoring"):
            if section in self.config:
                add(section, self.config[section])
        return values

    def render_text(self, value: str) -> str:
        result = value
        for _ in range(10):
            rendered = TEMPLATE_PATTERN.sub(
                lambda match: self.template_values.get(match.group(1), match.group(0)),
                result,
            )
            if rendered == result:
                break
            result = rendered
        unresolved = [
            match.group(1)
            for match in TEMPLATE_PATTERN.finditer(result)
            if match.group(1).startswith(RESERVED_TEMPLATE_PREFIXES)
        ]
        if unresolved:
            raise ValueError("配置中存在未解析占位符：" + "、".join(sorted(set(unresolved))))
        return result

    def render_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.render_text(value)
        if isinstance(value, list):
            return [self.render_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self.render_value(nested) for key, nested in value.items()}
        return value

    def artifact_policy(self) -> tuple[str, Path]:
        policy = self.config.get("artifact_policy", {})
        mode = str(policy.get("mode", "copy"))
        root_value = str(policy.get("root", "{run_artifacts_root}"))
        root = resolve_path(self.project_root, self.render_text(root_value)).resolve()
        if mode == "copy" and not is_relative_to(root, self.run_root):
            raise ValueError("copy 模式的证据快照目录必须位于本次 run 目录内")
        return mode, root

    def snapshot_artifacts(
        self,
        step_id: str,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        mode, snapshot_root = self.artifact_policy()
        if mode == "reference":
            return records
        copied: list[dict[str, Any]] = []
        project_root = self.project_root.resolve()
        for record in records:
            if not record.get("exists"):
                copied.append(record)
                continue
            source = Path(str(record["absolute_path"])).resolve()
            if is_relative_to(source, self.run_root):
                result = dict(record)
                result["origin_path"] = record.get("origin_path") or record.get("path")
                result["storage"] = "run_native"
                copied.append(result)
                continue
            if is_relative_to(source, project_root):
                origin_relative = source.relative_to(project_root)
            else:
                origin_relative = Path("external") / sha256(source)[:12] / source.name
            destination = (snapshot_root / step_id / origin_relative).resolve()
            if not is_relative_to(destination, self.run_root):
                raise ValueError(f"证据快照路径越界：{destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            source_hash = str(record["sha256"])
            if not destination.exists() or sha256(destination) != source_hash:
                shutil.copy2(source, destination)
            copied_hash = sha256(destination)
            if copied_hash != source_hash:
                raise IOError(f"证据快照校验失败：{source}")
            result = dict(record)
            result["origin_path"] = record.get("path") or str(source)
            result["origin_absolute_path"] = str(source)
            result["path"] = str(destination.relative_to(project_root))
            result["absolute_path"] = str(destination)
            result["storage"] = "run_snapshot"
            copied.append(result)
        return copied

    def initialize(self) -> None:
        if self.run_root.exists() and not self.resume:
            raise FileExistsError(f"运行目录已经存在：{self.run_root}")
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.logs_root.mkdir(parents=True, exist_ok=True)
        snapshot_path = self.config_snapshot_path
        if not snapshot_path.exists():
            shutil.copy2(self.config_path, snapshot_path)
        if not self.resolved_config_path.exists():
            write_json(self.resolved_config_path, self.render_value(self.config))
        if self.resume and self.manifest_path.exists():
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            expected_hash = self.manifest.get("config_snapshot_sha256")
            if expected_hash and sha256(snapshot_path) != expected_hash:
                raise ValueError("断点续跑时的配置快照已改变，请创建新运行")
            if expected_hash and sha256(self.config_path) != expected_hash:
                raise ValueError("断点续跑必须使用与原运行完全一致的任务配置")
            self.manifest["resumed_at"] = utc_now()
            return
        self.manifest = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "status": "running",
            "started_at": utc_now(),
            "finished_at": None,
            "project_root": str(self.project_root),
            "config_source": str(self.config_path),
            "config_snapshot": str(snapshot_path.relative_to(self.project_root)),
            "config_snapshot_sha256": sha256(snapshot_path),
            "resolved_config": str(self.resolved_config_path.relative_to(self.project_root)),
            "resolved_config_sha256": sha256(self.resolved_config_path),
            "artifact_policy": self.config.get(
                "artifact_policy", {"mode": "copy", "root": "{run_artifacts_root}"}
            ),
            "job": self.config["job"],
            "scoring": self.config.get("scoring", {}),
            "data_sources": self.config.get("data_sources", []),
            "environment": {
                "python": sys.version,
                "python_executable": sys.executable,
                "platform": platform.platform(),
                "hostname": platform.node(),
                "working_directory": os.getcwd(),
            },
            "steps": [],
            "limitations": self.config.get("limitations", []),
        }
        write_json(self.manifest_path, self.manifest)

    def previous_step(self, step_id: str) -> dict[str, Any] | None:
        return next((step for step in self.manifest.get("steps", []) if step["id"] == step_id), None)

    def upstream_artifacts(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for step in self.manifest.get("steps", []):
            if step.get("status") in TERMINAL_SUCCESS:
                records.extend(step.get("artifacts", []))
        return records

    def execution_fingerprint(
        self,
        step: dict[str, Any],
        code_files: list[dict[str, str]],
        inputs: list[dict[str, Any]],
    ) -> str:
        relevant_step = {
            key: value
            for key, value in step.items()
            if key not in {"reuse_if_artifacts_exist", "adopt_existing_artifacts"}
        }
        payload = {
            "job": self.config.get("job"),
            "scoring": self.config.get("scoring"),
            "pipeline_profile": self.config.get("pipeline_profile"),
            "step": relevant_step,
            "code_files": code_files,
            "inputs": artifact_signature(inputs),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def find_reuse_attestation(
        self,
        step_id: str,
        fingerprint: str,
        current_artifacts: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        runs_root = self.project_root / "runs"
        current_signature = artifact_signature(current_artifacts)
        if not runs_root.exists():
            return None
        for status_path in sorted(runs_root.glob(f"*/steps/{step_id}/status.json"), reverse=True):
            if self.run_root in status_path.parents:
                continue
            try:
                previous = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if previous.get("status") not in TERMINAL_SUCCESS:
                continue
            if previous.get("execution_fingerprint") != fingerprint:
                continue
            if artifact_signature(previous.get("artifacts", [])) != current_signature:
                continue
            return {
                "run_id": status_path.parents[2].name,
                "status_file": str(status_path.relative_to(self.project_root)),
            }
        return None

    def replace_step(self, result: dict[str, Any]) -> None:
        steps = [step for step in self.manifest.get("steps", []) if step["id"] != result["id"]]
        steps.append(result)
        configured_order = {step["id"]: index for index, step in enumerate(self.config["steps"])}
        steps.sort(key=lambda item: configured_order.get(item["id"], 9999))
        self.manifest["steps"] = steps
        write_json(self.manifest_path, self.manifest)
        step_root = self.run_root / "steps" / result["id"]
        step_root.mkdir(parents=True, exist_ok=True)
        write_json(step_root / "status.json", result)

    def run_step(self, step: dict[str, Any]) -> dict[str, Any]:
        step_id = step["id"]
        previous_result = self.previous_step(step_id)
        started = time.monotonic()
        result: dict[str, Any] = {
            "id": step_id,
            "name": step.get("name", step_id),
            "type": step.get("type", "command"),
            "status": "running",
            "started_at": utc_now(),
            "finished_at": None,
            "duration_seconds": None,
            "required": bool(step.get("required", True)),
            "notes": step.get("notes", ""),
        }
        dependencies = list(step.get("depends_on", []))
        if dependencies:
            known_statuses = {
                item["id"]: item.get("status") for item in self.manifest.get("steps", [])
            }
            unsatisfied = [
                dependency
                for dependency in dependencies
                if known_statuses.get(dependency) not in TERMINAL_SUCCESS
            ]
            if unsatisfied:
                result["status"] = "blocked"
                result["action_required"] = "先完成依赖步骤：" + "、".join(unsatisfied)
                result["finished_at"] = utc_now()
                result["duration_seconds"] = round(time.monotonic() - started, 3)
                return result
        self.replace_step(result)
        specifications = [self.render_text(value) for value in step.get("artifacts", [])]
        before = artifact_records(self.project_root, specifications)
        result["artifacts_before"] = before
        step_type = step.get("type", "command")

        if step_type == "checkpoint":
            satisfied = bool(step.get("satisfied", False))
            result["status"] = "passed" if satisfied else "needs_input"
            result["action_required"] = step.get("action_required", "") if not satisfied else ""
            result["artifacts"] = before
        elif step_type in {"verify_artifacts", "input_gate"}:
            complete = artifacts_complete(before)
            if complete:
                result["status"] = "passed"
            else:
                result["status"] = "needs_input" if step_type == "input_gate" else "failed"
                result["action_required"] = step.get("action_required", "补齐缺失输入文件")
            result["artifacts"] = before
        elif step_type == "command":
            command = [self.render_text(value) for value in step.get("command", [])]
            result["command"] = command
            result["code_files"] = []
            for argument in command:
                candidate = Path(argument)
                if not candidate.is_absolute():
                    candidate = self.project_root / candidate
                if candidate.is_file() and candidate.suffix.lower() == ".py":
                    result["code_files"].append({
                        "path": str(candidate.resolve()),
                        "sha256": sha256(candidate),
                    })
            input_specs = [self.render_text(value) for value in step.get("input_artifacts", [])]
            inputs = (
                artifact_records(self.project_root, input_specs)
                if input_specs
                else self.upstream_artifacts()
            )
            result["input_artifacts"] = inputs
            fingerprint = self.execution_fingerprint(step, result["code_files"], inputs)
            result["execution_fingerprint"] = fingerprint
            attestation = self.find_reuse_attestation(step_id, fingerprint, before)
            resume_valid = (
                self.resume
                and previous_result is not None
                and previous_result.get("status") in TERMINAL_SUCCESS
                and previous_result.get("execution_fingerprint") == fingerprint
                and artifact_signature(previous_result.get("artifacts", []))
                == artifact_signature(before)
                and step_id not in self.force_steps
            )
            if resume_valid:
                result = previous_result
                result["resume_decision"] = (
                    "previous success retained after fingerprint and artifact recheck"
                )
                return result
            can_reuse = (
                step_id not in self.force_steps
                and bool(step.get("reuse_if_artifacts_exist", False))
                and artifacts_complete(before)
                and attestation is not None
            )
            if can_reuse:
                result["status"] = "reused"
                result["reuse_reason"] = "matching execution fingerprint and output hashes found"
                result["reuse_attestation"] = attestation
                result["artifacts"] = before
            elif (
                step_id not in self.force_steps
                and artifacts_complete(before)
                and bool(step.get("adopt_existing_artifacts", False))
            ):
                result["status"] = "adopted_existing"
                result["reuse_reason"] = (
                    "one-time migration of historical outputs; hashes recorded for future reuse"
                )
                result["warnings"] = [
                    "Historical outputs predate the unified runner; lineage is supported by their own reports, not a prior runner fingerprint."
                ]
                result["artifacts"] = before
            else:
                stdout_path = self.logs_root / f"{step_id}.stdout.log"
                stderr_path = self.logs_root / f"{step_id}.stderr.log"
                result["stdout_log"] = str(stdout_path.relative_to(self.project_root))
                result["stderr_log"] = str(stderr_path.relative_to(self.project_root))
                if self.dry_run:
                    stdout_path.write_text("DRY RUN: " + subprocess.list2cmdline(command), encoding="utf-8")
                    stderr_path.write_text("", encoding="utf-8")
                    result["status"] = "planned"
                    result["exit_code"] = None
                else:
                    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
                        "w", encoding="utf-8"
                    ) as stderr_handle:
                        process_environment = os.environ.copy()
                        process_environment.update({
                            "RESEARCH_RUN_ID": self.run_id,
                            "RESEARCH_RUN_ROOT": str(self.run_root),
                            "RESEARCH_JOB_CONFIG": str(self.config_snapshot_path),
                            "RESEARCH_JOB_CONFIG_RESOLVED": str(self.resolved_config_path),
                        })
                        working_directory = resolve_path(
                            self.project_root,
                            self.render_text(str(step.get("working_directory", "{project_root}"))),
                        ).resolve()
                        result["working_directory"] = str(working_directory)
                        result["runtime_environment"] = {
                            key: process_environment[key]
                            for key in (
                                "RESEARCH_RUN_ID",
                                "RESEARCH_RUN_ROOT",
                                "RESEARCH_JOB_CONFIG",
                                "RESEARCH_JOB_CONFIG_RESOLVED",
                            )
                        }
                        process = subprocess.run(
                            command,
                            cwd=working_directory,
                            stdout=stdout_handle,
                            stderr=stderr_handle,
                            text=True,
                            check=False,
                            env=process_environment,
                        )
                    result["exit_code"] = process.returncode
                    after = artifact_records(self.project_root, specifications)
                    result["artifacts"] = after
                    needs_input_exit_codes = {
                        int(value) for value in step.get("needs_input_exit_codes", [])
                    }
                    if process.returncode in needs_input_exit_codes:
                        result["status"] = "needs_input"
                        result["action_required"] = step.get(
                            "action_required",
                            "命令已完成检查，但仍有人工输入或外部步骤未完成。",
                        )
                    elif process.returncode != 0:
                        result["status"] = "failed"
                    elif specifications and not artifacts_complete(after):
                        result["status"] = "failed"
                        result["error"] = "命令退出正常，但必要输出不完整"
                    else:
                        result["status"] = "completed"
        else:
            result["status"] = "failed"
            result["error"] = f"未知步骤类型：{step_type}"

        source_artifacts = list(result.get("artifacts", []))
        result["source_artifacts"] = source_artifacts
        if result["status"] in TERMINAL_SUCCESS:
            result["artifacts"] = self.snapshot_artifacts(step_id, source_artifacts)

        result["finished_at"] = utc_now()
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        return result

    def seal_run(self, report_path: Path) -> None:
        checksum_path = self.run_root / "checksums.sha256"
        seal_path = self.run_root / "seal.json"
        entries: list[str] = []
        for path in sorted(self.run_root.rglob("*")):
            if not path.is_file() or path in {checksum_path, seal_path}:
                continue
            relative = path.relative_to(self.run_root).as_posix()
            entries.append(f"{sha256(path)}  {relative}")
        checksum_path.write_text("\n".join(entries) + "\n", encoding="utf-8")
        seal = {
            "run_id": self.run_id,
            "sealed_at": utc_now(),
            "run_status": self.manifest["status"],
            "results_finality": self.manifest.get("results_finality"),
            "manifest_sha256": sha256(self.manifest_path),
            "report_sha256": sha256(report_path),
            "checksums_file_sha256": sha256(checksum_path),
            "credentials_stored": False,
        }
        write_json(seal_path, seal)

    def build_report(self) -> Path:
        report_path = self.run_root / "全过程证据报告.md"
        job = self.manifest["job"]
        disease = job.get("disease", {})
        lines = [
            f"# 科研筛选全过程证据报告：{self.run_id}",
            "",
            f"- 运行状态：`{self.manifest['status']}`",
            f"- 疾病：{disease.get('name_cn', '')} / {disease.get('name_en', '')}",
            f"- 物种：{disease.get('species', '')}（Taxonomy ID: {disease.get('taxon_id', '')}）",
            f"- 候选药材：{'、'.join(job.get('herbs', []))}",
            f"- 组合大小：{job.get('combination_size', 2)}",
            f"- 开始时间：{self.manifest.get('started_at')}",
            f"- 完成时间：{self.manifest.get('finished_at')}",
            "",
            "## 步骤状态",
            "",
            "| 步骤 | 状态 | 用时（秒） | 证明文件数 | 需要人工处理 |",
            "|---|---:|---:|---:|---|",
        ]
        for step in self.manifest.get("steps", []):
            artifacts = [item for item in step.get("artifacts", []) if item.get("exists")]
            action = str(step.get("action_required", "")).replace("|", "\\|")
            lines.append(
                f"| {step['name']} | {step['status']} | {step.get('duration_seconds', '')} | "
                f"{len(artifacts)} | {action} |"
            )
        lines.extend(["", "## 数据来源", ""])
        for source in self.manifest.get("data_sources", []):
            lines.append(
                f"- **{source.get('name', '')}**：{source.get('purpose', '')}；"
                f"版本/日期：{source.get('version', '运行时记录')}；{source.get('url', '')}"
            )
        lines.extend(["", "## 评分权重", ""])
        for key, value in self.manifest.get("scoring", {}).get("weights", {}).items():
            lines.append(f"- {key}：{float(value) * 100:g}%")
        lines.extend(["", "## 限制与不能证明的内容", ""])
        for limitation in self.manifest.get("limitations", []):
            lines.append(f"- {limitation}")
        lines.extend([
            "",
            "## 审计说明",
            "",
            "每个步骤的命令、退出码、标准输出、错误日志、产物路径、文件大小和 SHA-256 均保存在 `run_manifest.json`。",
            "密码、Cookie、验证码和网站登录凭证不得写入运行目录。",
        ])
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report_path

    def run(self) -> dict[str, Any]:
        self.initialize()
        for step in self.config["steps"]:
            result = self.run_step(step)
            self.replace_step(result)
            if result["status"] == "failed" and result.get("required", True):
                break
            if (
                result["status"] == "needs_input"
                and result.get("required", True)
                and bool(step.get("halt_on_needs_input", step.get("type") == "input_gate"))
            ):
                break
        statuses = [step["status"] for step in self.manifest.get("steps", []) if step.get("required", True)]
        if any(status == "failed" for status in statuses):
            final_status = "failed"
        elif any(status in {"needs_input", "blocked"} for status in statuses):
            final_status = "needs_input"
        elif any(status == "planned" for status in statuses):
            final_status = "dry_run"
        else:
            final_status = "completed"
        self.manifest["status"] = final_status
        self.manifest["results_finality"] = (
            "interim" if final_status in {"needs_input", "failed", "dry_run"} else "pipeline_validated"
        )
        self.manifest["finished_at"] = utc_now()
        report_path = self.build_report()
        self.manifest["report"] = str(report_path.relative_to(self.project_root))
        write_json(self.manifest_path, self.manifest)
        self.seal_run(report_path)
        latest = {
            "run_id": self.run_id,
            "status": final_status,
            "manifest": str(self.manifest_path.relative_to(self.project_root)),
            "report": str(report_path.relative_to(self.project_root)),
            "updated_at": utc_now(),
        }
        write_json(self.project_root / "runs" / "latest.json", latest)
        return self.manifest
