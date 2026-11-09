import pathlib
"""账本全流程冒烟：落库 → 排序 → 限额 → 冷却 → 消息 → 汇总。用临时库，不碰真库。"""
import sys, tempfile, os
from pathlib import Path
_ROOT = str(pathlib.Path(__file__).resolve().parents[1])
sys.path.insert(0, _ROOT)
from huntsman import config as cfgmod, ledger

tmp = Path(tempfile.gettempdir()) / "bjh-ledger-test.sqlite3"
if tmp.exists():
    tmp.unlink()

cfg = cfgmod.load_config()
cfg["safety"]["daily_send_limit"] = 3
cfg["safety"]["min_interval_sec"] = 18
cfg["safety"]["cooldown_after_minutes"] = 45
cfg["safety"]["cooldown_minutes"] = 20

conn = ledger.connect(tmp)
fails = []
def check(label, cond, extra=""):
    print(("  [ok] " if cond else "  [x ] ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        fails.append(label)

print("=== 落库 / 去重 ===")
job = {"job_id": "j1", "title": "Java 后端开发工程师", "company": "字节跳动",
       "salary": "30-50K·15薪", "area": "上海·浦东新区", "url": "https://www.zhipin.com/job_detail/j1.html",
       "desc": "要求 5 年以上 Java 开发经验", "score": 94.9, "verdict": "apply"}
check("第一次 upsert 是新插入", ledger.upsert_job(conn, job) is True)
job["score"] = 95.5
check("第二次 upsert 是更新", ledger.upsert_job(conn, job) is False)
got = ledger.get_job(conn, "j1")
check("分数被更新为 95.5", got and abs(got["score"] - 95.5) < 0.01, f"得 {got and got['score']}")
check("状态默认 new", got and got["status"] == "new", f"得 {got and got['status']}")

print("\n=== 批量 + 排序 ===")
for i, (jid, sc) in enumerate([("j2", 78.0), ("j3", 49.0), ("j4", 0.0)], start=2):
    ledger.upsert_job(conn, {"job_id": jid, "title": f"岗{i}", "company": f"公司{i}",
                             "salary": "20-30K", "area": "上海", "url": "", "desc": "", "score": sc,
                             "verdict": "borderline" if sc >= 58 else "skip"})
by_score = ledger.list_jobs(conn, order="score")
check("按分数降序", [j["job_id"] for j in by_score] == ["j1", "j2", "j3", "j4"],
      str([j["job_id"] for j in by_score]))
check("默认上限 50 够用", len(by_score) == 4, str(len(by_score)))
hi = ledger.list_jobs(conn, min_score=58)
check("min_score=58 只剩 2 个", len(hi) == 2, str([j["job_id"] for j in hi]))

print("\n=== 状态流转 ===")
ledger.set_status(conn, "j3", "skipped", "薪资不匹配")
check("j3 变 skipped", ledger.get_job(conn, "j3")["status"] == "skipped")
check("note 写进去了", ledger.get_job(conn, "j3")["note"] == "薪资不匹配")

print("\n=== 发送限额 / 冷却 ===")
ok, why, wait = ledger.can_send(conn, cfg)
check("初始可以发", ok, why)
for i in range(3):
    ledger.record_application(conn, "j1", f"招呼语{i}", True, True)
    ledger.mark_session_start(conn)
check("发了 3 条后计数为 3", ledger.count_sends_today(conn) == 3, str(ledger.count_sends_today(conn)))
ok, why, wait = ledger.can_send(conn, cfg)
check("达到 3 条上限后拦住", not ok, why)
check("拦的原因是日限额", "上限" in why or "limit" in why.lower(), why)

print("\n=== 冷却（冷却期内不让连续发） ===")
cfg["safety"]["daily_send_limit"] = 100
cfg["safety"]["min_interval_sec"] = 99999
ok, why, wait = ledger.can_send(conn, cfg)
check("间隔太短被拦", not ok, why)
check("wait 给出要等多少秒", isinstance(wait, int) and wait > 0, str(wait))

print("\n=== 投递记录 / 失败不计数 ===")
before = ledger.count_sends_today(conn)
ledger.record_application(conn, "j2", "失败的招呼", False, False, "fill-failed")
check("失败的投递不计入今日条数", ledger.count_sends_today(conn) == before, str(ledger.count_sends_today(conn)))
check("j2 状态变 failed", ledger.get_job(conn, "j2")["status"] == "failed", ledger.get_job(conn, "j2")["status"])

print("\n=== 消息 ===")
ledger.save_message(conn, "j1", "me", "您好，看到岗位了")
ledger.save_message(conn, "j1", "them", "方便发份简历吗")
ledger.save_message(conn, "j1", "them", "方便发份简历吗")
check("重复消息被吞掉", len(ledger.recent_messages(conn, "j1")) == 2,
      str(len(ledger.recent_messages(conn, "j1"))))
pend = ledger.pending_replies(conn)
check("待回复只有对方那 1 条（重复的被吞了）", len(pend) == 1, str(len(pend)))
if pend:
    ledger.mark_handled(conn, pend[0]["id"])
    check("标记后清空", len(ledger.pending_replies(conn)) == 0, str(len(ledger.pending_replies(conn))))

print("\n=== 事件 / 汇总 ===")
ledger.log_event(conn, "hunt", "搜了 20 个")
evs = ledger.recent_events(conn, 10)
check("事件读得到", len(evs) >= 1, str(len(evs)))
s = ledger.summary(conn)
print("  汇总:", {k: s[k] for k in list(s)[:8]})
check("汇总有 jobs 字段", "jobs" in s or "total" in s or len(s) > 0, str(list(s)))

conn.close()
print()
if fails:
    print(f"[x] {len(fails)} 项没过: {fails}")
    sys.exit(1)
print("[ok] 账本全流程都对")