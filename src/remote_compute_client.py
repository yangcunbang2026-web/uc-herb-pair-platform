"""Narrow HTTPS client. Credentials remain in the current UI session only."""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request

from src.remote_bundle import MAX_ARCHIVE_BYTES


class RemoteComputeError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_worker_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    local = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"} and parsed.port == 8765
    public = parsed.scheme == "https" and bool(re.fullmatch(r"[a-z0-9-]+\.trycloudflare\.com", parsed.hostname or "")) and parsed.port in {None, 443}
    if not (local or public) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise RemoteComputeError("后台地址无效，必须使用配置的HTTPS计算通道")
    return value.strip().rstrip("/")


class RemoteComputeClient:
    def __init__(self, url: str, access_code: str = "", timeout: int = 15):
        self.url = validate_worker_url(url)
        self.access_code = access_code
        self.timeout = timeout

    def _open(self, method: str, route: str, payload: dict | None = None):
        if not re.fullmatch(r"/(health|tasks(?:/remote-[0-9a-f]{32}(?:/(?:resume|bundle))?)?)", route):
            raise RemoteComputeError("不支持的计算接口")
        headers = {"Accept": "application/json", "User-Agent": "HerbEvidenceRemote/1"}
        if self.access_code:
            access_code = self.access_code.strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", access_code):
                raise RemoteComputeError("团队访问码格式不正确，请完整复制负责人提供的访问码")
            headers["Authorization"] = "Bearer " + access_code
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            return build_opener(_NoRedirect()).open(Request(self.url + route, data=body, headers=headers, method=method), timeout=self.timeout)
        except HTTPError as exc:
            messages = {401: "团队访问码不正确", 403: "没有本任务访问权限", 404: "任务不存在", 409: "任务状态已改变，请刷新后重试", 413: "上传内容太大", 429: "请求过于频繁或队列已满，请稍后重试", 503: "计算后台暂不可用，请确认电脑和通道已启动"}
            if exc.code in {400, 422}:
                try:
                    error = json.loads(exc.read(4096)).get("error", "输入不符合要求")
                except (ValueError, AttributeError):
                    error = "输入不符合要求"
                raise RemoteComputeError(str(error)[:300]) from None
            raise RemoteComputeError(messages.get(exc.code, f"计算通道返回HTTP {exc.code}，请稍后刷新")) from None
        except (URLError, TimeoutError, OSError):
            raise RemoteComputeError("计算后台离线或连接超时。请保持电脑开机联网，并启动计算后台。") from None

    def request(self, method: str, route: str, payload: dict | None = None) -> dict:
        with self._open(method, route, payload) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise RemoteComputeError("后台响应超过限制")
        try:
            value = json.loads(raw)
        except ValueError:
            raise RemoteComputeError("后台没有返回有效任务数据") from None
        if not isinstance(value, dict):
            raise RemoteComputeError("后台响应格式不正确")
        return value

    def download_bundle(self, task_id: str, path: Path) -> None:
        with self._open("GET", f"/tasks/{task_id}/bundle") as response, path.open("wb") as handle:
            received = 0
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > MAX_ARCHIVE_BYTES:
                    raise RemoteComputeError("证据包超过在线下载上限，请在研究端查看")
                handle.write(chunk)
