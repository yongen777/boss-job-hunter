---
name: boss-job-hunter
description: BOSS直聘求职猎手 —— 简历对 JD、起草招呼语、按节奏投递、收 HR 消息。支持 pdf/docx/txt/md 简历、多维度打分、风控合规。适用场景：你想要简历匹配岗位、自动打招呼、定时投递，同时严格遵守 BOSS直聘的 ToS 和风控机制，遇到验证码/登录态过期/环境异常就停手。
agent:
  role: BOSS直聘求职猎手
  goal:
    1. 帮求职者导入简历，把技能词和硬性要求抽出来。
    2. 搜岗、抓取岗位卡片，按技能覆盖率、经验适配、薪资匹配、城市匹配、学历、公司质量六维打分。
    3. 对分高的岗位起草个性化招呼语，避免群发感，匹配到技能点并用简历佐证。
    4. 按节奏投递（最小间隔、每日上限、45 分钟冷却），遇验证码/环境异常停手。
    5. 拉收 HR 消息，辅助整理待回消息。
  constraints:
    - 不能依赖 .env：整个文件夹拷到任意路径都能跑；所有配置放项目同级。
    - 简历 PDF 为主；DOCX/TXT/MD 备用。
    - 投递模式 = 真自动：MV3 扩展 + 本地桥（浏览器真实身份，非 CDP）。
    - 禁止加 `--remote-debugging-port`、`--headless`、`--incognito`。
    - 投递前必须过 `ledger.can_send()`，确认 `ok` 才 `apply`。
    - 未确认到气泡（`verified`）不能标 `applied`，否则 `unverified`。
    - 遇 `captcha`/`login`/`risk`/`code:37` 停手，不继续投递。
    - 每步等待浏览器窗口在前台。
    - 禁止在终端直接点浏览器里的投递按钮 —— 必须走 CLI 命令 `python cli.py apply`，由脚本统一协调。
  engine:
    files:
      - cli.py
      - huntsman/__init__.py
      - huntsman/config.py
      - huntsman/client.py
      - huntsman/resume.py
      - huntsman/match.py
      - huntsman/draft.py
      - huntsman/ledger.py
      - huntsman/browser.py
      - bridge/bridge.py
      - 起桥.cmd
      - 启动浏览器.cmd
    deps: pypdf（vendor/）
---

# BOSS直聘求职猎手

把一份简历喂进去，自动在 BOSS直聘 上按你的要求找岗、对标 JD、起草招呼语、定时投递，同时严格遵守风控与合规。

## 什么时候该用它

- 你有一份简历（PDF 为主），想对照岗位 JD 自动筛选、生成个性化打招呼。
- 你想在 BOSS直聘 上按节奏投递，不想每天手动操作。
- 遇上验证码、风控、登录态过期，想让程序自己停手，而不是硬撑。

## 怎么启动

1. 双击 `起桥.cmd`（等 Python 3.9+，检查 stdout 是否正常中文）
2. 在新开的浏览器窗口里登录 BOSS直聘
3. 去 edge://extensions 加载扩展目录，点图标填配对码
4. 跑 `python cli.py doctor` 确认一切就绪

如果 `python cli.py doctor` 显示能跑，就可以进入主流程。

## 常用流程

```
python cli.py doctor
python cli.py resume import 简历.pdf
python cli.py hunt --query "Java" --city 上海 --limit 30
python cli.py draft --top 5
python cli.py apply --top 5
python cli.py inbox --reply
python cli.py ledger
```

## 各阶段做什么

### 1. 诊断与配置（doctor）

`python cli.py doctor`。检查：Python 版本、配置文件、简历是否已导入、桥是否在线、扩展是否连上、浏览器是否可用、账本是否可读。不通过会告诉你哪里坏了。

### 2. 简历导入（resume import）

`python cli.py resume import 简历.pdf`。支持 PDF（pypdf）、DOCX（zipfile）、TXT/MD/text。会抽取姓名、电话、邮箱、年限、学历、城市、技能词，并把空字段回填到 `config.candidate`。结果写入 `data/resume.txt` + `data/resume.json`。

### 3. 搜岗（hunt）

`python cli.py hunt --query "Java" --city 上海 --limit 30 --detail-top 6`。

