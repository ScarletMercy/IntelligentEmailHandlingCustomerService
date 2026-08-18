# AI QQ 邮件自动客服系统

一个由 AI 驱动的 QQ 邮件自动客服系统。它会自动轮询 QQ 邮箱的新邮件，用大语言模型（LLM）按**意图 / 紧急度 / 终端平台**进行分类，路由到对应处理分支，起草专业回复，并通过 SMTP 自动回信。复杂或高优先级的邮件会升级给人工，并可选通过官方 QQ Bot 推送通知。

## 核心特性

- 异步轮询 QQ 邮箱新邮件（IMAP 增量拉取）
- 基于 LLM 的分类：意图（intent）、紧急度（urgency）、终端平台（terminal），并附带主题（topic）与摘要（summary）
- 提示词内嵌 JSON Schema 进行分类，对输出做**容错解析**（裸 JSON 或 ```json 代码块皆可），不依赖接口级结构化输出
- 自动起草专业、准确的回复
- 通过 SMTP（smtp.qq.com，STARTTLS）自动回信
- 复杂或高优先级邮件升级人工，并可选通过 QQ Bot 推送（c2c / group）
- 交互式配置向导：在线验证模型连接；QQ Bot 支持**扫码一键配置**（终端二维码，扫码后自动填入 App ID / Client Secret / 真实 openid）
- 崩溃安全的状态持久化（SQLite）：去重账本、UID 基线、UIDVALIDITY 守卫、重试队列、孤儿对账，重启或崩溃都不会丢失或重复处理
- 守护进程存活判定基于**独占文件锁**（不受 PID 复用影响），支持 `daemon` / `stop` / `status` 生命周期管理
- 优雅关闭（跨平台信号兼容）、有界并发、退避重试、死信兜底

## 技术架构

核心工作流是一个**自定义异步状态机 `EmailWorkflow`** —— 它**不依赖 LangGraph** 等编排库。技术栈：

- [openai](https://pypi.org/project/openai/)（`AsyncOpenAI`）—— 分类与起草；兼容 OpenAI / DeepSeek 等任意 OpenAI 兼容接口
- [aioimaplib](https://pypi.org/project/aioimaplib/) —— 异步 IMAP 邮件拉取
- [aiosmtplib](https://pypi.org/project/aiosmtplib/) —— 异步 SMTP 回信
- [httpx](https://pypi.org/project/httpx/) —— QQ Bot HTTP API 与扫码配置接口
- [beautifulsoup4](https://pypi.org/project/beautifulsoup4/) —— 邮件 HTML 正文清洗
- [cryptography](https://pypi.org/project/cryptography/) —— 扫码配置中客户端密钥的 AES-256-GCM 解密
- [qrcode](https://pypi.org/project/qrcode/) —— 终端渲染扫码配置用的二维码
- [tenacity](https://pypi.org/project/tenacity/) —— 分类失败重试
- [python-dotenv](https://pypi.org/project/python-dotenv/) —— `.env` 配置加载
- Python 3.10+（使用 PEP 604 `X | None` 联合类型语法）

> 注：推送通知使用官方 QQ Bot（c2c / group）。早期曾有的飞书（Lark）集成（推送 + Bitable 持久化）已被移除，分类与处理结果仅保留在内存与本地 SQLite 中。如需审计日志，见下方「扩展点」。

## 环境要求

- Python 3.10+
- 已开启 IMAP 的 QQ 邮箱，以及其**授权码**（非登录密码）
- 一个 OpenAI 兼容对话模型的 API Key（OpenAI、DeepSeek 等）
- （可选）一个 QQ 官方 Bot 用于推送通知

## 安装

### 1. 克隆项目

```bash
git clone <repository-url>
cd AIHandleQQEmail
```

### 2. 使用 uv 安装（推荐）

本项目使用 [uv](https://docs.astral.sh/uv/) 管理（先按需安装 uv，见 [uv 安装指南](https://docs.astral.sh/uv/getting-started/installation/)）：

```bash
uv sync                 # 创建 .venv 并按 uv.lock 精确安装（含 dev 组）
```

这会安装 `ai-email` 控制台入口。通过 `uv run` 运行：

```bash
uv run ai-email --help
```

> pip 兜底：`pip install .` 仍可安装运行时依赖（dev 工具链 pytest / pyinstaller / ruff / mypy 由 uv 依赖组管理）。构建系统依赖 `setuptools` 与 `wheel`。

## 配置

配置文件存放在 `~/.ai-email/.env`（位于项目目录之外，避免密钥入库）。推荐用交互式配置向导创建，它会在线验证模型连接与 QQ Bot 凭据：

```bash
ai-email setup
```

对于 QQ Bot 通知部分，向导采用**扫码配置**：在终端渲染二维码，用 QQ 扫码后 App ID、Client Secret 以及你的真实 `openid`（作为通知目标）会被自动填入。不提供手动填写 —— 没有接口能反查 openid，它只在扫码绑定或 Bot 消息事件中暴露。

你也可以把 `.env.example` 复制为 `~/.ai-email/.env` 并手动填写（此时 QQ Bot 通知留空即不启用）。

必填环境变量：

| 变量 | 必填 | 说明 |
|------|------|------|
| `MODEL` | 是 | 模型名称（如 `gpt-4o`、`deepseek-chat`） |
| `BASE_URL` | 是 | API 地址 |
| `API_KEY` | 是 | API Key |
| `QQEMAIL` | 是 | QQ 邮箱地址 |
| `EMAIL_PASSWORD` | 是 | QQ 邮箱**授权码**（非登录密码） |
| `QQ_APP_ID` | 否 | QQ Bot App ID（启用通知时填） |
| `QQ_CLIENT_SECRET` | 否 | QQ Bot Client Secret |
| `QQ_NOTIFY_TARGET` | 否 | 通知目标（`c2c:openid` 或 `group:groupid`；真实值由扫码配置自动填入） |
| `WORKER_CONCURRENCY` | 否 | 最大并发处理邮件数（默认 `4`）。`setup` 不提示，仅能在 `~/.ai-email/.env` 手动设置。 |
| `LLM_TIMEOUT_SECONDS` | 否 | 单次 LLM 请求超时（默认 `60`）。慢模型可调大；openai SDK 默认 600 秒会让挂起请求阻塞 worker 长达 30 分钟。`setup` 不提示。 |
| `RETRY_BACKOFF_SECONDS` | 否 | 失败邮件重试前的退避秒数（默认 `30`），避免 SMTP 故障时重试风暴。`setup` 不提示。 |
| `LOG_LEVEL` | 否 | 日志级别（默认 `INFO`；排查问题时设 `DEBUG`，无需改码）。`setup` 不提示。 |

说明：

- QQ 邮箱需要**授权码**而非登录密码，在 QQ 邮箱设置中生成。
- `MODEL`、`BASE_URL`、`API_KEY` 按你的模型服务商填写。
- 仅当三个 `QQ_*` 变量都非空时启用 QQ Bot 通知；占位值（如字面量 `c2c:openid`）会被识别为未配置。

## 使用

`uv run ai-email setup` 之后，使用各 CLI 子命令（每条可加 `uv run` 前缀，或先激活 `.venv`）：

```bash
uv run ai-email setup      # 交互式配置向导
uv run ai-email            # 前台运行（监听 + 处理）
uv run ai-email daemon     # 以守护进程后台运行
uv run ai-email status     # 查看守护进程状态
uv run ai-email stop       # 停止守护进程
```

> 无子命令时默认前台运行。守护进程内部通过 `python -m ai_email _worker` 派生子进程（该子命令为内部实现，普通用户无需直接调用）。

启动服务即开始监听，自动轮询新邮件并逐封走完工作流处理。

## 工作流程

1. **邮件监听**：异步 IMAP 增量拉取新邮件（仅取持久化 `last_uid` 之后的 UID），并带 UIDVALIDITY 守卫——若邮箱 UID 空间回卷则安全重置基线，避免误判或漏处理。
2. **邮件分类**：LLM 分析邮件，按意图、紧急度、终端平台（及主题/摘要）分类。
3. **路由**：按意图与紧急度分流：
   - `complex_request`，或 `high` / `critical` 紧急度 → 升级人工（可选 QQ Bot 推送）
   - `question` / `feature` → 知识库检索步骤
   - `bug` → 工单创建步骤
   - 其余（如 `building`）→ 直接进入起草
4. **回复起草**：LLM 根据邮件内容与处理结果起草回复。
5. **邮件发送**：通过 SMTP 自动回信。

## 邮件分类规则

系统按以下维度分类：

- **意图（`intent`）**：
  - `question`：一般咨询
  - `bug`：Bug 报告
  - `building`：部署相关问题
  - `feature`：功能建议
  - `complex_request`：需人工处理的复杂请求
- **紧急度（`urgency`）**：
  - `low`：低
  - `medium`：中
  - `high`：高
  - `critical`：紧急
- **终端（`terminal`）**：`Web`、`Windows`、`Android`、`Mac`、`iOS` 或 `Not provided`

> 枚举值以代码中的 `Literal` 为单一来源，运行时校验非法值会降级为安全默认值（`question` / `medium` / `Not provided`）。

## 可靠性与状态

守护进程把所有状态保存在 `~/.ai-email/seen.db`（标准库 `sqlite3`，无额外依赖），因此重启或崩溃都不会丢失或重复处理：

- **UID 增量拉取** —— 每轮只取大于持久化 `last_uid` 的 UID（`UID n+1:*`）；`last_uid` 在拉取后推进。
- **UIDVALIDITY 守卫** —— 持久化 IMAP UIDVALIDITY 值；若变化（UID 空间回卷），则重置去重表与 `last_uid` 基线，保证重新处理仍然安全。
- **原子去重** —— 每封邮件用单条 `INSERT OR IGNORE`（`status='processing'`）抢占，消除并发 worker 间的「检查再插入」竞态；只有抢到的 worker 才处理。
- **崩溃对账** —— 启动时 `reconcile_orphans` 把仍处于 `processing`（处理中被打断）的邮件回滚进重试队列；已 `done` 的保留。
- **退避重试** —— 发送失败或异常进入 `retry_queue`，退避窗口后重新进入流水线；超过 5 次按死信丢弃。
- **有界并发** —— 最多 `WORKER_CONCURRENCY`（默认 `4`）封邮件并行处理。
- **优雅关闭** —— 收到关闭信号后停止取新邮件、等待在途 worker 完成（超时强制取消），再断开 IMAP 与 QQ Bot 连接。POSIX 用 `loop.add_signal_handler`（覆盖 SIGINT+SIGTERM），Windows 回退 `signal.signal`（仅 SIGINT 可靠）。

`~/.ai-email/` 下的运行时文件：

| 文件 | 用途 |
|------|------|
| `.env` | 配置（由 `ai-email setup` 生成） |
| `seen.db` | 去重账本、`last_uid` / UIDVALIDITY、重试队列（含 `-wal`/`-shm` 附属文件） |
| `ai-email.pid` | 守护进程 PID（信息性；`stop` 据此下发终止信号） |
| `ai-email.lock` | 独占存活锁 —— 「守护进程是否在跑」的权威判据 |
| `ai-email.log` | 守护进程 stdout/stderr 日志（`status` 读取，超过 10MB 启动时滚动为 `.log.1`） |

**存活判定**：基于 `ai-email.lock` 的独占文件锁（Windows 用 `msvcrt.locking`，POSIX 用 `fcntl.flock`）。能抢到锁 = 无持有者 = 未运行；抢不到 = 运行中。这避免了依赖 PID 导致的 PID 复用误判。

## 持续集成

仓库包含两个 GitHub Actions 工作流（位于 `.github/workflows/`）：

- **`build-and-test.yml`**：在 push/PR 到 `main`、release 发布及手动触发时运行，跨 Python `3.10` / `3.13` 矩阵：
  1. `ruff check`（lint）+ `ruff format --check`（格式）+ `mypy`（类型检查）
  2. `pytest`（测试套件，无需密钥；`tests/conftest.py` 提供占位环境变量）
  3. `uv build`、产物上传，以及 wheel 安装 + 冒烟测试（`ai-email --help`）
- **`publish.yml`**：在推送 `v*` tag 或手动触发时，构建分发包并通过 OIDC 可信发布上传到 PyPI（无需配置 PyPI 密码）。

测试套件全程不触网，无需仓库密钥。

## 项目结构

```
.
├── ai_email/
│   ├── __init__.py                # 包入口：定义 main() 与 __version__（懒加载 cli）
│   ├── __main__.py                # 支持 `python -m ai_email`
│   ├── cli.py                     # CLI：setup 向导、daemon/worker、stop/status、PID+锁存活判定、隐藏密码输入
│   ├── workflow.py                # 核心引擎：EmailWorkflow、LLM 调用、路由、SMTP、pipeline
│   ├── qq_email_listener.py       # 异步 IMAP 监听（UID 增量拉取、UIDVALIDITY 守卫、重试队列排水）
│   ├── persistence.py             # SQLite 状态：去重账本、last_uid/UIDVALIDITY、重试队列
│   ├── qq_bot.py                  # QQ 官方 Bot 通知客户端（OAuth + token 缓存、业务错误识别）
│   ├── qq_onboard.py              # QQ Bot 扫码一键配置（QR 绑定任务、AES-GCM 密钥解密）
│   └── log_setup.py               # 单行 JSON 日志配置
├── tests/                         # pytest 套件（classification / cli / concurrency / listener / log_setup / persistence / qq_bot / qq_onboard / workflow）
├── pyproject.toml                 # 项目元数据与依赖
├── build.sh                       # 打包辅助（uv + PyInstaller 单文件）
├── Dockerfile                     # 容器镜像（uv 安装、非 root 运行、前台 ENTRYPOINT）
├── .env.example                   # ~/.ai-email/.env 模板
└── .github/workflows/
    ├── build-and-test.yml         # CI：lint / format / type / test / build
    └── publish.yml                # 发布：tag 触发，OIDC 可信发布到 PyPI
