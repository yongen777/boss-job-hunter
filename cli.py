#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""BOSS直聘求职猎手 —— 命令行入口。

用法一律是 `python cli.py <命令>`，不带环境变量、不装全局包，
整个目录拷到哪里都能跑。

    医生      doctor
    导入简历  resume import <文件>
    探针      probe
    搜岗      hunt --query "Java" --city 上海 --limit 30
    起草      draft --top 5
    投递      apply --top 5  /  apply --ids a,b,c
    收件箱    inbox  /  inbox --reply
    账本      ledger  /  ledger --events
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.parse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

from huntsman import config as cfgmod  # noqa: E402
from huntsman import draft as draftmod  # noqa: E402
from huntsman import ledger as ledgermod  # noqa: E402
from huntsman import match as matchmod  # noqa: E402
from huntsman import resume as resumemod  # noqa: E402
from huntsman.client import BridgeClient, BridgeError, quick_check  # noqa: E402

DATA = cfgmod.DATA_DIR
DRAFT_DIR = DATA / "drafts"

CITY_CODES = {
    "全国": "100010000",
    "北京": "101010100",
    "上海": "101020100",
    "天津": "101030100",
    "重庆": "101040100",
    "广州": "101280100",
    "深圳": "101280600",
    "杭州": "101210100",
    "成都": "101270100",
    "武汉": "101200100",
    "南京": "101190100",
    "西安": "101110100",
    "苏州": "101190400",
    "长沙": "101250100",
    "郑州": "101180100",
    "青岛": "101120200",
    "厦门": "101230200",
    "合肥": "101220100",
    "福州": "101230100",
    "济南": "101120100",
    "大连": "101070200",
    "宁波": "101210400",
    "无锡": "101190200",
    "东莞": "101281600",
    "佛山": "101280800",
}


# ---------------------------------------------------------------- 输出小工具


def _w(s) -> int:
    n = 0
    for ch in str(s):
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def _pad(s, width: int) -> str:
    s = "" if s is None else str(s)
    return s + " " * max(0, width - _w(s))


def table(rows: list[list], headers: list[str]) -> str:
    if not rows:
        return "（空）"
    widths = [_w(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], _w(cell))
    out = ["  ".join(_pad(h, widths[i]) for i, h in enumerate(headers)).rstrip()]
    out.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for r in rows:
        out.append("  ".join(_pad(c, widths[i]) for i, c in enumerate(r)).rstrip())
    return "\n".join(out)


def head(text: str) -> None:
    print()
    print("=" * 60)
    print(f"  {text}")
    print("=" * 60)


def say(text: str = "") -> None:
    print(text)


def warn(text: str) -> None:
    print(f"[!] {text}")


def die(text: str, code: int = 1) -> None:
    print(f"[x] {text}", file=sys.stderr)
    raise SystemExit(code)


def get_client() -> BridgeClient:
    client = BridgeClient()
    try:
        health = client.health()
    except BridgeError as exc:
        die(f"{exc}\n    先把桥启动起来：双击项目里的「起桥.cmd」")
    ext = health.get("extension", {})
    if not ext.get("connected"):
        die(
            "桥在跑，但扩展没连上。\n"
            "    1) 浏览器地址栏打开 edge://extensions ，确认「BOSS直聘求职猎手」是启用状态\n"
            "    2) 点扩展图标，把 data/.bridge-token 里那串配对码粘进去，点「保存并连接」\n"
            "    3) 再跑一次 python cli.py doctor"
        )
    return client


# ---------------------------------------------------------------- doctor


