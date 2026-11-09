"""跟本地桥说话的客户端。

只依赖标准库。所有请求都带 X-Bridge-Auth，桥那边校验不通过就是 401。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any

from . import config as cfgmod


class BridgeError(RuntimeError):
    """桥不可达、鉴权失败，或者命令执行失败。"""

    def __init__(self, message: str, *, code: str = "", status: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status


class BridgeClient:
    def __init__(self, base: str | None = None, token: str | None = None, *, timeout: int = 30):
        self.base = (base or cfgmod.bridge_url()).rstrip("/")
        self.token = token if token is not None else cfgmod.bridge_token(create=False) or ""
        self.timeout = timeout

    # ---------- 底层 ----------

    def _request(self, method: str, path: str, body: dict | None = None, *, timeout: float | None = None):
        url = f"{self.base}{path}"
        data = None
        headers = {"X-Bridge-Auth": self.token, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
                detail = json.loads(detail).get("error", "") or detail
            except Exception:
                pass
            if exc.code == 401:
                hint = "配对码对不上"
                token_path = cfgmod.TOKEN_PATH
                if token_path.exists():
                    hint += f"，用 {token_path} 里的值"
                raise BridgeError(f"桥拒绝了请求（401）：{hint}", code="unauthorized", status=401) from exc
            if exc.code == 403:
                raise BridgeError("桥拒绝了来源（403）：只接受扩展发来的请求", code="forbidden", status=403) from exc
            raise BridgeError(f"桥返回 {exc.code}：{detail or exc.reason}", code="http", status=exc.code) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, socket.timeout):
                raise BridgeError("请求超时了，桥可能卡住", code="timeout") from exc
            raise BridgeError(
                f"连不上桥（{self.base}）—— 桥没启动？先双击「起桥.cmd」",
                code="unreachable",
            ) from exc
        except json.JSONDecodeError as exc:
            raise BridgeError("桥返回的不是 JSON", code="bad-json") from exc

    # ---------- 接口 ----------

    def health(self) -> dict:
        return self._request("GET", "/api/health", timeout=6)

    def submit(self, action: str, params: dict | None = None, *, timeout: float = 0.0) -> dict:
        return self._request("POST", "/api/submit", {
            "action": action, "params": params or {}, "timeout": timeout,
        }, timeout=max(self.timeout, timeout + 10))

    def poll(self, wait: float = 25.0, ext_id: str = "", url: str = "") -> dict | None:
        """扩展侧用：取一条待执行命令。没有活就返回 None。"""
        res = self._request("POST", "/api/poll", {
            "wait": wait, "ext_id": ext_id, "url": url,
        }, timeout=wait + 15)
        if isinstance(res, dict) and res.get("id") and res.get("action"):
            return res
        return None

    def result(self, cmd_id: str, wait: float = 0.0) -> dict:
        return self._request("GET", f"/api/result?id={cmd_id}&wait={wait}",
                             timeout=max(wait + 10, 15))

    def stop(self) -> dict:
        return self._request("POST", "/api/stop", {}, timeout=8)

    # ---------- 高层 ----------

    def connected(self) -> bool:
        """桥活着，且扩展正挂着长轮询。"""
        try:
            h = self.health()
        except BridgeError:
            return False
        return bool(h.get("ok")) and bool(h.get("extension", {}).get("connected"))

    def call(self, action: str, params: dict | None = None, *, timeout: float = 90.0) -> dict:
        """发一条命令并等结果。返回 {'ok': bool, 'data': ..., 'error': str}。"""
        started = time.time()
        res = self.submit(action, params, timeout=timeout)
        status = res.get("status")
        if status == "done":
            out = dict(res)
        elif status == "timeout":
            raise BridgeError(
                res.get("error") or "扩展没在限定时间内回结果",
                code="ext-timeout",
            )
        else:  # queued，本地再等一段
            cmd_id = res.get("id", "")
            deadline = started + timeout
            while time.time() < deadline:
                r = self.result(cmd_id, wait=min(10, deadline - time.time()))
                if r.get("status") == "done":
                    out = r
                    break
                if r.get("status") == "unknown":
                    raise BridgeError("命令不见了（桥重启过？）", code="lost")
                time.sleep(0.4)
            else:
                raise BridgeError("等结果超时", code="client-timeout")
        out["elapsed_sec"] = round(time.time() - started, 2)
        return out


def quick_check() -> dict:
    """给 doctor 用：一次拿全桥和扩展的状态。"""
    report: dict[str, Any] = {"bridge_url": cfgmod.bridge_url(), "reachable": False}
    client = BridgeClient(timeout=6)
    try:
        health = client.health()
        report["reachable"] = True
        report["health"] = health
        report["extension_connected"] = bool(health.get("extension", {}).get("connected"))
    except BridgeError as exc:
        report["error"] = str(exc)
        report["error_code"] = exc.code
    return report