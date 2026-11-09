# BOSS直聘求职猎手（boss-job-hunter）

用简历去匹配 BOSS直聘 上的岗位 JD，生成针对每个岗位的招呼语，投递后自动盯 HR 回复并起草回话。

**设计前提：整个目录复制到任何路径都能直接跑。** 不读 `.env`，不读环境变量，不依赖全局配置。所有配置在项目内的 `config.json`，所有数据在项目内的 `data/`。

## 为什么不用 CDP

BOSS直聘 前端内置加固版 `disable-devtool`：用带调试端口的 Chromium 打开，约 5 秒后页面会自己 `window.close()` 跳 `about:blank`，并往风控埋点上报 `fe-tools-no-debug`；服务端还会返回 `{"code":37,"message":"您的环境存在异常."}`。

所以本项目**不用 CDP、不用 Playwright、不用无头浏览器**，改走：

```
MV3 扩展（在真实可见浏览器里点 DOM）  ⇄  本地 HTTP 桥（127.0.0.1:8898）  ⇄  Python CLI / Agent
```

浏览器以普通方式启动，加 `--load-extension` 和 `--no-proxy-server`，**绝不加** `--remote-debugging-port`。

> **关于 `--load-extension`**：Chromium 137 起这个开关被官方停用，启动时不再装载扩展（实测 Edge 156：命令行参数带上了，profile 里 `extensions.settings` 仍是 0 条）。项目仍然会把参数带上——旧版本浏览器能靠它自动装载；新版本就**必须手动装一次**（见下面第 2 步），装完 profile 会一直记着，之后不用再装。`doctor` 会如实报「扩展没连上」，不会假装连上了。

## 组成

| 目录 | 作用 |
|---|---|
| `bridge/bridge.py` | 本地桥。零第三方依赖（Python 标准库），长轮询把命令下发给扩展 |
| `extension/` | MV3 扩展。在 `zhipin.com` 页面里读列表、读 JD、发招呼语、读聊天 |
| `huntsman/` | 核心逻辑：PDF 简历解析、JD 匹配打分、招呼语/回复起草、投递台账 |
| `cli.py` | 命令行入口 |
| `skills/boss-job-hunter/SKILL.md` | 跨平台 skill 入口（Claw / Cursor / Claude Code / Kimi / OpenClaw / DSH） |
| `data/` | 专用浏览器 profile、简历、台账（不进 git） |

## 启动

**1. 起桥**

```bat
起桥.cmd
```

或 `python bridge\bridge.py`。放在后台挂着就行。

**2. 装扩展并配对**

一次性：打开 `edge://extensions` → 开发者模式 → 加载已解压的扩展程序 → 选 `extension` 目录。

然后在扩展弹窗里填桥地址和 token（token 在 `data/.bridge-token`，桥首次启动会自动生成）。

**3. 起专用浏览器**

```bat
启动浏览器.cmd
```

用独立 profile（`data/browser-profile`）启动，登录态保留在这里，不碰你日常浏览器的数据。第一次要手动登录一次 BOSS直聘。

**4. 自检**

```bat
python cli.py doctor
```

依次检查：Python 版本、桥是否在跑、扩展是否连上、浏览器是否开着、简历是否已导入。

## 用法

```bat
python cli.py resume import data\resume\我的简历.pdf     # 解析 PDF 建立证据库
python cli.py hunt --query "AI算法" --city 杭州 --limit 30  # 采集 + 匹配打分
python cli.py draft --top 10                                # 生成招呼语草稿
python cli.py apply --ids 1,3,7                             # 投递（默认停在发送前）
python cli.py inbox                                         # 拉 HR 回复，未读的起草应答
python cli.py ledger                                        # 看投递台账
```

## 测试

```bat
python tests\run.py
```

跑 `tests/` 下的 `test_*.py`（每个用子进程隔离，挂了不影响别的），退出码 0 = 全绿。
现在覆盖两块：账本全流程（落库/排序/限额/冷却/消息去重/汇总）和 draft（招呼语限长与违规词、意图分类、回复草稿）。

## 版式自检

站方改版时最怕的不是报错，是**静默降级**：控件没找到，采集回来一堆空字段，评分照样算出一个分数，你还是照着投了。

所以每个会动页面/读页面的动作在动手前先过一道闸（`extension/content.js` 里的 `guardLayout`）：

| 动作 | 卡在哪个控件 |
|---|---|
| `jobs.collect` | 岗位卡片 |
| `job.read` | 详情页标题 |
| `greet.click` | 「立即沟通」按钮 |
| `chat.list` / `chat.open` | 聊天列表 |
| `chat.read` | 消息区 |
| `chat.send` | 输入框 + 发送按钮 |

两种失败，含义完全不同，别混：

- `not-ready` —— 页面还在加载（`readyState` 没到 `complete`，等不到锚点）。**等两秒重跑就行**，不用改代码。
- `layout-changed` —— 页面上压根没这个控件，站点改版了。报错会点名缺哪个分组，去 `extension/selectors.js` 按新 class 校准。

`probe` / `state.get` / `wait.for` **故意不过闸**：版式坏掉的时候你得还能用探针去看现场，否则就只能靠猜。

职位描述（`detailDesc`）是单独一档：单个岗位读不到描述算提醒（这条评分偏低），连着一条都读不到就直接终止——条条都空只能是选择器失效，不能当成功往下走。

## 安全边界

- 默认 `safety.mode = "confirm"`：出草稿、列出要投的岗位、**停在发送前**等人批准。要真发得显式 `--yes`。
- 默认限速：每人间隔 ≥18 秒，每 45 分钟冷却一次，单日上限 20。这是平台短时限流的安全区，不是保守设置。
- 遇到验证码 / `code:37` / 简历画布冻结：**立即停**，不重试。重试会延长冷却。
- 只操作你自己有权使用的账号。自动化投递可能违反平台服务协议，风险自负。

## 免责

非官方项目，与 BOSS直聘 及其关联公司无任何关系。所有页面行为描述来自公开可观察的页面现象，仅用于让自己的账号正常使用。