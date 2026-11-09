"""draft 模块回归：招呼语 / 限长 / 违规词 / 意图分类 / 回复草稿。

只打印不断言等于没测——这里每条都带 check()，有失败就 exit 1。
"""

import pathlib
import sys

_ROOT = str(pathlib.Path(__file__).resolve().parents[1])
sys.path.insert(0, _ROOT)
from huntsman import config as cfgmod, draft  # noqa: E402

fails: list[str] = []


def check(ok: bool, what: str, detail: object = "") -> None:
    if ok:
        print(f"  [ok] {what}  {detail}")
    else:
        print(f"  [x] {what}  {detail}")
        fails.append(what)


cfg = cfgmod.load_config()
cfg["candidate"]["name"] = "张伟"
skills = ["Java", "Spring", "MySQL", "Redis", "Kafka", "Docker", "Kubernetes", "微服务", "分布式"]
job = {"id": "j1", "title": "Java 后端开发工程师", "company": "字节跳动",
       "area": "上海·浦东新区", "salary": "30-50K·15薪",
       "desc": "要求 5 年以上 Java 开发经验，熟悉 Spring Boot、MySQL、Redis、Kafka、Docker。负责订单中心高并发架构设计。",
       "match": {"matched_skills": ["Java", "Spring", "MySQL", "Redis", "Kafka", "Docker", "Kubernetes", "微服务"],
                 "score": 94.9}}

resume_text = ("2020.07-至今 某电商公司 高级后端工程师。"
               "负责订单中心微服务重构，QPS 从 800 提升到 5000。"
               "用 Spring Boot 搭了主链路，MySQL 分库分表，Redis 扛住热点。"
               "线上问题排查经验丰富。")

print("=== build_greeting（有证据） ===")
b = draft.build_greeting(job, resume_text, skills, cfg)
print(f"  {b['text']}")
check(b["violations"] == [], "有证据版无违规词", b["violations"])
check(b["chars"] <= b["max_chars"], "字数不超上限", f"{b['chars']}/{b['max_chars']}")
check(len(b["facts"]["evidence"]) > 0, "挑到了简历证据", b["facts"]["evidence"])
check(all(len(e) <= 61 for e in b["facts"]["evidence"]),
      "证据条都不超过 61 字（截断带省略号）",
      [len(e) for e in b["facts"]["evidence"]])
check("贵公司" not in b["text"], "没出现「贵公司」")
check("期待您的回复" not in b["text"], "没出现「期待您的回复」")
check(b["facts"].get("style") == cfg["greeting"].get("style"),
      "facts 里带上了 greeting.style（给 agent 改写用）", b["facts"].get("style"))

print("\n=== build_greeting（简历空） ===")
b2 = draft.build_greeting(job, "", skills, cfg)
print(f"  {b2['text']}")
check(b2["violations"] == [], "简历空也不违规", b2["violations"])
check(b2["chars"] <= b2["max_chars"], "简历空字数也不超", f"{b2['chars']}/{b2['max_chars']}")

print("\n=== pick_evidence 截断边界 ===")
long_sent = ("用 Kafka 做了消息削峰，在双十一大促期间把订单创建接口的 P99 从 1200ms 压到 180ms，"
             "同时把消息积压从百万级降到千级，这套方案后来复用到了另外三条业务线。")
ev = draft.pick_evidence(long_sent, ["Kafka"], limit=1)
check(len(ev) == 1, "长句也能挑出一条", len(ev))
check(ev and len(ev[0]) <= 61, "长句被截断到 60 字以内", len(ev[0]) if ev else None)
check(ev and ev[0].endswith("…"), "截断带省略号", ev[0][-6:] if ev else None)

print("\n=== check_greeting 反例 ===")
bad_cases = [
    ("您好，我是张伟，我对贵司这个岗位非常感兴趣，希望能有一个机会，我很崇拜贵司，恳请给个机会。", "套话+「您好我是」"),
    ("短", "太短"),
]
for bad, why in bad_cases:
    v = draft.check_greeting(bad, cfg)
    check(len(v) > 0, f"反例被抓到：{why}", v)

print("\n=== classify_intent ===")
cases = [
    ("你好，方便发一份简历吗？", "resume_request"),
    ("我们想约你周四下午 3 点面试，可以吗？", "interview"),
    ("这个岗位薪资大概 30-40K，你能接受吗？", "salary"),
    ("不好意思，这个岗位已经招到人了", "reject"),
    ("岗位已经招满，抱歉", "reject"),
    ("你的期望薪资不太合适", "salary"),
    ("你之前做过哪些高并发项目？", "question"),
    ("你好", "greeting"),
    ("今天天气不错", "other"),
]
misses = 0
for text, want in cases:
    got = draft.classify_intent(text)
    if got != want:
        misses += 1
        print(f"  [x] 想要 {want}，得 {got}  <- {text}")
check(misses == 0, "意图分类全对", f"{len(cases) - misses}/{len(cases)}")

print("\n=== reply_draft ===")
r_resume = draft.reply_draft("你好，方便发一份简历吗？", job, cfg)
print(f"  resume_request -> {r_resume['draft'] or '(留空)'}")
check(r_resume["intent"] == "resume_request", "简历请求意图对", r_resume["intent"])
check(bool(r_resume["draft"]), "简历请求有草稿", r_resume["draft"][:24])

r_itv = draft.reply_draft("我们想约你周四下午 3 点面试", job, cfg)
print(f"  interview -> {r_itv['draft'] or '(留空)'}")
check(r_itv["intent"] == "interview", "面试邀约意图对", r_itv["intent"])
check(r_itv["needs_human"] is True, "面试时间必须人来定", r_itv["needs_human"])

r_sal = draft.reply_draft("薪资 30-40K 能接受吗？", job, cfg)
print(f"  salary -> {r_sal['draft'] or '(留空)'}")
check(r_sal["intent"] == "salary", "薪资意图对", r_sal["intent"])
check("25-40K" in (r_sal["draft"] or ""), "薪资按 config 区间答，不报单点", r_sal["draft"])

r_q = draft.reply_draft("你做过什么项目？", job, cfg)
print(f"  question -> {r_q['draft']!r}")
check(r_q["intent"] == "question", "提问意图对", r_q["intent"])
check(r_q["needs_human"] is True, "具体提问要人答（不凭空编事实）", r_q["needs_human"])
check(r_q["draft"] == "", "提问不硬套模板，留空等人写", r_q["draft"])

r_rej = draft.reply_draft("已经招到人了", job, cfg)
print(f"  reject -> {r_rej['draft'] or '(留空)'}")
check(r_rej["intent"] == "reject", "婉拒意图对", r_rej["intent"])
check(bool(r_rej["draft"]) and r_rej["needs_human"] is False,
      "婉拒有收尾话且不用人管", (r_rej["draft"][:20], r_rej["needs_human"]))

print()
if fails:
    print(f"[x] {len(fails)} 项没过：{fails}")
    raise SystemExit(1)
print("[ok] draft 模块全绿")