def cmd_doctor(args) -> int:
    head("体检")
    problems = 0
    notes = 0

    # Python
    v = sys.version_info
    if v >= (3, 9):
        print(f"[ok] Python {v.major}.{v.minor}.{v.micro}")
    else:
        print(f"[x] Python {v.major}.{v.minor}，需要 3.9 以上")
        problems += 1

    # 配置
    print(f"[ok] 项目目录 {cfgmod.PROJECT_ROOT}")
    try:
        cfg = cfgmod.load_config()
        print(f"[ok] 配置文件 {cfgmod.CONFIG_PATH}")
    except Exception as exc:
        print(f"[x] 配置读不出来：{exc}")
        return 1

    # 简历
    try:
        text = resumemod.load_resume_text()
        skills = resumemod.load_resume_skills()
        print(f"[ok] 简历已导入：{len(text)} 字，认出 {len(skills)} 个技能词")
        if len(skills) < 5:
            warn("技能词太少，匹配会不准。可以在 data/skills.txt 里补你自己的技术栈")
            notes += 1
    except FileNotFoundError:
        print("[ ] 还没导入简历 -> python cli.py resume import 简历.pdf")
        notes += 1
        skills = []

    # 个人信息
    cand = cfg.get("candidate", {})
    missing = [k for k in ("name", "years_experience", "current_city") if not cand.get(k)]
    if missing:
        print(f"[!] config.json 的 candidate 里还空着：{'、'.join(missing)}")
        notes += 1
    else:
        print(f"[ok] 候选人信息齐全（{cand.get('name')}，{cand.get('years_experience')} 年）")
    if not (cand.get("salary_min_k") and cand.get("salary_max_k")):
        print("[!] 没设期望薪资（salary_min_k / salary_max_k），薪资这一项只能按中性算")
        notes += 1

    # pypdf
    try:
        import pypdf  # noqa: F401
        print(f"[ok] pypdf {pypdf.__version__}（读 PDF 用，装在 vendor/ 里）")
    except ImportError:
        print("[!] 读不了 PDF，只能导入 .docx / .txt。补：python -m pip install --target vendor pypdf")
        notes += 1

    # 桥
    qc = quick_check()
    if not qc["reachable"]:
        print(f"[x] 连不上桥（{qc['bridge_url']}）：{qc.get('error', '')}")
        print("    -> 双击「起桥.cmd」")
        problems += 1
    else:
        ext = qc.get("health", {}).get("extension", {})
        if qc.get("extension_connected"):
            print(f"[ok] 桥在跑，扩展已连上（{ext.get('id') or '未报 id'}，空闲 {ext.get('idle_sec')}s）")
        else:
            # 「没加载」和「加载了但没配对」在 health 里都长成 connected=false，
            # 唯一的区别是有没有报过 id。既然分得出来就别含糊其辞。
            print(f"[x] 桥在跑（{qc['bridge_url']}），但扩展没连上")
            ext_id = ext.get("id")
            idle = ext.get("idle_sec")
            if ext_id:
                print(f"    -> 配对过（id={ext_id}），最后一次心跳在 {idle} 秒前")
                print("       浏览器没开、扩展被禁用、或标签页全关了。先双击「启动浏览器.cmd」")
            else:
                print("    -> 还没配对过：edge://extensions 打开开发者模式 → 加载解压缩的扩展")
                print("       → 选 extension 目录 → 点扩展图标，把 data/.bridge-token 里的值粘进去")
            problems += 1

    # 浏览器
    br = cfg.get("browser", {})
    exe = cfgmod.resolve_path(br.get("executable") or "", None)
    if exe and Path(exe).exists():
        print(f"[ok] 浏览器 {exe}")
    else:
        found = detect_browser(br.get("kind", "edge"))
        if found:
            print(f"[ok] 找到浏览器 {found}（用「启动浏览器.cmd」起）")
        else:
            print("[!] 没找到 Edge/Chrome，config.json 的 browser.executable 里手填路径")
            notes += 1

    # 账本
    try:
        conn = ledgermod.connect()
        s = ledgermod.summary(conn)
        print(f"[ok] 账本 {cfgmod.LEDGER_PATH}（{s['jobs_total']} 个岗位，今天发 {s['sent_today']} 条）")
    except Exception as exc:
        print(f"[x] 账本打不开：{exc}")
        problems += 1

    # 节奏
    sft = cfg.get("safety", {})
    print(
        f"[ok] 当前模式 mode={sft.get('mode')}，每日上限 {sft.get('daily_send_limit')} 条，"
        f"间隔 ≥{sft.get('min_interval_sec')} 秒"
    )
    if sft.get("mode") == "auto":
        warn("mode=auto：apply 会自己点投递按钮。想先看看就把 config.json 改成 confirm")
        notes += 1
    else:
        print("     想全自动投递，把 config.json 的 safety.mode 改成 auto")

    print()
    if problems:
        print(f"结论：{problems} 个问题要先修，{notes} 条提醒")
        return 1
    print(f"结论：能跑（{notes} 条提醒）")
    return 0


def detect_browser(kind: str = "edge") -> str | None:
    candidates = []
    if kind == "chrome":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:
        candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


# ---------------------------------------------------------------- resume


def cmd_resume_import(args) -> int:
    src = Path(args.path).expanduser()
    if not src.is_absolute():
        src = Path.cwd() / src
    if not src.exists():
        die(f"找不到文件：{src}")
    try:
        result = resumemod.import_resume(src)
    except ValueError as exc:
        die(str(exc))
    head("简历已导入")
    say(f"来源      {result['source']}")
    say(f"存档      {result['copied_to']}")
    say(f"文本      {result['chars']} 字")
    fields = result["fields"]
    say(f"姓名      {fields.get('name') or '（没认出来，去 config.json 手填）'}")
    say(f"电话      {fields.get('phone') or '（没认出来）'}")
    say(f"邮箱      {fields.get('email') or '（没认出来）'}")
    say(f"年限      {fields.get('years_experience') if fields.get('years_experience') is not None else '（没认出来）'}")
    say(f"学历      {fields.get('education') or '（没认出来）'}")
    say(f"城市      {'、'.join(fields.get('cities') or []) or '（没认出来）'}")
    skills = result["skills"]
    say(f"技能      {len(skills)} 个")
    if skills:
        line = "、".join(skills[:30])
        say(f"          {line}{' …' if len(skills) > 30 else ''}")
    if result["config_filled"]:
        say(f"回填      config.json 的 {', '.join(result['config_filled'])}")
    say("")
    say("文本预览：")
    say("-" * 60)
    for line in (result["text_head"] or "").splitlines()[:15]:
        say("  " + line)
    say("-" * 60)
    if len(skills) < 5:
        warn("技能词偏少。若你的技术栈没被认出来，把它们一行一个写进 data/skills.txt")
    return 0


# ---------------------------------------------------------------- probe


