"""岗位打分。

分数只在 0~100 之间，由几个可解释的维度加权得到。
每个维度都返回 (0~1 的值, 一句人话理由)，好让用户看得懂为什么推荐/不推荐。
"""

from __future__ import annotations

import re

from .resume import extract_skills, load_skill_dict

_BIG_COMPANIES = """
字节跳动 抖音 腾讯 阿里巴巴 蚂蚁 美团 京东 百度 网易 华为 小米 拼多多 滴滴
快手 小红书 B站 哔哩哔哩 携程 微博 知乎 360 商汤 旷视 大疆 蔚来 理想 小鹏
比亚迪 中兴 浪潮 用友 金蝶 科大讯飞 海康威视 大华 顺丰 菜鸟 SHEIN 米哈游
""".split()

_OUTSOURCE_WORDS = ("外包", "劳务派遣", "驻场", "人力外包", "外派", "派遣")

_SALARY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~到]\s*(\d+(?:\.\d+)?)\s*([Kk千万元])")
_YEARS_RE = re.compile(r"(\d+)\s*年(?:以上|及以上)?\s*(?:[^\d，。；！？\n]{0,8})?(?:工作)?(?:经验|开发经验|相关经验)")

_EDU_RANK = {"高中": 1, "中专": 1, "大专": 2, "专科": 2, "本科": 3, "硕士": 4, "研究生": 4, "博士": 5}


# ---------------------------------------------------------------- 小解析器


def parse_salary(text: str) -> tuple[float, float] | None:
    """'15-25K·14薪' → (15, 25)；'1.5-2万' → (15, 20)。认不出返回 None。"""
    if not text:
        return None
    t = text.replace(" ", "")
    m = _SALARY_RE.search(t)
    if m:
        lo, hi, unit = float(m.group(1)), float(m.group(2)), m.group(3)
        if unit in ("万",):
            lo, hi = lo * 10, hi * 10
        elif unit == "元":
            return None  # 日薪/时薪，不参与月薪比较
        if hi < lo:
            lo, hi = hi, lo
        return (lo, hi)
    m = re.search(r"(\d+)\s*[Kk千]", t)
    if m:
        v = float(m.group(1))
        return (v, v)
    return None


def parse_required_years(text: str) -> int | None:
    m = _YEARS_RE.search(text or "")
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    if "应届" in (text or "") or "经验不限" in (text or ""):
        return 0
    return None


def parse_city(area: str) -> str:
    if not area:
        return ""
    return re.split(r"[·\-—\s]", area.strip())[0]


# ---------------------------------------------------------------- 单维度


def _skill_fit(job: dict, resume_skills: set[str], cfg: dict) -> tuple[float, str]:
    text = f"{job.get('title','')} {job.get('desc','')} {' '.join(job.get('tags') or [])}"
    jd_skills = set(extract_skills(text, load_skill_dict()))
    if not jd_skills:
        return 0.5, "JD 里没抽到明确技能词，这一项按中性算"

    hit = jd_skills & resume_skills
    coverage = len(hit) / len(jd_skills)

    must = [s for s in (cfg.get("candidate", {}).get("must_have") or []) if s]
    must_hit = [s for s in must if s in hit]
    must_ratio = (len(must_hit) / len(must)) if must else coverage

    value = 0.7 * coverage + 0.3 * must_ratio
    # 没进详情页时只有标题和标签，证据不足，往中间拉一把
    if not job.get("desc"):
        value = 0.5 + (value - 0.5) * 0.55

    missing = sorted(jd_skills - resume_skills)
    reason = f"JD 技能 {len(jd_skills)} 项，命中 {len(hit)} 项"
    if hit:
        reason += f"（{'、'.join(sorted(hit)[:6])}）"
    if must and not must_hit:
        reason += f"；核心要求 {'、'.join(must[:3])} 一个都没对上"
    elif missing and len(missing) <= 6:
        reason += f"；没覆盖：{'、'.join(missing)}"
    return value, reason