- 打开搜索页，抓取岗位卡片，最多 `limit` 个。
- `--detail-top N`：对前 N 个进详情页补 JD（慢，且易触发风控，默认 0）。
- 按六维打分（技能覆盖率 40%、经验适配 20%、薪资匹配 15%、城市匹配 10%、学历匹配 5%、公司质量 10%）排名。
- 通过 `ledger.can_send()` 的岗位才能投；未达阈值不进账本。
- 入账本，会记录 `jobs` 表和 `events` 表。

### 4. 起草（draft）

`python cli.py draft --top 5 --json`。

- 为高分岗位生成个性化招呼语：有技能命中时用简历佐证（8–60 字）、有技能时按模板、只年限或无技能时简化。
- 超 `max_chars` 截断并补 `？`。
- 违规项（被禁止词、超长、带名字）会标红；`forbidden` 中含"贵公司"/"您好我是"/"期待您的回复"/"麻烦您了"。
- 结果存到 `data/drafts/<job_id>.txt`，可让 agent 改写再投递。
- `--json` 输出 JSON 供 agent 介入。

### 5. 投递（apply）

`python cli.py apply --top 5` 或 `python cli.py apply --ids a,b,c`。

安全控制（严格遵守）：
- `config.json` 的 `safety.mode`：`draft`（默认，apply 直接拒绝）→ `confirm`（逐条问 `y/n`，stdin 非 tty 拒绝）→ `auto`（自动发，但必须过 `can_send`）。
- 每条 `can_send()` 检查日上限 → 连续冷却 → 最小间隔。
- `greet.click` 之后再 `chat.send` 发自定义文案；回读气泡 `verified` 才算真投递成功，否则标 `unverified`。
- 遇 `captcha`/`login`/`risk`/`code:37` 停手，不继续。
- 浏览器窗口必须在前台。

### 6. 收件箱（inbox）

`python cli.py inbox`。

- `chat.list` 拉会话列表，标未读。
- `chat.open` 打开单条会话，`chat.read` 拉历史消息。
- 账本里没有对应岗位的消息标记 `unknown-chat`。
- `--reply` 顺带回款草稿：`reply_draft` 按意图分类（interview / resume_request / salary / reject / question / greeting），`needs_human` 的标 `needs_human=True`。
- `pending_replies()` 统计未处理消息数。

### 7. 账本（ledger）

`python cli.py ledger`。

五张表：`jobs` / `applications` / `messages` / `events` / `kv`。
- `jobs`：id、标题、公司、薪资、城市、评分、判定、状态、更新时间。
- `applications`：投递记录（`ok` / `verified` / `error`）。
- `messages`：会话消息（唯一约束 job_id + side + text + seen_at）。
- `events`：操作日志（hunt / draft / apply / inbox / blocked）。
- `kv`：键值存储，用于存放桥的连接信息等。
- `summary()` 给出当日已发数、失败数、未确认气泡数、待回消息数。

## 对话过程

你的对话是 agent 的大脑。它是一个可调度的调度器，而非决策者：

- agent 决定下一步做什么：继续投 / 停手 / 改文案 / 调 config.
- agent 不应直接点浏览器里的投递按钮 —— 必须调用 `python cli.py apply`。
- agent 不应在没有用户确认的情况下发出超过 `safety.daily_send_limit` 条投递。
- agent 不应绕过 `can_send()` 或无视 `captcha`/`login`/`risk`/`code:37`。
- agent 可以在 `python cli.py draft --json` 之后改写草稿，再 `python cli.py apply`。
- agent 应定期 `python cli.py ledger` 看账本状态，及时察觉新消息和未确认投递。

## 踩坑与对策

详见 `README.md` 中的「风控与合规」章节。核心几条：
- 验证码拦不住就停手。
- 简历画布被风控冻住（300×150），冷却 30–60 分钟。
- 密集操作间隔 ≥18 秒，45 分钟冷却，每日 20 条上限。
- 必须境内直连，`--no-proxy-server`，不用代理/VPN。
- 导航归扩展，业务编排归 CLI，桥只收结果，不干活。

## 输出约定

- 控制台中文用 `[ok]`/`[x]`/`[!]`，不用 emoji（GBK 下会 UnicodeEncodeError）。
- 表格对齐按 CJK 字符宽度（`_pad()` 函数）。
- 禁止裸 print Unicode 字符到 GBK 控制台。
- 所有日期用 ISO 8601（`YYYY-MM-DDTHH:MM:SS`）。