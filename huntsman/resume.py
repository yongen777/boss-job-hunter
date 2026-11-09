"""简历导入与解析。

支持 PDF / DOCX / TXT / MD。PDF 用 vendor/ 里的 pypdf，其余走标准库。
解析结果落到 data/resume/：原文件 + resume.txt（纯文本）+ resume.json（字段）。
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path

from . import config as cfgmod

# ---------------------------------------------------------------- 技能词典

# 不追求全，只求覆盖常见岗位词。用户可以往 data/skills.txt 里加自己的。
_SKILL_WORDS = """
Python Java Golang Go C++ C# PHP Ruby Rust Scala Kotlin Node.js JavaScript TypeScript
Shell Bash Perl Lua MATLAB Swift Objective-C Dart SQL HTML CSS
Spring SpringBoot Spring Cloud Django Flask FastAPI Tornado Express Koa NestJS
React Vue Angular Svelte Next.js Nuxt jQuery Bootstrap Ant Design Element UI Vant
uni-app Flutter React Native Electron 微信小程序 小程序 Webpack Vite Rollup Babel
MySQL PostgreSQL Oracle SQLServer MongoDB Redis Elasticsearch ClickHouse HBase
Cassandra SQLite TiDB Neo4j InfluxDB Kafka RabbitMQ RocketMQ ActiveMQ ZooKeeper
Nginx Tomcat Netty gRPC Dubbo WebSocket Docker Kubernetes K8s Jenkins GitLab
GitHub Actions Linux Ubuntu CentOS AWS 阿里云 腾讯云 华为云 Terraform Ansible
Prometheus Grafana ELK CI/CD DevOps Hadoop Spark Flink Hive MapReduce Storm
Presto Airflow 数据仓库 数仓 ETL OLAP 机器学习 深度学习 神经网络 TensorFlow PyTorch
Keras scikit-learn XGBoost LightGBM NLP 自然语言处理 计算机视觉 推荐系统 推荐算法
召回 排序 大模型 LLM RAG Transformer AIGC 数据挖掘 特征工程 强化学习
Android iOS 安卓 鸿蒙 HarmonyOS 自动化测试 单元测试 Selenium Appium JMeter
Postman pytest JUnit 性能测试 接口测试 微服务 分布式 高并发 高可用 缓存
消息队列 设计模式 领域驱动 DDD 架构设计 重构 代码评审 敏捷 Scrum 项目管理
需求分析 产品设计 原型 Axure Figma Sketch 数据分析 Excel 汇报 渗透测试
安全加固 等保 WAF OAuth JWT 加密 鉴权 前端工程化 性能优化 响应式
Three.js ECharts D3 数据可视化 爬虫 反爬 运维 监控 日志 容器化 灰度
""".split()

_CN_CITIES = """
北京 上海 广州 深圳 杭州 成都 武汉 南京 西安 苏州 天津 重庆 长沙 郑州 青岛
厦门 合肥 福州 济南 大连 宁波 无锡 东莞 佛山 珠海 中山 沈阳 哈尔滨 长春 昆明
南昌 贵阳 南宁 海口 兰州 乌鲁木齐 石家庄 太原 常州 温州 嘉兴 惠州
""".split()

_EDU_RANK = {"高中": 1, "中专": 1, "大专": 2, "专科": 2, "本科": 3, "硕士": 4, "研究生": 4, "博士": 5}

# ---------------------------------------------------------------- 文本抽取


def _strip_tags(xml: str) -> str:
    xml = re.sub(r"<w:p[ >]", "\n<w:p ", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    xml = re.sub(r"<w:br[^>]*/>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')


def _read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        try:
            raw = zf.read("word/document.xml").decode("utf-8", "replace")
        except KeyError:
            raise ValueError("这个 .docx 里没有 word/document.xml，可能不是 Word 文档")
    return _strip_tags(raw)


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError(
            "读 PDF 需要 pypdf，但项目里的 vendor/ 没有它。\n"
            "在项目根目录执行一次：python -m pip install --target vendor pypdf"
        )
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise ValueError("这个 PDF 加密了，先解密再导入")
    chunks = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            chunks.append("")
    return "\n".join(chunks)


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        raw = _read_pdf(path)
    elif suffix == ".docx":
        raw = _read_docx(path)
    elif suffix in (".txt", ".md", ".text"):
        raw = path.read_text(encoding="utf-8", errors="replace")
    elif suffix == ".doc":
        raise ValueError("老版 .doc 读不了，请在 Word 里另存为 .docx 或 .pdf 再导入")
    else:
        raise ValueError(f"不认识的格式：{suffix}（支持 pdf / docx / txt / md）")
    return _tidy(raw)


# ---------------------------------------------------------------- 技能抽取


def load_skill_dict() -> list[str]:
    """内置词典 + 用户自己加的词（data/skills.txt，一行一个）。"""
    words = list(_SKILL_WORDS)
    extra = cfgmod.DATA_DIR / "skills.txt"
    if extra.exists():
        for line in extra.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
    seen, out = set(), []
    for w in words:
        k = w.lower()
        if k not in seen:
            seen.add(k)
            out.append(w)
    return out


def extract_skills(text: str, dictionary: list[str] | None = None) -> list[str]:
    """从一段文字里挑出技能词。英文按词边界匹配，避免 Go 命中 Google。"""
    words = dictionary if dictionary is not None else load_skill_dict()
    low = text.lower()
    found = []
    for w in words:
        wl = w.lower()
        if re.search(r"[a-z]", wl):
            pattern = r"(?<![a-z0-9+#._])" + re.escape(wl) + r"(?![a-z0-9+#_])"
            if re.search(pattern, low):
                found.append(w)
        else:
            if wl in low:
                found.append(w)
    return found


# ---------------------------------------------------------------- 字段解析


def parse_fields(text: str) -> dict:
    fields: dict = {}

    m = re.search(r"1[3-9]\d{9}", text)
    if m:
        fields["phone"] = m.group(0)
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    if m:
        fields["email"] = m.group(0)

    m = re.search(r"(\d+)\s*年(?:以上)?(?:的)?(?:工作)?(?:经验|开发经验)", text)
    if not m:
        m = re.search(r"工作年限[:：\s]*(\d+)", text)
    if m:
        try:
            fields["years_experience"] = int(m.group(1))
        except ValueError:
            pass

    for kw, rank in sorted(_EDU_RANK.items(), key=lambda kv: -kv[1]):
        if kw in text:
            fields["education"] = kw
            fields["education_rank"] = rank
            break

    cities = []
    for c in _CN_CITIES:
        if c in text and c not in cities:
            cities.append(c)
    if cities:
        fields["cities"] = cities[:5]

    head = "\n".join([ln for ln in text.splitlines() if ln.strip()][:6])
    m = re.search(r"(?:姓\s*名|名字)[:：\s]*([\u4e00-\u9fa5]{2,4})", head)
    if m:
        fields["name"] = m.group(1)
    else:
        for ln in head.splitlines():
            ln = ln.strip()
            if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", ln) and not re.search(r"简历|求职|个人", ln):
                fields["name"] = ln
                break

    return fields


# ---------------------------------------------------------------- 导入流程


def import_resume(src: Path) -> dict:
    src = Path(src).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"找不到这个文件：{src}")
    if not src.is_file():
        raise ValueError(f"这不是一个文件：{src}")

    cfgmod.ensure_dirs()
    text = extract_text(src)
    if len(text.strip()) < 30:
        raise ValueError(
            "这份简历抽出来几乎是空的。\n"
            "常见原因：扫描件（图片 PDF）、或者字体做了子集化。\n"
            "换一份能选中文字的 PDF，或者导一份 .docx 再试。"
        )

    skills = extract_skills(text)
    fields = parse_fields(text)
    fields["skills"] = skills
    fields["chars"] = len(text)
    fields["source"] = src.name

    dest = cfgmod.RESUME_DIR / src.name
    if src != dest:
        shutil.copy2(src, dest)

    (cfgmod.RESUME_DIR / "resume.txt").write_text(text, encoding="utf-8")
    (cfgmod.RESUME_DIR / "resume.json").write_text(
        json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    cfg = cfgmod.load_config()
    cand = cfg.setdefault("candidate", {})
    filled = []
    for key in ("name", "phone", "email", "years_experience", "education"):
        if not cand.get(key) and fields.get(key):
            cand[key] = fields[key]
            filled.append(key)
    if not cand.get("current_city") and fields.get("cities"):
        cand["current_city"] = fields["cities"][0]
        filled.append("current_city")
    if filled:
        cfgmod.save_config(cfg)

    return {
        "source": str(src),
        "copied_to": str(dest),
        "chars": len(text),
        "skills": skills,
        "fields": {k: v for k, v in fields.items() if k not in ("skills",)},
        "config_filled": filled,
        "text_head": text[:400],
    }


def load_resume_text() -> str:
    path = cfgmod.RESUME_DIR / "resume.txt"
    if not path.exists():
        raise FileNotFoundError(
            "还没导入简历。先跑一次：python cli.py resume import <简历文件路径>"
        )
    return path.read_text(encoding="utf-8")


def load_resume_skills() -> list[str]:
    path = cfgmod.RESUME_DIR / "resume.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("skills"), list) and data["skills"]:
                return list(data["skills"])
        except Exception:
            pass
    return extract_skills(load_resume_text())