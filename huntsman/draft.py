"""招呼语与 HR 回复的草稿。

这里不接大模型 —— 只用「从简历里抠出真实证据 + 拼一句人话」的模板法，
保证断网也能跑。draft 输出的 facts 是给 agent 用的原料：
agent 拿着它写更自然的一句，写进 data/drafts/<job_id>.txt，apply 直接用。
模板只是兜底，别把它当成品。
"""

from __future__ import annotations

import re

from .resume import extract_skills, load_skill_dict

_SENT_SPLIT = re.compile(r"[。；;\n]")


def pick_evidence(resume_text: str, skills: list[str], limit: int = 2) -> list[str]:
    """在简历里找能佐证这些技能的句子，越具体越好。"""
    if not skills:
        return []
    sentences = [s.strip() for s in _SENT_SPLIT.split(resume_text) if s.strip()]
    picked: list[str] = []
    used: set[str] = set()
    for skill in skills:
        # 同一个技能词，优先挑带数字的那句（做过多少、提升多少，最像真话）
        candidates = [s for s in sentences if skill.lower() in s.lower()]
        candidates.sort(key=lambda s: (re.search(r"\d", s) is None, len(s)))
        for sent in candidates:
            if sent in used or any(sent[:30] == p[:30] for p in picked):
                continue
            # 太短没信息量
            if len(sent) < 8:
                continue
            # 太长塞不进招呼语，且保留完整词边界别乱切
            if len(sent) > 60:
                seg = sent[:60]
                sp = seg.rfind(" ")
                if sp == -1:
                    sent = seg[:58] + "…"
                else:
                    sent = sent[:sp + 1] + "…"
            picked.append(sent)
            used.add(sent)
            break
        if len(picked) >= limit:
            break
    return picked


def _join(items: list[str], sep: str = "、") -> str:
    return sep.join([i for i in items if i])


def build_greeting(job: dict, resume_text: str, resume_skills: list[str], cfg: dict) -> dict:
    """拼一句招呼语，并返回它依据的事实（给 agent 参考/改写用）。"""
    g = cfg.get("greeting", {})
    max_chars = int(g.get("max_chars", 150))
    cand = cfg.get("candidate", {})

    title = (job.get("title") or "").strip()
    company = (job.get("company") or "").strip()
    matched = job.get("match", {}).get("matched_skills") or []
    if not matched:
        jd_text = f"{title} {job.get('desc','')}"
        matched = [s for s in extract_skills(jd_text, load_skill_dict()) if s in set(resume_skills)][:4]

    evidence = pick_evidence(resume_text, matched, limit=2)
    years = cand.get("years_experience")
    city = (job.get("area") or "").split("·")[0]

    facts = {
        "job_title": title,
        "company": company,
        "city": city,
        "salary": job.get("salary", ""),
        "matched_skills": matched,
        "evidence": evidence,
        "years_experience": years,
        "score": job.get("match", {}).get("score"),
        "score_reasons": job.get("match", {}).get("reasons", []),
        # 下面两条是给 agent 改写用的口径，模板本身不照它们变 —— 别以为改了 style 文案就会跟着变
        "style": g.get("style", ""),
        "must_mention": g.get("must_mention", []),
    }

    if matched and evidence:
        body = f"{title}这个岗，{_join(matched[:2])} 我在项目里实际做过 —— {evidence[0]}。"
    elif matched:
        years_bit = f"{years} 年" if years else "一段时间"
        body = f"{title}这个岗，{_join(matched[:2])} 这块我有 {years_bit}的实操经验。"
    elif years:
        body = f"{title}这个岗我看了下，同类工作做过{years}年，想聊聊看合不合适。"
    else:
        body = f"{title}这个岗我看了下，觉得方向对得上，想聊聊看合不合适。"

    tail = "方便的话聊两句？"
    text = body + tail
    if len(text) > max_chars:
        text = body[: max_chars - len(tail) - 1] + "…" + tail

    return {
        "text": text,
        "facts": facts,
        "chars": len(text),
        "max_chars": max_chars,
        "violations": check_greeting(text, cfg),
    }