# 当前页面是哪种页 → 必需的控件分组。缺一个就说明改版了，得当场报出来，
# 而不是打一张全是 0 的表然后退出码 0（那是假绿）。
PROBE_REQUIRED = {
    "list": {
        "label": "岗位列表页",
        "groups": {
            "jobCard": "岗位卡片",
            "jobName": "卡片里的岗位名",
            "jobCompany": "卡片里的公司名",
            "jobLink": "卡片里指向 job_detail 的链接",
        },
    },
    "detail": {
        "label": "职位详情页",
        "groups": {
            "detailTitle": "职位标题",
            "detailSalary": "薪资",
            "detailDesc": "职位描述正文",
            "detailCompany": "公司信息",
            "greetButton": "「立即沟通」按钮",
        },
    },
    "chat": {
        "label": "聊天页",
        "groups": {
            "chatList": "会话列表",
            "chatMessages": "消息气泡列表",
            "chatInput": "输入框",
            "chatSendButton": "发送按钮",
        },
    },
}


def guess_page_kind(url: str) -> str:
    u = (url or "").lower()
    if "/job_detail/" in u or "/job_detail" in u:
        return "detail"
    if "/web/geek/chat" in u:
        return "chat"
    if "/web/geek/job" in u or "/web/geek/jobs" in u:
        return "list"
    return "unknown"


def probe_verdict(kind: str, hits: dict) -> tuple[str, list[tuple[str, str, int]]]:
    """看这一页需要的控件还在不在。返回（页面中文名，缺的控件清单）。

    缺的每个控件都带名字和「试过几个候选」，这样报错能指名道姓，
    而不是丢一句"选择器失效"让人自己猜。
    """
    spec = PROBE_REQUIRED[kind]
    missing: list[tuple[str, str, int]] = []
    for group, human in spec["groups"].items():
        entries = hits.get(group) or []
        if not any((e.get("count") or 0) > 0 for e in entries):
            missing.append((group, human, len(entries)))
    return spec["label"], missing


def cmd_probe(args) -> int:
    client = get_client()
    head("页面探针")
    res = client.call("probe.dump", {"textChars": 1200}, timeout=60)
    if not res.get("ok"):
        die(f"探针失败（{res.get('code')}）：{res.get('error')}")
    data = res.get("data") or {}
    say(f"页面      {data.get('title')}")
    say(f"地址      {data.get('url')}")
    say(f"加载      {data.get('readyState')}")
    risk = data.get("risk") or {}
    if risk.get("blocked"):
        warn(f"页面被拦住了：{risk.get('kind')} —— {risk.get('detail')}")
    counts = data.get("counts") or {}
    say(f"元素      链接 {counts.get('links')}，按钮 {counts.get('buttons')}，岗位链接 {counts.get('jobDetailLinks')}")
    hits = data.get("hits") or {}
    if hits:
        say("")
        say("选择器命中情况（0 的要改 selectors.js）：")
        rows = []
        for group, entries in sorted(hits.items()):
            best = max((e.get("count") or 0) for e in entries) if entries else 0
            first = next((e for e in entries if (e.get("count") or 0) > 0), None)
            rows.append([group, best, first.get("sel") if first else "（全都没命中）"])
        say(table(rows, ["分组", "命中", "首个可用的选择器"]))
        zero = [g for g, e in hits.items() if not any((x.get("count") or 0) > 0 for x in e)]
        if zero:
            say("")
            warn(f"{len(zero)} 个分组一个都没命中：{'、'.join(sorted(zero))}")
            say("    页面改版了的话，用下面的 classTop 找新特征，改 extension/selectors.js")
            if data.get("readyState") and data["readyState"] != "complete":
                say(f"    另外：readyState 不是 complete（当前 {data['readyState']}），页面还没加载完 —— 等两秒再测一次")
    top = data.get("classTop") or []
    if top and args.classes:
        say("")
        say("页面上出现最多的 class（找新选择器用）：")
        for cls, n in top[: args.classes]:
            say(f"  {n:>5}  {cls}")
    if args.text:
        say("")
        say("正文前段：")
        say("-" * 60)
        say((data.get("textHead") or "")[: args.text])
        say("-" * 60)

    # ---- 结论：这一页需要的控件都在吗 ----
    kind = guess_page_kind(data.get("url") or "")
    say("")
    if risk.get("blocked"):
        die(f"页面被拦住了（{risk.get('kind')}），选择器验不了。先在浏览器里过了这一关再来。")
    if kind == "unknown":
        warn(f"认不出这是什么页（{data.get('url')}），只列命中情况，不做结论。")
        say("    能认的是：/web/geek/job（列表）、/job_detail/（详情）、/web/geek/chat（聊天）")
        return 0

    spec = PROBE_REQUIRED[kind]
    label, missing = probe_verdict(kind, hits)
    if missing:
        warn(f"这是{label}，但有 {len(missing)} 个必需控件一个都没命中：")
        for group, human, tried in missing:
            say(f"    缺 {human}（分组 {group}）—— {tried} 个候选选择器全 0")
        die(f"版式对不上，selectors.js 要按这一页的 classTop 重新校准（{label}）")
    print(f"[ok] {label}：{len(spec['groups'])} 个必需控件都命中了")
    return 0