def _experience_fit(job: dict, cand: dict) -> tuple[float, str]:
    need = parse_required_years(f"{job.get('desc','')} {job.get('title','')} {job.get('tags') or ''}")
    mine = cand.get("years_experience")
    if isinstance(mine, str) and mine.isdigit():
        mine = int(mine)
    if need is None:
        return 0.7, "JD 没写年限要求"
    if not isinstance(mine, int):
        return 0.6, f"JD 要 {need} 年，你还没填工作年限"
    diff = need - mine
    if diff <= 0:
        if mine - need >= 6:
            return 0.7, f"JD 要 {need} 年，你有 {mine} 年 —— 可能嫌你年限偏高/薪资对不上"
        return 1.0, f"JD 要 {need} 年，你有 {mine} 年，正好对上"
    if diff == 1:
        return 0.6, f"JD 要 {need} 年，你差 1 年"
    if diff == 2:
        return 0.3, f"JD 要 {need} 年，差 {diff} 年，偏险"
    return 0.1, f"JD 要 {need} 年，差 {diff} 年，基本没戏"


def _salary_fit(job: dict, cand: dict) -> tuple[float, str]:
    jd = parse_salary(job.get("salary", ""))
    lo = cand.get("salary_min_k") or 0
    hi = cand.get("salary_max_k") or 0
    if not jd:
        return 0.6, f"薪资写的是「{job.get('salary') or '面议'}」，没法比"
    if not lo and not hi:
        return 0.7, f"JD 给 {jd[0]:g}-{jd[1]:g}K，你没设期望薪资"
    want_lo = float(lo or hi)
    want_hi = float(hi or lo)
    if jd[1] < want_lo:
        return 0.2, f"JD 上限 {jd[1]:g}K 低于你期望的 {want_lo:g}K"
    if jd[0] >= want_lo:
        return 1.0, f"JD 给 {jd[0]:g}-{jd[1]:g}K，够你要的 {want_lo:g}-{want_hi:g}K"
    overlap = min(jd[1], want_hi) - max(jd[0], want_lo)
    value = 0.6 + 0.4 * max(0.0, overlap) / max(want_hi - want_lo, 1.0)
    return min(value, 0.95), f"JD 给 {jd[0]:g}-{jd[1]:g}K，和期望 {want_lo:g}-{want_hi:g}K 部分重叠"


def _city_fit(job: dict, cand: dict) -> tuple[float, str]:
    city = parse_city(job.get("area", ""))
    home = (cand.get("current_city") or "").strip()
    targets = [c for c in (cand.get("cities") or []) if c]
    if not city:
        return 0.6, "JD 没写城市"
    if home and city in home:
        return 1.0, f"就在{city}"
    if any(city in t or t in city for t in targets):
        return 0.9, f"{city} 在你选的城市里"
    if not home and not targets:
        return 0.6, f"在{city}，你没设城市偏好"
    return 0.3, f"在{city}，不在你的目标城市里"


def _education_fit(job: dict, cand: dict) -> tuple[float, str]:
    text = f"{job.get('desc','')} {job.get('title','')}"
    need = None
    for kw, rank in sorted(_EDU_RANK.items(), key=lambda kv: kv[1]):
        if re.search(r"(?:本科|硕士|博士|大专|专科|研究生|学历)[^\n]{0,6}" + kw, text) or re.search(
            kw + r"(?:及以上|以上|学历)", text
        ):
            need = rank
            need_kw = kw
            break
    if need is None:
        return 0.7, "JD 没写学历门槛"
    mine_kw = cand.get("education") or ""
    mine = _EDU_RANK.get(mine_kw)
    if mine is None:
        return 0.6, f"JD 要{need_kw}，你还没填学历"
    if mine >= need:
        return 1.0, f"JD 要{need_kw}，你是{mine_kw}"
    if need - mine == 1:
        return 0.4, f"JD 要{need_kw}，你是{mine_kw}，差一档"
    return 0.1, f"JD 要{need_kw}，你是{mine_kw}，够不着"


