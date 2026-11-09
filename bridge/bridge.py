#!/usr/bin/env python3
"""本地桥：把 CLI/Agent 的命令下发给浏览器扩展，把扩展的执行结果收回来。

零第三方依赖，只用 Python 标准库。

安全边界（全部在这个文件里）：
  * 只监听 127.0.0.1，不对外网开放
  * 每个请求都要带 `X-Bridge-Auth` 头，值等于 data/.bridge-token
  * Origin 只接受空（CLI）和 chrome-extension://（扩展）；
    网页里带的 Origin 一律 403，防止任意站点盲注入指令
  * 不写任何日志到磁盘之外的地方

协议：
  POST /api/submit   {action, params, timeout}   -> {id} 或直接等到结果
  POST /api/poll     {ext_id, url}               -> {id, action, params} / {}
  POST /api/result   {id, ok, code, data, error} -> {ok:true}
  GET  /api/result   ?id=&wait=                  -> {status, ok, code, data, error}
  GET  /api/health                               -> 桥与扩展状态
  POST /api/stop                                 -> 清空待执行队列
"""

import json
import os
import queue
import secrets
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from huntsman import config as cfgmod  # noqa: E402

VERSION = "0.1.0"
MAX_QUEUE = 50
CMD_TTL_SEC = 60
PENDING_TTL_SEC = 300


class Pending:
    __slots__ = ("id", "action", "created", "event", "ok", "code", "data", "error", "done_at")

    def __init__(self, cid, action):
        self.id = cid
        self.action = action
        self.created = time.time()
        self.event = threading.Event()
        self.ok = None
        self.code = None
        self.data = None
        self.error = None
        self.done_at = None

    def resolve(self, ok, data=None, error=None, code=None):
        self.ok = ok
        self.code = code
        self.data = data
        self.error = error
        self.done_at = time.time()
        self.event.set()


class Bridge:
    def __init__(self):
        self.cmds = queue.Queue()
        self.pending = {}
        self.lock = threading.Lock()
        self.ext_id = None
        self.ext_url = None
        self.ext_seen = 0.0
        self.inflight = None
        self.started = time.time()
        self.counters = {"submitted": 0, "dispatched": 0, "failed": 0, "timeouts": 0}

    # ---- CLI 侧 ----

    def submit(self, action, params, timeout=0.0):
        with self.lock:
            if self.cmds.qsize() >= MAX_QUEUE:
                raise RuntimeError(f"桥的命令队列已满（{MAX_QUEUE}），扩展是不是没连上？先跑 doctor")
            cid = uuid.uuid4().hex[:12]
            p = Pending(cid, action)
            self.pending[cid] = p
            self.cmds.put({"id": cid, "action": action, "params": params or {}, "queued_at": time.time()})
            self.counters["submitted"] += 1
        if timeout and timeout > 0:
            p.event.wait(timeout)
        return p

    def wait(self, cid, timeout):
        with self.lock:
            p = self.pending.get(cid)
        if not p:
            return None
        p.event.wait(timeout)
        return p

    # ---- 扩展侧 ----

    def poll(self, wait, ext_id=None, url=None):
        if ext_id or url:
            with self.lock:
                self.ext_id = ext_id or self.ext_id
                self.ext_url = url if url is not None else self.ext_url
                self.ext_seen = time.time()
        deadline = time.time() + max(0.0, min(wait, 28.0))
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                return None
            try:
                item = self.cmds.get(timeout=min(remain, 1.0))
            except queue.Empty:
                continue
            if time.time() - item.get("queued_at", 0) > CMD_TTL_SEC:
                with self.lock:
                    p = self.pending.pop(item["id"], None)
                    self.counters["timeouts"] += 1
                if p:
                    p.resolve(False, error="命令在队列里等太久，已过期（扩展当时没连上？）")
                continue
            with self.lock:
                self.inflight = item["id"]
                self.counters["dispatched"] += 1
            return item

    def report(self, cid, ok, data=None, error=None, code=None):
        with self.lock:
            p = self.pending.get(cid)
            if self.inflight == cid:
                self.inflight = None
            if p is None:
                return False
            if not ok:
                self.counters["failed"] += 1
        p.resolve(ok, data, error, code)
        return True

    def health(self):
        with self.lock:
            idle = (time.time() - self.ext_seen) if self.ext_seen else None
            return {
                "ok": True,
                "version": VERSION,
                "uptime_sec": round(time.time() - self.started, 1),
                "extension": {
                    "connected": idle is not None and idle < 40,
                    "id": self.ext_id,
                    "url": self.ext_url,
                    "idle_sec": round(idle, 1) if idle is not None else None,
                },
                "queue": self.cmds.qsize(),
                "inflight": self.inflight,
                "counters": dict(self.counters),
            }

    def sweep(self):
        """清掉超时没回音的 pending，免得内存里越积越多。"""
        now = time.time()
        with self.lock:
            dead = [k for k, p in self.pending.items() if p.done_at and now - p.done_at > 60]
            for k in dead:
                self.pending.pop(k, None)
            stale = [k for k, p in self.pending.items() if not p.done_at and now - p.created > PENDING_TTL_SEC]
            for k in stale:
                p = self.pending.pop(k, None)
                if p:
                    p.resolve(False, error="命令等了 5 分钟没结果，放弃了")


BRIDGE = Bridge()


