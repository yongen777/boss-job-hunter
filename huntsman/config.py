"""配置与路径解析。

硬规矩：任何路径都从本文件的位置推出来，不读 CWD、不读环境变量、不读 .env。
整个目录复制到任意位置都能跑。
"""

import json
import os
import secrets
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
DATA_DIR = PROJECT_ROOT / "data"
RESUME_DIR = DATA_DIR / "resume"
LEDGER_PATH = DATA_DIR / "ledger.db"
TOKEN_PATH = DATA_DIR / ".bridge-token"
VENDOR_DIR = PROJECT_ROOT / "vendor"

# 第三方依赖装在项目内的 vendor/ 里，跟着目录一起搬，不依赖全局 site-packages。
if VENDOR_DIR.is_dir() and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

_DEFAULTS = {
    "bridge": {"host": "127.0.0.1", "port": 8898, "poll_wait_sec": 25, "cmd_timeout_sec": 90},
    "browser": {"kind": "edge", "executable": "", "profile_dir": "data/browser-profile", "extra_args": ["--no-proxy-server"]},
    "candidate": {},
    "matching": {"min_score_to_apply": 70, "weights": {}, "hard_reject_on_blacklist": True},
    "greeting": {"max_chars": 150},
    "safety": {
        "mode": "confirm",
        "daily_send_limit": 20,
        "min_interval_sec": 18,
        "cooldown_after_minutes": 45,
        "cooldown_minutes": 20,
        "stop_on_captcha": True,
        "stop_on_code_37": True,
    },
}


def _deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    """读 config.json，缺的键用默认值补全。文件不存在就返回默认值。"""
    if not CONFIG_PATH.exists():
        return _deep_merge(_DEFAULTS, {})
    with CONFIG_PATH.open("r", encoding="utf-8-sig") as f:
        user = json.load(f)
    return _deep_merge(_DEFAULTS, user)


def save_config(cfg):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def resolve_path(value, default=None):
    """把配置里的相对路径按项目根解析；绝对路径原样返回。"""
    if not value:
        return resolve_path(default) if default else None
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


def ensure_dirs():
    for d in (DATA_DIR, RESUME_DIR):
        d.mkdir(parents=True, exist_ok=True)


def bridge_token(create=True):
    """本机桥的鉴权 token。首次调用时生成并落盘，权限收紧到当前用户。"""
    ensure_dirs()
    if TOKEN_PATH.exists():
        t = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if t:
            return t
    if not create:
        return ""
    t = secrets.token_urlsafe(24)
    TOKEN_PATH.write_text(t, encoding="utf-8")
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        pass
    return t


def bridge_url(cfg=None):
    cfg = cfg or load_config()
    b = cfg["bridge"]
    return f"http://{b['host']}:{b['port']}"