# ---------------------------------------------------------------- hunt


def resolve_city(city: str | None) -> str | None:
    if not city:
        return None
    city = city.strip()
    if city.isdigit():
        return city
    return CITY_CODES.get(city)


def cmd_hunt(args) -> int:
    cfg = cfgmod.load_config()
    try:
        resume_text = resumemod.load_resume_text()
    except FileNotFoundError as exc:
        die(str(exc))
    skills = set(resumemod.load_resume_skills())
    if not skills:
        warn("简历里一个技能词都没认出来，匹配分数会偏低")

    code = resolve_city(args.city)
    if args.city and not code:
        die(
            f"不认识城市「{args.city}」。\n"
            f"    内置的：{'、'.join(list(CITY_CODES)[:12])} …\n"
            "    也可以用 --city-code 直接给 BOSS 的城市数字码"
        )
    if args.city_code:
        code = args.city_code

    url = "https://www.zhipin.com/web/geek/job?query=" + urllib.parse.quote(args.query)
    if code:
        url += "&city=" + code

    client = get_client()
    head(f"搜岗：{args.query}" + (f" @ {args.city}" if args.city else ""))
    say(f"打开 {url}")
    res = client.call("navigate", {"url": url, "settleMs": 2500}, timeout=60)
    if not res.get("ok"):
        die(f"打不开搜索页（{res.get('code')}）：{res.get('error')}")

    say(f"采集岗位卡片（最多 {args.limit} 个）…")
    res = client.call("jobs.collect", {"limit": args.limit}, timeout=120)
    if not res.get("ok"):
        code_ = res.get("code")
        if code_ in ("captcha", "login", "risk"):
            die(f"页面被拦住了（{code_}）：{res.get('error')}\n    去浏览器里手动过一下验证/登录，再重跑")
        die(f"采集失败（{code_}）：{res.get('error')}")
    data = res.get("data") or {}
    items = data.get("items") or []
    say(f"拿到 {len(items)} 个岗位（页面共 {data.get('totalCards')} 张卡片）")
    if not items:
        die("一个都没拿到。跑 python cli.py probe 看看选择器是不是失效了")

    hits = data.get("selectorHit")
    if hits and min(hits.values() or [0]) == 0:
        warn("有字段没采到，跑 probe 校准一下 selectors.js")

    # 详情页补齐（慢，且更容易触发风控，只对前 N 个做）
    detail_n = int(args.detail_top or 0)
    if detail_n > 0:
        say(f"进详情页补 JD（前 {detail_n} 个，每个约 2 秒）…")
        # 先粗排一遍，把最可能是好岗位的挑出来看详情
        rough = matchmod.rank_jobs(items, skills, cfg)
        reads = 0
        desc_missing = 0
        for i, job in enumerate(rough[:detail_n], 1):
            try:
                r = client.call("navigate", {"url": job.get("href"), "settleMs": 1800}, timeout=60)
                if not r.get("ok"):
                    warn(f"{job.get('title')} 打开失败：{r.get('error')}")
                    continue
                r2 = client.call("job.read", {}, timeout=60)
                if r2.get("ok"):
                    reads += 1
                    detail = r2.get("data") or {}
                    job["desc"] = detail.get("desc") or ""
                    job["tags"] = detail.get("tags") or job.get("tags")
                    job["hrName"] = detail.get("hrName")
                    job["hrTitle"] = detail.get("hrTitle")
                    job["address"] = detail.get("address")
                    if job["desc"]:
                        print(f"  [{i}/{detail_n}] {job.get('company')} · {job.get('title')} — {len(job['desc'])} 字")
                    else:
                        desc_missing += 1
                        warn(f"  [{i}/{detail_n}] {job.get('title')} 详情页没读到职位描述 —— 这条评分会偏低")
                else:
                    warn(f"{job.get('title')} 读详情失败：{r2.get('error')}")
            except BridgeError as exc:
                warn(f"详情页出错：{exc}")
            time.sleep(1.5 + random.random())
        # 一条读不到可能是页面本身没写 JD；条条都读不到就是选择器失效了，别当成功往下走
        if reads and desc_missing == reads:
            die("详情页全都没读到职位描述 —— selectors.js 里的 detailDesc 多半失效了，先跑 probe 确认再重来")

    ranked = matchmod.rank_jobs(items, skills, cfg)
    conn = ledgermod.connect()
    new_count = 0
    for job in ranked:
        if ledgermod.upsert_job(conn, job):
            new_count += 1
    ledgermod.log_event(conn, "hunt", f"{args.query} @ {args.city or '不限'} → {len(items)} 个，新增 {new_count}")

    apply_list = [j for j in ranked if j["match"]["verdict"] == "apply"]
    head("结果")
    rows = []
    for job in ranked[: args.show]:
        m = job["match"]
        rows.append([
            f"{m['score']:.0f}",
            m["verdict"],
            (job.get("title") or "")[:22],
            (job.get("company") or "")[:16],
            job.get("salary") or "",
            (job.get("area") or "").split("·")[0],
        ])
    say(table(rows, ["分", "判定", "职位", "公司", "薪资", "城市"]))
    say("")
    say(f"新增 {new_count} 个岗位入库；{len(apply_list)} 个达到投递线（{cfg['matching']['min_score_to_apply']} 分）")
    if apply_list:
        say("下一步：python cli.py draft --top 5")
    return 0