def _make_handler(bridge, token, quiet):

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"BossJobHunter/{VERSION}"

        # -- 基础 --

        def log_message(self, fmt, *args):
            if not quiet:
                sys.stderr.write("[桥] %s\n" % (fmt % args))

        def handle_one_request(self):
            # 浏览器关标签页 / 长轮询超时都会把连接从中途掐断。
            # 那不是桥出错了，别让它污染 stderr —— 真错误会被这堆 traceback 埋掉。
            try:
                super().handle_one_request()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def _origin_ok(self):
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return origin.startswith("chrome-extension://")

        def _auth_ok(self):
            got = self.headers.get("X-Bridge-Auth") or ""
            return secrets.compare_digest(got, token)

        def _send(self, code, payload, extra=None):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            origin = self.headers.get("Origin")
            if origin and origin.startswith("chrome-extension://"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _read_json(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return {}
            if n <= 0 or n > 8 * 1024 * 1024:
                return {}
            raw = self.rfile.read(n)
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {}

        def _guard(self):
            if not self._origin_ok():
                self._send(403, {"ok": False, "error": "origin 不被允许"})
                return False
            if not self._auth_ok():
                self._send(401, {"ok": False, "error": "token 不对，看 data/.bridge-token"})
                return False
            return True

        # -- 路由 --

        def do_OPTIONS(self):
            origin = self.headers.get("Origin") or ""
            hdrs = {}
            if origin.startswith("chrome-extension://"):
                hdrs["Access-Control-Allow-Origin"] = origin
                hdrs["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
                hdrs["Access-Control-Allow-Headers"] = "Content-Type, X-Bridge-Auth"
                hdrs["Access-Control-Max-Age"] = "600"
                hdrs["Vary"] = "Origin"
            self._send(204, {}, hdrs)

        def do_GET(self):
            path, _, qs = self.path.partition("?")
            params = {}
            for pair in qs.split("&"):
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    params[k] = v
            if path == "/api/health":
                if not self._auth_ok():
                    self._send(401, {"ok": False, "error": "token 不对"})
                    return
                self._send(200, bridge.health())
                return
            if path == "/api/result":
                if not self._guard():
                    return
                cid = params.get("id", "")
                wait = float(params.get("wait") or 0)
                p = bridge.wait(cid, wait)
                if p is None:
                    self._send(404, {"status": "unknown", "ok": False, "error": "没有这个命令 id"})
                elif not p.event.is_set():
                    self._send(200, {"status": "pending", "id": cid})
                else:
                    self._send(200, {"status": "done", "id": cid, "ok": p.ok,
                                     "code": p.code, "data": p.data, "error": p.error})
                return
            self._send(404, {"ok": False, "error": "没有这个路径"})

        def do_POST(self):
            path = self.path.partition("?")[0]
            if path == "/api/poll":
                if not self._guard():
                    return
                body = self._read_json()
                wait = float(body.get("wait") or 0)
                item = bridge.poll(wait, body.get("ext_id"), body.get("url"))
                self._send(200, item or {})
                return
            if not self._guard():
                return
            body = self._read_json()

            if path == "/api/submit":
                action = (body.get("action") or "").strip()
                if not action:
                    self._send(400, {"ok": False, "error": "缺 action"})
                    return
                timeout = float(body.get("timeout") or 0)
                try:
                    p = bridge.submit(action, body.get("params"), timeout)
                except RuntimeError as e:
                    self._send(503, {"ok": False, "error": str(e)})
                    return
                if timeout and timeout > 0:
                    if not p.event.is_set():
                        self._send(200, {"status": "timeout", "id": p.id, "ok": False,
                                         "error": "扩展没有在限定时间内回结果"})
                    else:
                        self._send(200, {"status": "done", "id": p.id, "ok": p.ok,
                                         "code": p.code, "data": p.data, "error": p.error})
                else:
                    self._send(200, {"status": "queued", "id": p.id})
                return

            if path == "/api/result":
                cid = body.get("id") or ""
                ok = bool(body.get("ok"))
                known = bridge.report(cid, ok, body.get("data"), body.get("error"), body.get("code"))
                self._send(200, {"ok": True, "known": known})
                return

            if path == "/api/stop":
                drained = 0
                while True:
                    try:
                        item = bridge.cmds.get_nowait()
                    except queue.Empty:
                        break
                    drained += 1
                    with bridge.lock:
                        p = bridge.pending.pop(item["id"], None)
                    if p:
                        p.resolve(False, error="队列被清空")
                self._send(200, {"ok": True, "drained": drained})
                return

            self._send(404, {"ok": False, "error": "没有这个路径"})

    return Handler


def main(argv=None):
    argv = argv or sys.argv[1:]
    quiet = "--quiet" in argv

    # Windows 控制台默认 GBK，直接 print 中文 banner 会乱码甚至抛 UnicodeEncodeError。
    # 这里改成 UTF-8 并容忍个别打不出的字符。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    cfg = cfgmod.load_config()
    cfgmod.ensure_dirs()
    token = cfgmod.bridge_token()
    host = cfg["bridge"]["host"]
    port = int(cfg["bridge"]["port"])

    handler = _make_handler(BRIDGE, token, quiet)
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True

    def sweep_loop():
        while True:
            time.sleep(30)
            BRIDGE.sweep()

    threading.Thread(target=sweep_loop, daemon=True, name="sweeper").start()

    print(f"BOSS直聘求职猎手 · 本地桥 v{VERSION}")
    print(f"  监听   {host}:{port}（只本机）")
    print(f"  token  {token}")
    print(f"  配置   {cfgmod.CONFIG_PATH}")
    print()
    print("扩展配对用的就是这个 token。Ctrl+C 结束。")
    sys.stdout.flush()

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n桥已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()