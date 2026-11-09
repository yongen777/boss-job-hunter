"""probe 的判定逻辑：认页面 + 点出缺了哪个控件。

probe 以前只要跑到就会打一张表然后 exit 0 —— 全是 0 也是 0，那是假绿。
这里盯住两件真事：
  1. 认得出页面种类（认不出就明说认不出，不硬下结论）
  2. 必需控件缺了要能指名道姓，并且驱动 exit 1
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli  # noqa: E402

fails: list[str] = []


def check(ok: bool, what: str, detail: str = "") -> None:
    if ok:
        print(f"[ok] {what}")
    else:
        print(f"[x] {what}" + (f" —— {detail}" if detail else ""))
        fails.append(what)


def hit(n: int) -> list[dict]:
    return [{"sel": ".x", "count": n}]


def all_hit(groups: dict) -> dict:
    return {g: hit(3) for g in groups}


# ---- 1. 认页面 ----
cases = [
    ("https://www.zhipin.com/web/geek/job?query=java&city=101020100", "list"),
    ("https://www.zhipin.com/web/geek/job", "list"),
    ("https://www.zhipin.com/job_detail/abc123.html", "detail"),
    ("https://www.zhipin.com/job_detail/?query=java", "detail"),
    ("https://www.zhipin.com/web/geek/chat", "chat"),
    ("https://www.zhipin.com/web/geek/chat?id=7", "chat"),
    ("https://www.zhipin.com/web/geek/resume", "unknown"),
    ("https://www.zhipin.com/", "unknown"),
    ("", "unknown"),
]
bad = [(u, want, cli.guess_page_kind(u)) for u, want in cases if cli.guess_page_kind(u) != want]
check(not bad, f"认页面 {len(cases)} 例都对", f"{len(bad)} 例不对：{bad[:3]}")

# job_detail 优先于 /web/geek/job：URL 里两个都有时不能认成列表页
check(
    cli.guess_page_kind("https://www.zhipin.com/web/geek/job?x=/job_detail/y") == "detail",
    "同时含着两段路径时认成详情页",
)

# ---- 2. 全命中 → 不报缺 ----
for kind in ("list", "detail", "chat"):
    groups = cli.PROBE_REQUIRED[kind]["groups"].keys()
    label, missing = cli.probe_verdict(kind, all_hit(groups))
    check(not missing, f"{label}：必需控件全在时不报缺", f"却报了 {missing}")
    check(label != "", f"{kind} 有中文名", "名字是空的")

# ---- 3. 少一个 → 只报那个，且带分组名和人话名字 ----
groups = list(cli.PROBE_REQUIRED["list"]["groups"].keys())
partial = all_hit(groups)
partial[groups[0]] = [{"sel": ".job-card-wrapper", "count": 0}, {"sel": ".job-list-box li", "count": 0}]
label, missing = cli.probe_verdict("list", partial)
check(len(missing) == 1, "少一个就只报一个", f"报了 {len(missing)} 个：{missing}")
if missing:
    g, human, tried = missing[0]
    check(g == groups[0], "报的是缺的那组", f"报成 {g}")
    check(tried == 2, "带上试过几个候选", f"tried={tried}")
    check(human and human != g, "带人话名字而不是分组代号", f"human={human!r}")

# ---- 4. 全空 → 全报 ----
label, missing = cli.probe_verdict("detail", {})
check(
    len(missing) == len(cli.PROBE_REQUIRED["detail"]["groups"]),
    "全空时把每个必需控件都报出来",
    f"只报了 {len(missing)} 个",
)

# ---- 5. 候选全报 -1（坏选择器）也算没命中 ----
label, missing = cli.probe_verdict("chat", {g: [{"sel": "((( ", "count": -1}] for g in cli.PROBE_REQUIRED["chat"]["groups"]})
check(len(missing) == len(cli.PROBE_REQUIRED["chat"]["groups"]), "坏选择器（count=-1）不算命中")

# ---- 6. hits 里多了不相干的分组不影响判定 ----
extra = all_hit(cli.PROBE_REQUIRED["list"]["groups"])
extra["captcha"] = hit(0)
extra["riskNotice"] = hit(0)
label, missing = cli.probe_verdict("list", extra)
check(not missing, "不相干分组为 0 不连累判定", f"却报了 {missing}")

# ---- 7. 三个页面各自要求的控件不一样（别把列表页的规则套到聊天页） ----
all_groups = {g for k in cli.PROBE_REQUIRED for g in cli.PROBE_REQUIRED[k]["groups"]}
label, missing = cli.probe_verdict("chat", all_hit(all_groups))
check(not missing, "把全站分组全填上时三页都算齐")

if fails:
    print()
    print(f"[x] {len(fails)} 项没通过：")
    for f in fails:
        print(f"    - {f}")
    raise SystemExit(1)

print()
print("[ok] probe 判定全绿")