# ---------------------------------------------------------------- draft


def _greeting_for(job: dict, resume_text: str, skills: list[str], cfg: dict) -> dict:
    """优先用 agent 写好的文件，没有才用模板兜底。"""
    path = DRAFT_DIR / f"{job['job_id']}.txt"
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return {
                "text": text,
                "chars": len(text),
                "source": str(path),
                "violations": draftmod.check_greeting(text, cfg),
                "facts": {},
            }
    built = draftmod.build_greeting(job, resume_text, skills, cfg)
    built["source"] = "模板"
    return built


def cmd_draft(args) -> int:
    cfg = cfgmod.load_config()
    try:
        resume_text = resumemod.load_resume_text()
    except FileNotFoundError as exc:
        die(str(exc))
    skills = resumemod.load_resume_skills()

    conn = ledgermod.connect()
    if args.job_id:
        job = ledgermod.get_job(conn, args.job_id)
        if not job:
            die(f"账本里没有岗位 {args.job_id}")
        jobs = [job]
    else:
        jobs = ledgermod.list_jobs(conn, status="new", min_score=cfg["matching"]["min_score_to_apply"],
                                   limit=args.top)
    if not jobs:
        die("没有待起草的岗位。先跑 python cli.py hunt ...")

    head(f"起草招呼语（{len(jobs)} 个）")
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for job in jobs:
        job_full = dict(job)
        if job.get("match_json"):
            try:
                job_full["match"] = json.loads(job["match_json"])
            except json.JSONDecodeError:
                job_full["match"] = {}
        built = _greeting_for(job_full, resume_text, skills, cfg)
        path = DRAFT_DIR / f"{job['job_id']}.txt"
        if not path.exists():
            path.write_text(built["text"] + "\n", encoding="utf-8")
        ledgermod.set_status(conn, job["job_id"], "drafted", built["text"])

        say("")
        say(f"── {job.get('company')} · {job.get('title')}  [{job.get('score')}]")
        say(f"   {job.get('salary')}  {job.get('area')}  {job.get('url')}")
        say(f"   草稿（{built['chars']} 字，来源 {built['source']}）：")
        for line in built["text"].splitlines():
            say(f"     {line}")
        for v in built["violations"]:
            warn(f"   {v}")
        if built.get("facts"):
            facts = built["facts"]
            if facts.get("evidence"):
                say(f"   依据：{' / '.join(facts['evidence'])}")
            if facts.get("matched_skills"):
                say(f"   命中技能：{'、'.join(facts['matched_skills'][:8])}")
            if facts.get("style"):
                say(f"   口味：{facts['style']}（agent 改写时照这个来）")
        say(f"   存到 {path}")
        out.append({"job_id": job["job_id"], "text": built["text"]})

    say("")
    say(f"共 {len(out)} 条。改文案直接编辑 data/drafts/<岗位id>.txt；")
    say("想让 agent 写得更自然，跑：python cli.py draft --json 把原料喂给它。")
    if args.json:
        say("")
        say(json.dumps(out, ensure_ascii=False, indent=2))
    ledgermod.log_event(conn, "draft", f"{len(out)} 条")
    return 0


# ---------------------------------------------------------------- apply


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes", "是", "1")


def _select_jobs(conn, cfg, args) -> list[dict]:
    if args.ids:
        ids = [x.strip() for x in args.ids.split(",") if x.strip()]
        jobs = []
        for jid in ids:
            row = ledgermod.get_job(conn, jid)
            if not row:
                warn(f"账本里没有 {jid}，跳过")
                continue
            jobs.append(row)
        return jobs
    status = args.status or "drafted"
    jobs = ledgermod.list_jobs(conn, status=status,
                               min_score=cfg["matching"]["min_score_to_apply"],
                               limit=args.top)
    return jobs


