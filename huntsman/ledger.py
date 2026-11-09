"""投递账本（SQLite，标准库）。

三件事必须留痕：投给谁了、发出去的话是什么、HR 回了什么。
另外它是限速的唯一裁判 —— can_send() 说不许就不许，别在别处绕过去。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from . import config as cfgmod

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    title       TEXT,
    company     TEXT,
    salary      TEXT,
    area        TEXT,
    url         TEXT,
    score       REAL,
    verdict     TEXT,
    match_json  TEXT,
    jd_text     TEXT,
    status      TEXT NOT NULL DEFAULT 'new',
    note        TEXT,
    first_seen  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    greeting    TEXT NOT NULL,
    ok          INTEGER NOT NULL DEFAULT 0,
    verified    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    side        TEXT NOT NULL,
    text        TEXT NOT NULL,
    seen_at     TEXT NOT NULL,
    handled     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(job_id, side, text, seen_at)
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    detail      TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    k           TEXT PRIMARY KEY,
    v           TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_msg_job ON messages(job_id);
CREATE INDEX IF NOT EXISTS idx_app_sent ON applications(sent_at);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path else cfgmod.LEDGER_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------- 岗位


def upsert_job(conn: sqlite3.Connection, job: dict) -> bool:
    """返回 True 表示这是新岗位。"""
    job_id = str(job.get("jobId") or job.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("岗位没有 id，不能进账本")
    ts = now()
    cur = conn.execute("SELECT job_id FROM jobs WHERE job_id = ?", (job_id,))
    exists = cur.fetchone() is not None
    match = job.get("match") or {}
    # 兼容两种形状：匹配引擎的 match 子字典，或直接摊在顶层的 score/verdict
    score = match.get("score")
    if score is None:
        score = job.get("score")
    verdict = match.get("verdict")
    if verdict is None:
        verdict = job.get("verdict")
    body = (
        job.get("title"), job.get("company"), job.get("salary"), job.get("area"),
        job.get("href") or job.get("url"), score, verdict,
        json.dumps(match, ensure_ascii=False) if match else None,
        job.get("desc"),
    )
    if exists:
        conn.execute(
            """UPDATE jobs SET title=?, company=?, salary=?, area=?, url=?, score=?,
                   verdict=?, match_json=?, jd_text=?, updated_at=?
               WHERE job_id=?""",
            body + (ts, job_id),
        )
    else:
        conn.execute(
            """INSERT INTO jobs (job_id,title,company,salary,area,url,score,verdict,
                   match_json,jd_text,status,first_seen,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,'new',?,?)""",
            (job_id,) + body + (ts, ts),
        )
    conn.commit()
    return not exists


def get_job(conn: sqlite3.Connection, job_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(conn: sqlite3.Connection, status: str | None = None,
              min_score: float | None = None, limit: int = 50,
              order: str = "score") -> list[dict]:
    sql = "SELECT * FROM jobs WHERE 1=1"
    args: list = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if min_score is not None:
        sql += " AND COALESCE(score, 0) >= ?"
        args.append(min_score)
    sql += " ORDER BY " + ("score DESC, first_seen DESC" if order == "score" else "first_seen DESC")
    sql += " LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def set_status(conn: sqlite3.Connection, job_id: str, status: str, note: str = "") -> None:
    conn.execute("UPDATE jobs SET status=?, note=?, updated_at=? WHERE job_id=?",
                 (status, note, now(), job_id))
    conn.commit()


def find_by_company_title(conn: sqlite3.Connection, company: str, title: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM jobs WHERE company=? AND title=? ORDER BY updated_at DESC LIMIT 1",
        (company, title),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- 投递


def record_application(conn: sqlite3.Connection, job_id: str, greeting: str,
                       ok: bool, verified: bool, error: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO applications (job_id,sent_at,greeting,ok,verified,error) VALUES (?,?,?,?,?,?)",
        (job_id, now(), greeting, int(ok), int(verified), error or None),
    )
    status = "applied" if (ok and verified) else ("unverified" if ok else "failed")
    conn.execute("UPDATE jobs SET status=?, updated_at=? WHERE job_id=? AND status NOT IN ('replied')",
                 (status, now(), job_id))
    conn.commit()
    return int(cur.lastrowid)


def count_sends_today(conn: sqlite3.Connection) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM applications WHERE ok=1 AND substr(sent_at,1,10)=?",
        (today,),
    ).fetchone()
    return int(row["n"] if row else 0)


def last_send_at(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute(
        "SELECT sent_at FROM applications WHERE ok=1 ORDER BY sent_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row["sent_at"])
    except ValueError:
        return None


def _kv_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
    return row["v"] if row else None


def _kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO kv (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (key, value))
    conn.commit()


def can_send(conn: sqlite3.Connection, cfg: dict) -> tuple[bool, str, int]:
    """能不能再发一条。返回 (允许, 原因, 建议等待秒数)。"""
    s = cfg.get("safety", {})
    daily = int(s.get("daily_send_limit", 20))
    min_interval = int(s.get("min_interval_sec", 18))
    cool_after = int(s.get("cooldown_after_minutes", 45))
    cool_len = int(s.get("cooldown_minutes", 20))

    sent_today = count_sends_today(conn)
    if sent_today >= daily:
        return False, f"今天已经发了 {sent_today} 条，到上限 {daily} 了", 0

    last = last_send_at(conn)
    if last is None:
        return True, f"今天第 1 条（上限 {daily}）", 0

    elapsed = (datetime.now() - last).total_seconds()

    started = _kv_get(conn, "session_started_at")
    if started:
        try:
            start_dt = datetime.fromisoformat(started)
            busy = (datetime.now() - start_dt).total_seconds()
            if busy > cool_after * 60 and elapsed < cool_len * 60:
                wait = int(cool_len * 60 - elapsed)
                return False, f"连续发了 {busy/60:.0f} 分钟，进冷却 {cool_len} 分钟，还剩 {wait//60} 分 {wait%60} 秒", wait
        except ValueError:
            pass

    if elapsed < min_interval:
        wait = int(min_interval - elapsed)
        return False, f"距上一条才 {int(elapsed)} 秒，按要求至少隔 {min_interval} 秒，再等 {wait} 秒", wait

    return True, f"今天第 {sent_today + 1} 条（上限 {daily}），距上一条 {int(elapsed//60)} 分", 0


def mark_session_start(conn: sqlite3.Connection) -> None:
    """本轮开始 —— 只有冷却计时用。已经过冷却期就重新计时。"""
    started = _kv_get(conn, "session_started_at")
    if not started:
        _kv_set(conn, "session_started_at", now())
        return
    try:
        start_dt = datetime.fromisoformat(started)
    except ValueError:
        _kv_set(conn, "session_started_at", now())
        return
    last = last_send_at(conn)
    if last and (last - start_dt).total_seconds() > 3600 * 2:
        _kv_set(conn, "session_started_at", now())


# ---------------------------------------------------------------- 消息


def save_message(conn: sqlite3.Connection, job_id: str, side: str, text: str) -> bool:
    """返回 True 表示这条是新的（之前没见过）。"""
    cur = conn.execute(
        "INSERT OR IGNORE INTO messages (job_id,side,text,seen_at) VALUES (?,?,?,?)",
        (job_id, side, text, now()),
    )
    conn.commit()
    return cur.rowcount > 0


def pending_replies(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """SELECT m.*, j.title, j.company FROM messages m
           JOIN jobs j ON j.job_id = m.job_id
           WHERE m.side='them' AND m.handled=0
           ORDER BY m.seen_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_handled(conn: sqlite3.Connection, msg_id: int, handled: bool = True) -> None:
    conn.execute("UPDATE messages SET handled=? WHERE id=?", (int(handled), msg_id))
    conn.commit()


