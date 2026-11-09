# -*- coding: utf-8 -*-
"""起一个「自己的」浏览器实例来跑投递。

为什么必须单独起一个：

1. BOSS直聘 的前端带加固的反调试，被 CDP 接管（--remote-debugging-port）的窗口
   会在几秒内自己 window.close()，服务端还会返 code:37「环境存在异常」。
   所以这个工具**永远不加 --remote-debugging-port**，改用扩展 + 本地桥通信。
2. 用一个独立的 --user-data-dir，登录态就跟着这个文件夹走，既不污染你日常用的
   浏览器，也能把整个目录拷到别的机器上继续用。
3. 必须境内直连。走代理会触发风控，所以默认带 --no-proxy-server。

直接跑：python -m huntsman.browser
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import config as cfgmod

# 这些参数会让浏览器立刻被识别或直接关窗，谁配进来都拦掉
FORBIDDEN_ARGS = (
    "--remote-debugging-port",
    "--remote-debugging-pipe",
    "--headless",
    "--disable-gpu",
    "--incognito",
)

DEFAULT_CANDIDATES = {
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/microsoft-edge",
    ],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
    ],
}


def detect(kind: str = "edge") -> str | None:
    """找浏览器可执行文件。找不到返回 None。"""
    for path in DEFAULT_CANDIDATES.get(kind, DEFAULT_CANDIDATES["edge"]):
        if Path(path).exists():
            return path
    return None


def build_command(cfg: dict | None = None) -> tuple[str, list[str]]:
    """拼出启动命令。返回 (可执行文件, 参数列表)。"""
    cfg = cfg or cfgmod.load_config()
    br = cfg.get("browser", {})
    kind = (br.get("kind") or "edge").lower()

    exe = br.get("executable") or ""
    if exe:
        exe = str(cfgmod.resolve_path(exe, None))
        if not Path(exe).exists():
            raise SystemExit(f"[x] config.json 里 browser.executable 指的路径不存在：{exe}")
    else:
        found = detect(kind)
        if not found:
            raise SystemExit(
                f"[x] 没找到 {kind}。\n"
                "    在 config.json 的 browser.executable 里填完整路径，"
                "或者把 browser.kind 改成 chrome"
            )
        exe = found

    profile_dir = cfgmod.resolve_path(br.get("profile_dir") or "data/browser-profile")
    if not profile_dir:
        raise SystemExit("[x] config.json 里 browser.profile_dir 配错了")
    if isinstance(profile_dir, str):
        profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    ext_dir = cfgmod.PROJECT_ROOT / "extension"
    if not (ext_dir / "manifest.json").exists():
        raise SystemExit(f"[x] 扩展目录不完整，缺 {ext_dir / 'manifest.json'}")

    extra = list(br.get("extra_args") or [])
    for bad in FORBIDDEN_ARGS:
        if any(a == bad or a.startswith(bad + "=") for a in extra):
            raise SystemExit(
                f"[x] config.json 的 browser.extra_args 里有 {bad}，这个不能加。\n"
                "    加了它 BOSS直聘 会自动关掉窗口，投递根本走不下去。"
            )

    args = [
        f"--user-data-dir={profile_dir}",
        # 137+ 的 Chrome/Edge 把 --load-extension 关掉了（静默忽略，扩展不装也不报错）。
        # 这个 feature flag 能把它开回来；开不回来的版本就只能手动去 edge://extensions 加载。
        f"--load-extension={ext_dir}",
        "--disable-features=Translate,DisableLoadExtensionCommandLineSwitch",
        "--no-proxy-server",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
        cfgmod.bridge_url(cfg or cfgmod.load_config()),
    ]
    # extra_args 里跟默认参数重复的丢掉，别在命令行里出现两遍
    for a in extra:
        if a in args:
            continue
        args.append(a)
    return exe, args


def find_extension_id(profile_dir: Path, ext_dir: Path) -> str | None:
    """从 profile 的注册表里反查这个扩展目录的扩展 id。

    清单里没写 `key`，id 是按扩展目录路径算出来的；从命令行 --load-extension
    加载的扩展（location=8）只登记在 `Secure Preferences` 里，不在 `Preferences`。
    """
    target = str(ext_dir).lower()
    for name in ("Secure Preferences", "Preferences"):
        pref = Path(profile_dir) / "Default" / name
        if not pref.exists():
            continue
        try:
            data = json.loads(pref.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        settings = ((data.get("extensions") or {}).get("settings") or {})
        for eid, entry in settings.items():
            known = str((entry or {}).get("path") or "").lower()
            if known and known == target:
                return eid
    return None


def read_token() -> str:
    """读桥的配对码（桥首次启动会生成）。没有就返回空串。"""
    try:
        return cfgmod.TOKEN_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def wait_paired(
    info: dict,
    cfg: dict | None = None,
    *,
    wait_sec: float = 20.0,
) -> dict:
    """等扩展自己连上桥。

    配对码不用手填了：扩展一醒过来发现 storage 里没有 token，就会去
    `GET /api/token` 领一个（桥只放行空 Origin 和 chrome-extension://，
    网页里 fetch 带的 Origin 会被 403 掉）。所以这里只管等，不再往
    profile 里塞东西 —— 命令行开 chrome-extension:// 页面会被新版
    Chromium 直接拒掉，那条路走不通。
    """
    cfg = cfg or cfgmod.load_config()
    token = read_token()
    if not token:
        return {"ok": False, "connected": False, "code": "no-token",
                "reason": f"没找到配对码 {cfgmod.TOKEN_PATH}（桥启动过吗？）"}

    eid = find_extension_id(Path(info["profile"]), Path(info["extension"]))
    deadline = time.time() + wait_sec
    last = ""
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                cfgmod.bridge_url(cfg).rstrip("/") + "/api/health",
                headers={"X-Bridge-Auth": token},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            ext = data.get("extension") or {}
            if ext.get("connected"):
                return {"ok": True, "connected": True, "extId": ext.get("id") or eid}
            last = "桥在跑，但扩展还没来要指令"
        except OSError as exc:
            last = f"连不上桥：{exc}"
        time.sleep(0.5)
    return {"ok": False, "connected": False, "extId": eid,
            "code": "not-connected", "reason": last or "等超时了"}


def launch(cfg: dict | None = None, *, dry_run: bool = False, pair: bool = True) -> dict:
    """起浏览器。返回 {ok, exe, profile, extension, command, pid?, dry_run?, pair?}。"""
    cfg = cfg or cfgmod.load_config()
    exe, args = build_command(cfg)
    br = cfg.get("browser", {})
    info = {
        "ok": True,
        "exe": exe,
        "profile": str(cfgmod.resolve_path(br.get("profile_dir") or "data/browser-profile")),
        "extension": str(cfgmod.PROJECT_ROOT / "extension"),
        "bridge": cfgmod.bridge_url(cfg),
        "command": " ".join([f'"{exe}"'] + [f'"{a}"' if " " in a else a for a in args]),
    }
    if dry_run:
        info["dry_run"] = True
        return info
    proc = subprocess.Popen([exe] + args, close_fds=True)
    info["pid"] = proc.pid
    if pair:
        info["pair"] = wait_paired(info, cfg)
    return info


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    dry = "--dry-run" in argv or "--print" in argv
    pair = "--no-pair" not in argv
    cfgmod.ensure_dirs()
    info = launch(dry_run=dry, pair=pair)

    print("启动浏览器")
    print(f"  程序   {info['exe']}")
    print(f"  配置   {info['profile']}   （独立身份，不影响你日常的浏览器）")
    print(f"  扩展   {info['extension']}")
    print(f"  桥     {info['bridge']}")
    print()
    if dry:
        print("将要执行的命令：")
        print("  " + info["command"])
        return 0
    print(f"已启动，pid {info['pid']}")
    paired = info.get("pair") or {}
    if not pair:
        print("  （--no-pair：没等扩展连上来）")
    elif paired.get("ok"):
        print(f"  [ok] 扩展已连上桥（id {paired.get('extId') or '未知'}），配对码它自己领的")
    else:
        print(f"  [!] 扩展还没连上：{paired.get('reason') or paired.get('code')}")
        print("      多半是扩展还没装进这个 profile，按下面第 2 步加载一下，")
        print("      加载完它会自己来领配对码，不用手填。")
    print()
    print("接下来：")
    print("  1) 这个新窗口里登录 BOSS直聘（zhipin.com），扫码或账号密码都行")
    print("  2) 地址栏开 edge://extensions ，看「BOSS直聘求职猎手」在不在")
    print("     不在的话：打开右上角「开发人员模式」→「加载解压缩的扩展」→ 选上面那个扩展目录")
    if paired.get("ok"):
        print("  3) 配对已经通了，这步跳过")
    else:
        print("  3) 扩展加载好之后等几秒，它会自己连桥；一直不通就点扩展图标，")
        print("     把 data/.bridge-token 里的配对码粘进去，点「保存并连接」")
    print("  4) 回到命令行跑  python cli.py doctor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())