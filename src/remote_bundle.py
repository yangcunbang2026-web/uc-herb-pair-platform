"""Task-only, fingerprinted evidence transfer. Never export credentials or jobs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import threading
import zipfile
import uuid

MAX_FILES = 12000
MAX_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_BYTES = 80 * 1024 * 1024
ALLOWED_SUFFIXES = {".json", ".csv", ".tsv", ".txt", ".pdb", ".pdbqt", ".sdf", ".cif", ".log", ".png", ".svg", ".md", ".fasta", ".fa", ".xml"}
_BUNDLE_PUBLISH_LOCK = threading.Lock()


def _task_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"remote-[0-9a-f]{32}", value):
        raise ValueError("任务编号无效")
    return value


def safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("证据路径必须为规范相对路径")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or PurePosixPath(value).is_absolute():
        raise ValueError("证据路径越界")
    return value


def build_task_bundle(project_root: Path, status: dict) -> Path:
    root = project_root.resolve()
    task_id = _task_id(status.get("task_id"))
    analysis_name = safe_relative(str(status.get("analysis_root", "")).replace("\\", "/"))
    rules_name = safe_relative(str(status.get("rules_path", "")).replace("\\", "/"))
    analysis = (root / analysis_name).resolve()
    rules = (root / rules_name).resolve()
    task_analysis = root / "data" / "formal_analysis" / task_id
    if not analysis.is_relative_to(task_analysis) or not analysis.is_dir():
        raise ValueError("分析目录不是本任务的独立目录")
    if not rules.is_relative_to(root / "config" / "tasks" / task_id) and not rules.is_relative_to(task_analysis):
        raise ValueError("规则文件不是本任务的文件")
    paths: dict[str, Path] = {}
    pending: list[Path] = []
    total_bytes = 0

    def add(path: Path, expected_hash: str | None = None) -> None:
        nonlocal total_bytes
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("证据不存在或越出项目目录")
        name = resolved.relative_to(root).as_posix()
        permitted = any(name.startswith(prefix) for prefix in (
            "data/formal_analysis/", "data/formal_inputs/", "data/channel_tests/tcmsp/", "config/tasks/",
        )) or name == "src/staged_formula.py"
        if not permitted or (resolved.suffix.lower() not in ALLOWED_SUFFIXES and name != "src/staged_formula.py"):
            raise ValueError(f"不允许导出的文件类型或目录：{name}")
        if any(term in resolved.name.casefold() for term in ("secret", "credential", "access_code", ".env", "payload.json")):
            raise ValueError("拒绝导出私密配置")
        if expected_hash and hashlib.sha256(resolved.read_bytes()).hexdigest() != expected_hash.lower():
            raise ValueError(f"来源指纹不一致：{name}")
        if name in paths:
            return
        total_bytes += resolved.stat().st_size
        if total_bytes > MAX_BYTES or len(paths) >= MAX_FILES:
            raise ValueError("证据包超过传输上限，请在研究端导出")
        paths[name] = resolved
        if resolved.suffix == ".json":
            pending.append(resolved)

    def references(obj: object) -> None:
        if isinstance(obj, dict):
            value = obj.get("relative_path") or obj.get("path")
            expected = obj.get("sha256")
            if isinstance(value, str) and isinstance(expected, str) and re.fullmatch(r"[0-9a-fA-F]{64}", expected):
                value = value.replace("\\", "/")
                add(root / value, expected)
            for child in obj.values():
                references(child)
        elif isinstance(obj, list):
            for child in obj:
                references(child)

    for path in sorted(analysis.rglob("*")):
        if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES:
            add(path)
    add(rules)
    if (rules.parent / "pipeline.json").is_file():
        add(rules.parent / "pipeline.json")
    if (root / "src/staged_formula.py").is_file():
        add(root / "src/staged_formula.py")
    for field in ("target_grades_csv", "core_pathways_csv", "disease_reference_csv", "ppi_edges_csv", "ppi_metadata_json", "definition_manifest_json"):
        value = json.loads(rules.read_text(encoding="utf-8-sig")).get(field)
        if isinstance(value, str) and (root / value).is_file():
            add(root / value)
    while pending:
        path = pending.pop()
        try:
            references(json.loads(path.read_text(encoding="utf-8-sig")))
        except json.JSONDecodeError:
            continue
    manifest = {
        "schema_version": 1, "task_id": task_id, "status": status.get("status"),
        "analysis_root": analysis_name, "rules_path": rules_name,
        "disease_cn": status.get("disease_cn", ""), "disease_en": status.get("disease_en", ""),
        "files": [{"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
                  for name, path in sorted(paths.items())],
    }
    destination = root / "private" / "remote_compute" / "bundles"
    destination.mkdir(parents=True, exist_ok=True)
    manifest_body = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    bundle_key = hashlib.sha256(manifest_body.encode("utf-8")).hexdigest()[:16]
    bundle = destination / f"{task_id}-{bundle_key}.zip"
    if bundle.is_file():
        return bundle
    temporary = destination / f"{task_id}-{uuid.uuid4().hex}.tmp"
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("bundle_manifest.json", manifest_body)
            for name, path in sorted(paths.items()):
                archive.write(path, name)
        # Another request may have published this content-addressed archive
        # while it was being built. Never replace an existing final ZIP: on
        # Windows it may already be open for a concurrent download.
        with _BUNDLE_PUBLISH_LOCK:
            if not bundle.is_file():
                temporary.replace(bundle)
    finally:
        # This request owns exactly this UUID-named temporary file; other
        # requests' archives and temporary files are never removed.
        temporary.unlink(missing_ok=True)
    return bundle


def unpack_task_bundle(archive_path: Path, destination: Path, task_id: str) -> dict:
    """Validate every member and fingerprint before writing in an empty cache."""
    task_id = _task_id(task_id)
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("证据缓存目录必须为空")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("证据包超过下载上限")
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_FILES + 1 or sum(item.file_size for item in members) > MAX_BYTES:
            raise ValueError("证据包超过解压上限")
        names = [safe_relative(item.filename) for item in members]
        if len(names) != len(set(names)) or any(stat.S_ISLNK(item.external_attr >> 16) for item in members):
            raise ValueError("证据包包含重复或符号链接文件")
        manifest = json.loads(archive.read("bundle_manifest.json"))
        if manifest.get("schema_version") != 1 or manifest.get("task_id") != task_id:
            raise ValueError("证据包任务身份不匹配")
        listed = {safe_relative(row["path"]): row for row in manifest["files"]}
        if len(listed) != len(manifest["files"]) or set(names) != {"bundle_manifest.json", *listed}:
            raise ValueError("证据文件与清单不一致")
        for field in ("analysis_root", "rules_path"):
            name = safe_relative(manifest.get(field))
            allowed = (f"data/formal_analysis/{task_id}/", f"config/tasks/{task_id}/")
            if not any(name == prefix.rstrip("/") or name.startswith(prefix) for prefix in allowed):
                raise ValueError("证据入口不属于所选任务")
        for name, row in listed.items():
            body = archive.read(name)
            if len(body) != row["bytes"] or hashlib.sha256(body).hexdigest() != row["sha256"]:
                raise ValueError("证据文件指纹不一致")
        destination.mkdir(parents=True, exist_ok=True)
        for name in names:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as handle:
                handle.write(archive.read(name))
    return manifest