def cmd_apply(args) -> int:
    cfg = cfgmod.load_config()
    mode = (cfg.get("safety", {}).get("mode") or "confirm").lower()

    if mode == "draft":
        die(
            "当前 safety.mode = draft，apply 不会动浏览器。\n"
            "    要么把 config.json 的 safety.mode 改成 confirm（每条问你一句）或 auto（全自动），\n"
            "    要么就用 python cli.py draft 出文案，自己去页面上发。"
        )
    if mode not in ("confirm", "auto"):
        die(f"config.json 里 safety.mode 的值「{mode}」不认识，只能是 draft / confirm / auto")

    conn = ledgermod.connect()
    jobs = _select_jobs(conn, cfg, args)
    if not jobs:
        die("没有可投的岗位。先 hunt，再 draft。")

    try:
        resume_text = resumemod.load_resume_text()
    except FileNotFoundError as exc:
        die(str(exc))
    skills = resumemod.load_resume_skills()

    head(f"准备投递 {len(jobs)} 个岗位（mode={mode}）")
    if mode == "auto":
        warn("auto 模式：会自己点「打招呼」。全程别去抢那个浏览器窗口。")
    if args.dry_run:
        say("--dry-run：只演练，不打开页面、不点按钮。")

    if mode == "confirm" and not args.dry_run:
        if not sys.stdin.isatty():
            die(
                "mode=confirm 要你在旁边一条条按 y 确认，但当前不是交互终端。\n"
                "    想全自动：把 config.json 的 safety.mode 改成 auto\n"
                "    想先看稿子：python cli.py draft --top 5"
            )

    client = None if args.dry_run else get_client()
    sent = 0
    skipped = 0
    failed = 0

    for idx, job in enumerate(jobs, 1):
        job_full = dict(job)
        if job.get("match_json"):
            try:
                job_full["match"] = json.loads(job["match_json"])
            except json.JSONDecodeError:
                pass
        built = _greeting_for(job_full, resume_text, skills, cfg)
        greeting = built["text"]
        title_line = f"[{idx}/{len(jobs)}] {job.get('company')} · {job.get('title')}  {job.get('salary') or ''}"

        say("")
        say(title_line)
        for line in greeting.splitlines():
            say(f"   > {line}")
        for v in built.get("violations") or []:
            warn(f"   文案有问题：{v}")

        if args.dry_run:
            ok, reason, _ = ledgermod.can_send(conn, cfg)
            say(f"   [dry] 现在能不能发：{'能' if ok else '不能'} —— {reason}")
            continue

        ok, reason, wait = ledgermod.can_send(conn, cfg)
        if not ok:
            if wait and wait <= 120:
                say(f"   等 {wait} 秒（{reason}）")
                time.sleep(wait + 1)
                ok, reason, wait = ledgermod.can_send(conn, cfg)
            if not ok:
                warn(f"停：{reason}")
                say("   剩下的下次再投。")
                break
        say(f"   {reason}")

        if mode == "confirm":
            if not _confirm("   点「打招呼」投出去？[y/N] "):
                say("   跳过")
                ledgermod.set_status(conn, job["job_id"], "skipped", "人工跳过")
                skipped += 1
                continue

        # 1) 打开岗位页
        try:
            res = client.call("navigate", {"url": job.get("url"), "settleMs": 1800}, timeout=60)
        except BridgeError as exc:
            warn(f"   打不开：{exc}")
            failed += 1
            continue
        if not res.get("ok"):
            warn(f"   打不开（{res.get('code')}）：{res.get('error')}")
            failed += 1
            if res.get("code") in ("captcha", "login", "risk"):
                ledgermod.log_event(conn, "blocked", f"{res.get('code')}: {res.get('error')}")
                say("   浏览器那边被拦住了，先去人工处理，这次到此为止。")
                break
            continue

        # 2) 点打招呼
        try:
            res = client.call("greet.click", {}, timeout=60)
        except BridgeError as exc:
            warn(f"   点按钮出错：{exc}")
            failed += 1
            continue
        if not res.get("ok"):
            code_ = res.get("code")
            if code_ == "no-button":
                warn("   页面上找不到「打招呼」按钮 —— 可能已投过、或者这是猎头岗")
                ledgermod.set_status(conn, job["job_id"], "skipped", "没有打招呼按钮")
                skipped += 1
            elif code_ in ("captcha", "login", "risk"):
                ledgermod.log_event(conn, "blocked", f"{code_}: {res.get('error')}")
                warn(f"   被拦住了（{code_}）：{res.get('error')}")
                say("   停手，去浏览器里人工过一下。")
                break
            else:
                warn(f"   失败（{code_}）：{res.get('error')}")
                failed += 1
            continue
        data = res.get("data") or {}
        if data.get("already"):
            say(f"   之前就聊过（按钮写着「{data.get('label')}」），不重复投")
            ledgermod.set_status(conn, job["job_id"], "duplicate", data.get("label") or "")
            skipped += 1
            continue

        # 3) 补一条自己的话
        verified = False
        error_text = ""
        if not args.no_greeting:
            try:
                res2 = client.call("chat.send", {"text": greeting}, timeout=90)
            except BridgeError as exc:
                res2 = {"ok": False, "code": "bridge", "error": str(exc)}
            if res2.get("ok"):
                verified = bool((res2.get("data") or {}).get("verified"))
                if verified:
                    say("   自定义招呼已发出，气泡已确认")
                else:
                    warn(f"   {(res2.get('data') or {}).get('warning') or '输入框清了但没看到气泡，请人工核对'}")
            else:
                error_text = res2.get("error") or ""
                warn(f"   自定义招呼没发出去（{res2.get('code')}）：{error_text}")
                if res2.get("code") in ("captcha", "login", "risk"):
                    ledgermod.record_application(conn, job["job_id"], greeting, True, False, error_text)
                    ledgermod.log_event(conn, "blocked", f"{res2.get('code')}: {error_text}")
                    say("   停手。")
                    break

        ledgermod.record_application(conn, job["job_id"], greeting, True, verified, error_text)
        ledgermod.mark_session_start(conn)
        sent += 1

        if idx < len(jobs):
            gap = int(cfg["safety"]["min_interval_sec"]) + random.randint(0, 7)
            say(f"   歇 {gap} 秒")
            time.sleep(gap)

    head("投递结果")
    say(f"发出 {sent} 条，跳过 {skipped} 条，失败 {failed} 条")
    s = ledgermod.summary(conn)
    say(f"今天累计 {s['sent_today']} 条（上限 {cfg['safety']['daily_send_limit']}）")
    if s["unverified"]:
        warn(f"有 {s['unverified']} 条没确认到气泡，去 zhipin.com/web/geek/chat 人工看一眼")
    if args.dry_run:
        say("（dry-run，什么都没发）")
    return 0