def _company_fit(job: dict, cfg: dict) -> tuple[float, str, bool]:
    """返回 (分数, 理由, 是否硬拒)。"""
    company = job.get("company") or ""
    blob = f"{company} {job.get('title','')} {job.get('desc','')[:600]}"
    mc = cfg.get("matching", {})
    cand = cfg.get("candidate", {})

    for name in cand.get("blacklist_companies") or []:
        if name and name in company:
            return 0.0, f"公司「{company}」在你拉黑的名单里", True

    for kw in cand.get("blacklist_keywords") or []:
        if kw and kw in blob:
            return 0.0, f"命中拉黑词「{kw}」", bool(mc.get("hard_reject_on_blacklist", True))

    for kw in cand.get("avoid_keywords") or []:
        if kw and kw in (job.get("title") or ""):
            return 0.0, f"岗位名里有「{kw}」，你不做这类", True

    if any(w in company for w in _OUTSOURCE_WORDS):
        return 0.1, f"「{company}」看着是外包/派遣类", False

    if any(b in company for b in _BIG_COMPANIES):
        return 1.0, f"{company} 是知名公司", False

    return 0.7, f"{company or '公司名缺失'}，没特别信号", False


# ---------------------------------------------------------------- 总分


def score_job(job: dict, resume_skills: set[str], cfg: dict) -> dict:
    weights = cfg.get("matching", {}).get("weights") or {}
    w = {
        "skill_coverage": float(weights.get("skill_coverage", 40)),
        "experience_fit": float(weights.get("experience_fit", 20)),
        "salary_fit": float(weights.get("salary_fit", 15)),
        "city_fit": float(weights.get("city_fit", 10)),
        "education_fit": float(weights.get("education_fit", 5)),
        "company_quality": float(weights.get("company_quality", 10)),
    }
    total_w = sum(w.values()) or 1.0

    cand = cfg.get("candidate", {})
    skill_v, skill_r = _skill_fit(job, resume_skills, cfg)
    exp_v, exp_r = _experience_fit(job, cand)
    sal_v, sal_r = _salary_fit(job, cand)
    city_v, city_r = _city_fit(job, cand)
    edu_v, edu_r = _education_fit(job, cand)
    co_v, co_r, hard = _company_fit(job, cfg)

    raw = (
        skill_v * w["skill_coverage"]
        + exp_v * w["experience_fit"]
        + sal_v * w["salary_fit"]
        + city_v * w["city_fit"]
        + edu_v * w["education_fit"]
        + co_v * w["company_quality"]
    )
    score = round(100.0 * raw / total_w, 1)
    if hard:
        score = 0.0

    threshold = float(cfg.get("matching", {}).get("min_score_to_apply", 70))
    if hard:
        verdict = "reject"
    elif score >= threshold:
        verdict = "apply"
    elif score >= threshold - 12:
        verdict = "borderline"
    else:
        verdict = "skip"

    # 现实门槛：钱差太多、或者城市压根不对 —— 技能再满配，人也不会去。
    # 分数只用来排序，能不能自动投还得过这一关。
    blocker = ""
    if sal_v < 0.35 and job.get("salary"):
        blocker = sal_r
    elif city_v < 0.35:
        blocker = city_r
    if blocker and verdict in ("apply", "borderline"):
        verdict = "borderline" if verdict == "apply" else "skip"

    return {
        "score": score,
        "verdict": verdict,
        "threshold": threshold,
        "hard_reject": hard,
        "dimensions": {
            "skill_coverage": [round(skill_v, 2), skill_r],
            "experience_fit": [round(exp_v, 2), exp_r],
            "salary_fit": [round(sal_v, 2), sal_r],
            "city_fit": [round(city_v, 2), city_r],
            "education_fit": [round(edu_v, 2), edu_r],
            "company_quality": [round(co_v, 2), co_r],
        },
        "jd_skills": sorted(
            set(extract_skills(
                f"{job.get('title','')} {job.get('desc','')}",
                load_skill_dict(),
            ))
        ),
        "matched_skills": sorted(
            set(extract_skills(
                f"{job.get('title','')} {job.get('desc','')}",
                load_skill_dict(),
            )) & resume_skills
        ),
        "blocker": blocker,
        "reasons": [co_r, skill_r, exp_r, sal_r, city_r, edu_r],
    }


def rank_jobs(jobs: list[dict], resume_skills: set[str], cfg: dict) -> list[dict]:
    scored = []
    for job in jobs:
        result = score_job(job, resume_skills, cfg)
        merged = dict(job)
        merged["match"] = result
        scored.append(merged)
    scored.sort(key=lambda j: (-j["match"]["score"], j.get("company") or ""))
    return scored