```

## 开发指南

### 主要组件

1. **`qq_email_listener.QQEmailListener`** —— 异步生成器，通过 IMAP UID 增量搜索（含 UIDVALIDITY 守卫）拉取新邮件，并在失败时透明重连；同时按退避窗口从 `retry_queue` 重投失败邮件。
2. **`EmailWorkflow`** —— 自定义异步工作流，携带 `WorkflowState` 流经各节点：
   - `classify_intent` —— LLM 分类（提示词内嵌 JSON Schema、容错提取、带重试）
   - `search_knowledge_base` —— `question`/`feature` 的知识库检索（占位实现；替换方法体即可接入 RAG/向量检索，契约：结果写入 `state.handle_results`）
   - `create_ticket` —— `bug` 的工单创建（占位实现；替换方法体接入工单平台，契约：工单号/链接写入 `state.handle_results`，按紧急度映射 P0/P1/P2）
   - `to_human` —— 复杂/高优先级邮件升级人工（+ QQ Bot 推送，带界重试）
   - `draft_response` —— LLM 起草回复
   - `send_reply` —— SMTP 回信（smtp.qq.com:587，STARTTLS）
3. **`QQBotNotifier`** —— QQ 官方 Bot 客户端，带 OAuth token 缓存与自动刷新、401 重试、业务错误码识别。
4. **CLI** —— `setup` 向导（含在线验证与扫码配置），以及 `daemon` / `stop` / `status`。存活由 `~/.ai-email/ai-email.lock` 的独占锁判定（worker 运行期间持有，故回收的 PID 不会被误判为守护进程）；worker PID 同时记入 `ai-email.pid`。

### 扩展点

可基于需求扩展：

- 接入 RAG / 向量检索知识库（实现 `search_knowledge_base`）
- 把 `create_ticket` 接到工单管理系统
- 增加多语言支持
- 增强人工审核界面
- 增加审计日志（当前结果仅存内存与本地 SQLite）

## 部署

### Docker（推荐用于后台/常驻）

镜像以 `ai-email` 作为 PID 1 **前台运行**（刻意不用 `daemon` 子命令：容器运行时已是 supervisor，自我二次守护化会破坏信号处理与重启策略）。将本地的 `~/.ai-email` 挂载为卷以持久化配置、SQLite 与日志：

```bash
docker run -v "$HOME/.ai-email:/home/aiemail/.ai-email" <image>
```

### PyInstaller 单文件（build.sh）

```bash
./build.sh
```

产物为 `dist/AIHandleQQEmail`，可直接 `setup` 后前台运行。注意：单文件模式下 `sys.executable` 不是解释器，`daemon` 子命令不可用（它需派生 `python -m ai_email _worker` 子进程）；后台运行请用 Docker 或 systemd。

## 注意事项

1. QQ 邮箱必须开启 IMAP 服务。
2. 必须使用 QQ 邮箱**授权码**而非登录密码。
3. 需稳定的网络以访问 AI API。
4. 生产环境建议：用 Docker/systemd 做进程管理与重启，并关注 `~/.ai-email/ai-email.log`。

## License

本项目基于 MIT License —— 详见 [LICENSE](LICENSE) 文件。