# ---------------------------------------------------------------- inbox


def _job_for_chat(conn, item: dict) -> dict | None:
    """会话列表给的是 HR 名 + 公司，用公司名对回账本里的岗位。"""
    company = (item.get("company") or "").strip()
    if not company:
        return None
    row = conn.execute(
        "SELECT * FROM jobs WHERE company = ? ORDER BY "
        "CASE status WHEN 'applied' THEN 0 WHEN 'replied' THEN 1 ELSE 2 END, updated_at DESC LIMIT 1",
        (company,),
    ).fetchone()
    return dict(row) if row else None


def cmd_inbox(args) -> int:
    cfg = cfgmod.load_config()
    conn = ledgermod.connect()
    client = get_client()

    head("收件箱")
    res = client.call("chat.list", {}, timeout=60)
    if not res.get("ok"):
        code_ = res.get("code")
        if code_ in ("captcha", "login", "risk"):
            die(f"被拦住了（{code_}）：{res.get('error')}\n    去浏览器里人工过一下")
        die(f"拿不到会话列表（{code_}）：{res.get('error')}")
    items = (res.get("data") or {}).get("items") or []
    say(f"{len(items)} 个会话，其中 {sum(1 for i in items if i.get('unread'))} 个有未读")

    targets = [i for i in items if i.get("unread")] if args.unread_only else items
    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        say("没有要读的会话。")
        return 0

    new_msgs = 0
    replies = []
    for i, item in enumerate(targets, 1):
        name = item.get("name") or "?"
        company = item.get("company") or ""
        try:
            r = client.call("chat.open", {"index": item.get("index")}, timeout=60)
            if not r.get("ok"):
                warn(f"[{i}] {company} · {name} 打不开：{r.get('error')}")
                continue
            r2 = client.call("chat.read", {"limit": 12}, timeout=60)
            if not r2.get("ok"):
                warn(f"[{i}] 读消息失败：{r2.get('error')}")
                continue
        except BridgeError as exc:
            warn(f"[{i}] 出错：{exc}")
            continue

        msgs = (r2.get("data") or {}).get("messages") or []
        job = _job_for_chat(conn, item)
        job_id = job["job_id"] if job else f"chat:{company or name}"
        say("")
        say(f"── {company} · {name}" + (f"  [{job['title']}]" if job else "  （账本里没有对应岗位）"))

        added = 0
        for m in msgs:
            text = (m.get("text") or "").strip()
            if not text:
                continue
            side = m.get("side") or "unknown"
            if ledgermod.save_message(conn, job_id, side, text):
                added += 1
                if side == "them":
                    new_msgs += 1
        say(f"   {len(msgs)} 条消息，新的 {added} 条")
        for m in msgs[-4:]:
            who = "我" if m.get("side") == "me" else ("对方" if m.get("side") == "them" else "?")
            text = (m.get("text") or "").replace("\n", " ")[:70]
            say(f"   {who}: {text}")
        if job:
            ledgermod.set_status(conn, job["job_id"], "replied" if new_msgs else job["status"])

        if args.reply:
            theirs = [m for m in msgs if m.get("side") == "them"]
            if theirs:
                d = draftmod.reply_draft(theirs[-1].get("text") or "", job or {}, cfg,
                                          history=[{"side": m.get("side"), "text": m.get("text")} for m in msgs])
                replies.append({"chat": f"{company} · {name}", **d})
        time.sleep(1.2 + random.random())

    head("汇总")
    say(f"新收到对方消息 {new_msgs} 条")
    pending = ledgermod.pending_replies(conn)
    if pending:
        say(f"账本里还没处理的对方消息 {len(pending)} 条")

    if replies:
        say("")
        say("回复草稿（意图 → 建议）：")
        for r in replies:
            say("")
            say(f"── {r['chat']}  意图={r['intent']}")
            say(f"   对方：{(r['incoming'] or '').replace(chr(10), ' ')[:80]}")
            if r["draft"]:
                say(f"   草稿：{r['draft']}")
            else:
                say("   草稿：（这类不自动回）")
            say(f"   提示：{r['hint']}")
            if r["needs_human"]:
                warn("   需要人来定")
        say("")
        say("这些只是草稿 —— 真要回，把话复制到浏览器聊天窗里发，或者让 agent 写好再发。")
    ledgermod.log_event(conn, "inbox", f"读 {len(targets)} 个会话，新消息 {new_msgs}")
    return 0


# ---------------------------------------------------------------- ledger