def recent_messages(conn: sqlite3.Connection, job_id: str, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM messages WHERE job_id=? ORDER BY seen_at DESC, id DESC LIMIT ?",
        (job_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ---------------------------------------------------------------- 事件


def log_event(conn: sqlite3.Connection, kind: str, detail: str = "") -> None:
    conn.execute("INSERT INTO events (at,kind,detail) VALUES (?,?,?)", (now(), kind, detail))
    conn.commit()


def recent_events(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 概览


def summary(conn: sqlite3.Connection) -> dict:
    def one(sql: str, args=()):
        row = conn.execute(sql, args).fetchone()
        return int(row[0]) if row else 0

    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "jobs_total": one("SELECT COUNT(*) FROM jobs"),
        "by_status": {
            r["status"]: r["n"]
            for r in conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        },
        "sent_today": one(
            "SELECT COUNT(*) FROM applications WHERE ok=1 AND substr(sent_at,1,10)=?", (today,)
        ),
        "sent_total": one("SELECT COUNT(*) FROM applications WHERE ok=1"),
        "unverified": one("SELECT COUNT(*) FROM applications WHERE ok=1 AND verified=0"),
        "failed": one("SELECT COUNT(*) FROM applications WHERE ok=0"),
        "pending_replies": one("SELECT COUNT(*) FROM messages WHERE side='them' AND handled=0"),
        "last_send": (last_send_at(conn) or datetime.min).isoformat(timespec="seconds")
        if last_send_at(conn) else None,
    }


def today_bounds() -> tuple[str, str]:
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(timespec="seconds"), (start + timedelta(days=1)).isoformat(timespec="seconds")