def check_greeting(text: str, cfg: dict) -> list[str]:
    """按 config.greeting 的规矩自检。返回违规项（中文说明）。"""
    g = cfg.get("greeting", {})
    problems: list[str] = []

    for word in g.get("forbidden") or []:
        if word and word in text:
            problems.append(f"出现了「{word}」，config 里列明不要用")

    max_chars = int(g.get("max_chars", 150))
    if len(text) > max_chars:
        problems.append(f"太长：{len(text)} 字，上限 {max_chars}")

    if not g.get("use_candidate_name", False):
        name = (cfg.get("candidate", {}) or {}).get("name")
        if name and name in text:
            problems.append(f"带上了自己的名字「{name}」，config 里 use_candidate_name=false")

    if len(text) < 20:
        problems.append("太短，像群发")

    return problems


# ---------------------------------------------------------------- HR 回复

_INTENTS = [
    ("interview", ("面试", "面谈", "约个时间", "什么时候方便", "来公司", "视频面", "电话面", "聊一下")),
    ("resume_request", ("简历", "发我", "发一份", "附件", "作品")),
    ("salary", ("薪资", "薪酬", "期望", "多少钱", "待遇", "package", "涨薪")),
    ("reject", ("不合适", "不匹配", "暂不考虑", "已经有合适", "已经招到", "招满",
                "暂停招聘", "不再招", "已经找到", "名额满", "遗憾")),
    ("question", ("?", "？", "是否", "会不会", "有没有", "能否", "几年")),
    ("greeting", ("你好", "在吗", "hi", "hello", "看到你的")),
]

_REPLY_HINTS = {
    "interview": "对方在约面试 —— 这必须人来定时间，别自动回。",
    "resume_request": "对方要简历 —— 先确认平台上简历附件是不是最新的，再回一句「已发」。",
    "salary": "对方问薪资 —— 按 config 里的 salary_min_k / salary_max_k 给区间，别报单点。",
    "reject": "对方婉拒了 —— 回一句客气话收尾，别追问。",
    "question": "对方在问具体问题 —— 需要基于简历事实答，答不上来就说不确定。",
    "greeting": "对方在打招呼 —— 简单回应，把话题引到岗位匹配上。",
}


def classify_intent(text: str) -> str:
    low = (text or "").lower()
    for intent, kws in _INTENTS:
        if any(k.lower() in low for k in kws):
            return intent
    return "other"


def reply_draft(incoming: str, job: dict, cfg: dict, history: list[dict] | None = None) -> dict:
    """给一条 HR 消息配一个回复草稿 + 风险提示。"""
    intent = classify_intent(incoming)
    cand = cfg.get("candidate", {})
    lo = cand.get("salary_min_k") or 0
    hi = cand.get("salary_max_k") or 0
    salary_bit = f"{lo}-{hi}K" if lo and hi else (f"{lo}K 起" if lo else "")

    if intent == "interview":
        text = "可以的，我这边时间上比较灵活，您看哪天方便？"
        needs_human = True
    elif intent == "resume_request":
        text = "简历我这边同步一下，附件是最新的。"
        needs_human = True
    elif intent == "salary":
        if salary_bit:
            text = f"期望在 {salary_bit} 这个区间，具体可以按岗位职责再谈。"
            needs_human = False
        else:
            # 没配期望薪资就别赌，让人来定价
            text = "薪资这块我想先了解下岗位的预算范围。"
            needs_human = True
    elif intent == "reject":
        text = "好的，谢谢您抽时间看，后续有合适的岗位再联系。"
        needs_human = False
    elif intent == "question":
        text = ""
        needs_human = True
    elif intent == "greeting":
        text = "您好，看到岗位介绍了。想先了解下这个岗位目前最看重哪块能力？"
        needs_human = False
    else:
        text = ""
        needs_human = True

    return {
        "intent": intent,
        "incoming": incoming,
        "draft": text,
        "needs_human": needs_human,
        "hint": _REPLY_HINTS.get(intent, "这类消息没有现成模板，得人来判断。"),
        "job": {"title": job.get("title"), "company": job.get("company")},
        "context": (history or [])[-4:],
    }