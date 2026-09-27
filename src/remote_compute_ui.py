"""Small remote-task panel, preserving the existing seven evidence chapters."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import tempfile
import uuid

import streamlit as st

from src.job_builder import normalize_herbs
from src.remote_bundle import unpack_task_bundle
from src.remote_compute_client import RemoteComputeClient, RemoteComputeError

STATE_NAMES = {"queued": "排队中", "running": "电脑正在计算", "needs_input": "需要补充证据", "completed": "计算完成", "failed": "执行失败，可查看原因", "interrupted": "电脑后台中断，可恢复"}
UPLOAD_NAMES = {"genecards": "GeneCards疾病靶点表", "core_pathways": "核心通路定义表", "target_grades": "靶点分级表"}
UPLOAD_TYPES = {"genecards": ["csv", "tsv", "txt"], "core_pathways": ["csv"], "target_grades": ["csv"]}


def _request_identity(payload: dict) -> str:
    """Retry the same input once; changed inputs must create a new request."""
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    if st.session_state.get("remote_request_fingerprint") != fingerprint:
        st.session_state["remote_request_fingerprint"] = fingerprint
        st.session_state["remote_request_id"] = str(uuid.uuid4())
    return st.session_state.setdefault("remote_request_id", str(uuid.uuid4()))


def _uploads(files: dict) -> dict:
    result = {}
    for role, file in files.items():
        if file is not None:
            body = file.getvalue()
            if len(body) > 3 * 1024 * 1024:
                raise ValueError("每份证据文件最多3MB")
            result[role] = {"name": file.name, "content_base64": base64.b64encode(body).decode("ascii")}
    return result


def _load_result(client: RemoteComputeClient, task_id: str) -> None:
    temporary = Path(tempfile.mkdtemp(prefix="herb-evidence-"))
    archive = temporary / "evidence.zip"
    client.download_bundle(task_id, archive)
    manifest = unpack_task_bundle(archive, temporary / "files", task_id)
    st.session_state["remote_result"] = {"root": str(temporary / "files"), "archive": str(archive), "manifest": manifest}


@st.fragment(run_every="10s")
def _task_monitor(worker_url: str) -> None:
    access_code = st.session_state.get("remote_access_code", "")
    if not access_code:
        return
    client = RemoteComputeClient(worker_url, access_code)
    try:
        tasks = client.request("GET", "/tasks").get("tasks", [])
    except RemoteComputeError as exc:
        st.warning(str(exc))
        return
    if not tasks:
        st.caption("还没有远程任务。提交后会在这里显示进度。")
        return
    lookup = {task["task_id"]: task for task in tasks}
    task_ids = list(lookup)
    if st.session_state.get("remote_selected_task") not in task_ids:
        st.session_state["remote_selected_task"] = task_ids[0]
    selected = st.selectbox("团队任务记录", task_ids, key="remote_selected_task",
                            format_func=lambda key: f"{lookup[key].get('disease_cn') or lookup[key].get('disease_en') or '研究任务'} · {STATE_NAMES.get(lookup[key].get('status'), '等待状态')} · {key[-8:]}")
    try:
        task = client.request("GET", f"/tasks/{selected}")
    except RemoteComputeError as exc:
        st.warning(str(exc))
        return
    state = task.get("status", "queued")
    st.write(f"**{STATE_NAMES.get(state, state)}** · {task.get('stage', '')}")
    if state in {"running", "queued"}:
        progress = task.get("progress")
        if isinstance(progress, (int, float)):
            st.progress(max(0, min(100, int(progress))))
        st.caption("约每10秒刷新。关掉网页不影响任务；电脑关机、休眠或断网会影响计算或查看。")
    if task.get("public_message"):
        st.info(str(task["public_message"]))
    for reason in task.get("blockers", []):
        st.warning(str(reason))
    if state in {"needs_input", "failed", "interrupted"}:
        with st.expander("补充数据并继续本任务"):
            st.caption("只上传科研数据库导出文件，不要上传账号密码或患者个人信息。新尝试保留原始失败记录。")
            with st.form(f"resume-{selected}"):
                required = task.get("required_uploads", [])
                files = {role: st.file_uploader(UPLOAD_NAMES[role] + ("（待补充）" if role in required else "（可选）"), type=UPLOAD_TYPES[role], key=f"resume-{selected}-{role}") for role in UPLOAD_NAMES}
                resume = st.form_submit_button("补充后继续计算")
            if resume:
                try:
                    client.request("POST", f"/tasks/{selected}/resume", {"uploads": _uploads(files)})
                    st.success("已重新加入本机计算队列")
                    st.rerun()
                except (RemoteComputeError, ValueError) as exc:
                    st.error(str(exc))
    if state == "completed" or task.get("bundle_available"):
        if st.button("加载本任务的结果与七章证据", key=f"remote-load-{selected}", type="primary"):
            try:
                with st.spinner("正在从研究电脑读取并核对证据文件…"):
                    _load_result(client, selected)
                st.rerun()
            except (RemoteComputeError, ValueError, OSError, KeyError) as exc:
                st.error(f"证据未能加载：{exc}")


def render_remote_compute_panel(project_root: Path) -> tuple[Path, Path, Path] | None:
    config_path = project_root / "config" / "remote_compute.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        config = {}
    url = config.get("worker_url", "")
    if not config.get("enabled") or not url:
        st.caption("远程计算入口尚未启用；下方可查看已完成的基准任务。")
        return None
    st.markdown('<div id="new-research-task" class="chapter-anchor"></div>', unsafe_allow_html=True)
    with st.expander("开始新分析：网页提交，你的研究电脑计算", expanded=True):
        st.caption("两味药组合 · 新分阶段公式 · 单任务排队运行 · 所有结果仅为科研候选，不是临床处方")
        st.text_input("团队访问码", type="password", key="remote_access_code", help="由电脑负责人提供，只在当前网页会话使用，不写入公开仓库。")
        if st.button("退出团队访问", key="remote-logout"):
            st.session_state.pop("remote_result", None)
            st.session_state.pop("remote_access_code", None)
            st.rerun()
        code = st.session_state.get("remote_access_code", "")
        online = False
        try:
            health = RemoteComputeClient(url, timeout=6).request("GET", "/health")
            online = health.get("ok") is True and health.get("protocol") == "uc-remote-v1"
            if online:
                st.success("研究电脑已连接，可以提交任务。")
            else:
                st.warning("后台连接状态异常，请联系电脑负责人。")
        except RemoteComputeError as exc:
            st.warning(str(exc))
        with st.form("remote-new-task"):
            left, right = st.columns(2)
            disease_cn = left.text_input("疾病中文名", placeholder="例如：溃疡性结肠炎")
            disease_en = right.text_input("疾病英文标准名", placeholder="例如：Ulcerative Colitis")
            herbs = st.text_area("候选中药（顿号、逗号或换行分隔）", placeholder="甘草、马齿苋、黄芪", height=100)
            source = st.selectbox("疾病靶点来源", ["auto", "genecards_upload", "open_targets"], format_func=lambda key: {"auto": "优先复用同疾病已核验快照；没有则按官方接口获取", "genecards_upload": "上传自己授权导出的GeneCards文件", "open_targets": "Open Targets官方接口（来源与GeneCards不同）"}[key])
            with st.expander("数据库导出与评分定义（需要时上传）"):
                st.caption("UC可沿用已记录的操作性定义；其他疾病不能直接套UC通路。缺资料时任务会说明需要什么。")
                files = {role: st.file_uploader(label, type=UPLOAD_TYPES[role], key=f"new-{role}") for role, label in UPLOAD_NAMES.items()}
            submit = st.form_submit_button("提交到研究电脑计算", disabled=not online or not code, type="primary")
        if submit:
            try:
                payload = {"disease_cn": disease_cn.strip(), "disease_en": disease_en.strip(), "herbs": normalize_herbs(herbs), "disease_source": source, "uploads": _uploads(files)}
                payload["request_id"] = _request_identity(payload)
                created = RemoteComputeClient(url, code).request("POST", "/tasks", payload)
                st.session_state["remote_selected_task"] = created["task_id"]
                st.session_state.pop("remote_request_id", None)
                st.session_state.pop("remote_request_fingerprint", None)
                st.success(f"任务已提交，编号：{created['task_id']}")
            except (ValueError, RemoteComputeError, KeyError) as exc:
                st.error(str(exc))
        if code:
            _task_monitor(url)
    result = st.session_state.get("remote_result")
    if result:
        if st.button("返回原UC基准结果", key="remote-clear-result"):
            st.session_state.pop("remote_result", None)
            st.rerun()
        manifest = result["manifest"]
        st.caption(f"当前查看远程任务：{manifest['task_id']}。排名与证据来自本任务，不是默认示例。")
        archive = Path(result["archive"])
        if archive.is_file():
            st.download_button("下载本任务全部证据包", archive.read_bytes(), file_name=f"{manifest['task_id']}.zip", key="remote-bundle-download")
        root = Path(result["root"])
        return root, root / manifest["rules_path"], root / manifest["analysis_root"]
    return None