def cmd_ledger(args) -> int:
    conn = ledgermod.connect()
    s = ledgermod.summary(conn)
    head("账本")
    say(f"岗位总数   {s['jobs_total']}")
    if s["by_status"]:
        say(f"状态分布   " + "，".join(f"{k} {v}" for k, v in sorted(s["by_status"].items())))
    say(f"今天已发   {s['sent_today']} 条")
    say(f"累计已发   {s['sent_total']} 条")
    if s["unverified"]:
        warn(f"未确认气泡 {s['unverified']} 条（发出去过，但没看到气泡）")
    if s["failed"]:
        say(f"失败       {s['failed']} 条")
    say(f"待回消息   {s['pending_replies']} 条")
    if s["last_send"]:
        say(f"上次投递   {s['last_send']}")

    if args.events:
        say("")
        say("最近事件：")
        rows = [[e["at"].replace("T", " "), e["kind"], (e["detail"] or "")[:50]]
                for e in ledgermod.recent_events(conn, 20)]
        say(table(rows, ["时间", "类型", "说明"]))
        return 0

    status = args.status
    jobs = ledgermod.list_jobs(conn, status=status, limit=args.limit, order="time" if not status else "score")
    if jobs:
        say("")
        rows = []
        for j in jobs:
            rows.append([
                (j.get("job_id") or "")[:10],
                (f"{j.get('score'):.0f}" if j.get("score") is not None else "-"),
                j.get("status") or "",
                (j.get("title") or "")[:20],
                (j.get("company") or "")[:14],
                (j.get("updated_at") or "").replace("T", " ")[5:16],
            ])
        say(table(rows, ["id", "分", "状态", "职位", "公司", "更新时间"]))
    return 0


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cli.py",
        description="BOSS直聘求职猎手 —— 简历对 JD、起草招呼语、按节奏投递、收 HR 消息。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "常见流程：\n"
            "  python cli.py doctor\n"
            "  python cli.py resume import 简历.pdf\n"
            "  python cli.py hunt --query \"Java\" --city 上海 --limit 30 --detail-top 6\n"
            "  python cli.py draft --top 5\n"
            "  python cli.py apply --top 5 --dry-run\n"
            "  python cli.py apply --top 5\n"
            "  python cli.py inbox --reply\n"
        ),
    )
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("doctor", help="体检：环境、配置、桥、扩展、登录")
    sp.set_defaults(func=cmd_doctor)

    rp = sub.add_parser("resume", help="简历相关")
    rsub = rp.add_subparsers(dest="action")
    ri = rsub.add_parser("import", help="导入简历（pdf / docx / txt / md）")
    ri.add_argument("path", help="简历文件路径")
    ri.set_defaults(func=cmd_resume_import)

    pp = sub.add_parser("probe", help="探针：看页面选择器有没有失效")
    pp.add_argument("--classes", type=int, default=0, help="顺带打印 N 个高频 class")
    pp.add_argument("--text", type=int, default=0, help="顺带打印正文前 N 字")
    pp.set_defaults(func=cmd_probe)

    hp = sub.add_parser("hunt", help="搜岗、打分、入库")
    hp.add_argument("--query", required=True, help="搜索关键词，比如 \"Java\" \"数据分析\"")
    hp.add_argument("--city", default="", help="城市名（上海/北京/深圳…）")
    hp.add_argument("--city-code", default="", help="BOSS 的城市数字码，覆盖 --city")
    hp.add_argument("--limit", type=int, default=30, help="最多采多少个岗位卡片（默认 30）")
    hp.add_argument("--detail-top", type=int, default=0, help="对前 N 个岗位进详情页补 JD（慢）")
    hp.add_argument("--show", type=int, default=20, help="结果表显示多少行（默认 20）")
    hp.set_defaults(func=cmd_hunt)

    dp = sub.add_parser("draft", help="生成招呼语草稿")
    dp.add_argument("--top", type=int, default=5, help="给分最高的 N 个岗位起草（默认 5）")
    dp.add_argument("--job-id", default="", help="只给这一个岗位起草")
    dp.add_argument("--json", action="store_true", help="额外输出 JSON，方便 agent 改写")
    dp.set_defaults(func=cmd_draft)

    ap = sub.add_parser("apply", help="投递（真点浏览器按钮）")
    ap.add_argument("--top", type=int, default=3, help="按分数取前 N 个（默认 3）")
    ap.add_argument("--ids", default="", help="指定岗位 id，逗号分隔")
    ap.add_argument("--status", default="drafted", help="从哪个状态里取（默认 drafted）")
    ap.add_argument("--dry-run", action="store_true", help="只演练，不打开浏览器")
    ap.add_argument("--no-greeting", action="store_true", help="只点「打招呼」，不补自定义文案")
    ap.set_defaults(func=cmd_apply)

    ip = sub.add_parser("inbox", help="收 HR 消息")
    ip.add_argument("--reply", action="store_true", help="顺带给回复草稿")
    ip.add_argument("--unread-only", action="store_true", help="只处理有未读的会话")
    ip.add_argument("--limit", type=int, default=0, help="最多看 N 个会话")
    ip.set_defaults(func=cmd_inbox)

    lp = sub.add_parser("ledger", help="看账本")
    lp.add_argument("--events", action="store_true", help="看事件日志")
    lp.add_argument("--status", default="", help="按状态过滤")
    lp.add_argument("--limit", type=int, default=20)
    lp.set_defaults(func=cmd_ledger)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    cfgmod.ensure_dirs()
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        say("")
        say("中断了。已经发出去的都记在账本里，python cli.py ledger 可以查。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())