# 安装指南（简体中文）

`grok-delegate` 是一个**本地 stdio MCP 服务器**。它通过 stdin/stdout 与 MCP
宿主通信，并复用本机**已登录的 Grok CLI** 会话。它**不会**在 MCP 配置中实现
OAuth，也**不得**在宿主 JSON 中写入 API 密钥或 `GROK_AGENT_SECRET`。

软件包版本：**0.4.1**

---

## 前置条件

| 要求 | 说明 |
|---|---|
| **Python 3.10+** | `python3 --version` 或 `py -3 --version` |
| **Grok CLI** | 已安装并在 `PATH` 中（或设置 `GROK_DELEGATE_BIN`） |
| **已登录的 Grok CLI** | 在本机完成 CLI 常规登录一次 |
| **git** | worktree 与结果回读需要 |
| **本仓库克隆** | 见下方源码安装 |

验证 CLI 与会话（切勿将令牌粘贴到聊天或配置中）：

```bash
grok --version
grok models    # 本地 CLI 会话有效时应成功
```

---

## 从源码安装

```bash
git clone <REPO_URL> <REPO_PATH>
cd <REPO_PATH>
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
# 可选：测试依赖
pip install -e ".[test]"
```

可编辑安装会提供控制台脚本 `grok-delegate`（入口：
`grok_delegate.server:main`）。

---

## 如何运行服务器

服务器是长生命周期的 **stdio** 进程。MCP 宿主会启动它；你也可以手动启动以便
调试（进程会在 stdin 上等待）。

```bash
# pip install -e . 之后
grok-delegate

# 等价的模块形式
python -m grok_delegate.server
python -m grok_delegate
```

运维自检（非 MCP 宿主）：

```bash
python -m grok_delegate --self-test
python -m grok_delegate --smoke-delegate   # 可选的实时 plan-only smoke
python -m grok_delegate --help
```

最低可用环境变量（路径为占位符，请替换为你的路径）：

```bash
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_REPO_ROOT="<PROJECT_ROOT>"
export GROK_DELEGATE_LANES_PARENT="<LANES_PARENT>"
export GROK_DELEGATE_JOBS_DIR="<JOBS_DIR>"
# 可选
# export GROK_DELEGATE_BIN="grok"
# export PYTHONPATH="<REPO_PATH>"   # 仅在未 editable 安装时需要
```

空 allowlist → 失败关闭（`ALLOWED_ROOTS_EMPTY`）。

---

## 连接 Claude Desktop

编辑 Claude Desktop 的 MCP 配置（路径因操作系统而异；常见文件名
`claude_desktop_config.json`）。合并 `mcpServers` 条目——**不要包含密钥**：

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "grok-delegate",
      "args": [],
      "env": {
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

若 Claude 进程的 PATH 中没有 console script，请显式调用解释器：

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": ["-m", "grok_delegate.server"],
      "env": {
        "PYTHONPATH": "<REPO_PATH>",
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

保存后重启 Claude Desktop。另见 `examples/claude_desktop.mcp.json`。

---

## 连接 Claude Code

在项目根目录的 `.mcp.json`（或 Claude Code 文档指定的位置）：

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": ["-m", "grok_delegate.server"],
      "env": {
        "PYTHONPATH": "<REPO_PATH>",
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

模板：`examples/claude-code.mcp.json`。

---

## 连接 Codex CLI

```bash
codex mcp add grok-delegate \
  --env "GROK_DELEGATE_ALLOWED_ROOTS=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_REPO_ROOT=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_LANES_PARENT=<LANES_PARENT>" \
  --env "GROK_DELEGATE_JOBS_DIR=<JOBS_DIR>" \
  -- grok-delegate
```

或模块形式：

```bash
codex mcp add grok-delegate \
  --env "PYTHONPATH=<REPO_PATH>" \
  --env "GROK_DELEGATE_ALLOWED_ROOTS=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_REPO_ROOT=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_LANES_PARENT=<LANES_PARENT>" \
  --env "GROK_DELEGATE_JOBS_DIR=<JOBS_DIR>" \
  -- python -m grok_delegate.server
```

验证：`codex mcp list`。模板：`examples/codex.cli.example.sh`。

**切勿**向 `codex mcp add` 传入 `--env GROK_AGENT_SECRET=...`。

---

## 连接 Cursor

Cursor 的 MCP 配置（用户级或项目级 `mcp.json`——以当前 Cursor 文档为准）：

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": ["-m", "grok_delegate.server"],
      "env": {
        "PYTHONPATH": "<REPO_PATH>",
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

模板：`examples/cursor.mcp.json`。

---

## 连接 VS Code / Continue

### VS Code（支持 MCP 的版本 / Copilot MCP）

在产品文档所述的 MCP 服务器 JSON（用户或工作区设置）中，添加带相同**非机密**
`env` 键的 stdio 服务器条目：

```json
{
  "mcp": {
    "servers": {
      "grok-delegate": {
        "type": "stdio",
        "command": "python",
        "args": ["-m", "grok_delegate.server"],
        "env": {
          "PYTHONPATH": "<REPO_PATH>",
          "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
          "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
          "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
          "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
        }
      }
    }
  }
}
```

字段名可能因 VS Code 版本而异——保留 **command / args / env** 与 stdio 传输；
不要为本服务器添加远程 URL 或 OAuth 字段。

### Continue

在 Continue 的 MCP / 服务器配置（YAML 或 JSON，视版本而定）中，注册指向
`grok-delegate` 或 `python -m grok_delegate.server` 的 **stdio** MCP 服务器，
并使用相同环境变量。不要为本软件包配置 HTTP/SSE 端点。

---

## ChatGPT / OpenAI

ChatGPT 自定义 MCP 连接器面向**远程 HTTP**（或类似托管）端点。**本软件包是
本地 stdio 进程**，不是 OpenAI 托管的远程 MCP 服务器。

可选方案：

1. **推荐：** 仅将 `grok-delegate` 与会启动 **stdio** MCP 的**本地**宿主一起
   使用（Claude Desktop、Claude Code、Codex CLI、Cursor、本地 VS Code /
   Continue）。
2. **若**你通过自己运维的**可信 MCP 桥**暴露它（机器上的 stdio ↔ 远程前端），
   请将该桥视为高风险基础设施：切勿在远程或共享配置中放入 OAuth 密钥、API
   密钥或 `GROK_AGENT_SECRET`；尽量缩小 allowlist 根路径；WebSocket 密钥仅保留
   在运行 Grok 的机器的进程内存中。

本指南**不会**编造本仓库并不存在的 ChatGPT UI 点击步骤。OpenAI 的远程 MCP
产品功能请遵循其现行文档；它们与本本地服务器无关。

---

## 通用 MCP 宿主 JSON

任何能启动本地 stdio MCP 服务器的宿主：

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": ["-m", "grok_delegate.server"],
      "env": {
        "PYTHONPATH": "<REPO_PATH>",
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

多个精确根路径：在 `GROK_DELEGATE_ALLOWED_ROOTS` 中用 `;` 分隔。allowlist 根
的**子路径不会**被隐式信任——每个 `project_root` 必须与 allowlist 条目**精确**
匹配。

---

## 首次验证

### 1. 运维自检

```bash
cd <REPO_PATH>
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
python -m grok_delegate --self-test
```

预期看到关于二进制、版本、认证存在性、git 以及 status 工具 JSON-RPC 路径的
PASS/FAIL 表。全部通过需要有效的本地 Grok CLI 会话。

### 2. 在 MCP 宿主中

宿主列出 tools 后：

1. 确认服务器信息版本为 **0.4.1**（initialize / status）。
2. 调用 **`grok_agent_status`**（或兼容的 `grok_delegate_status`）。
3. 确认默认传输行为：**stdio**（`auto` → 仅 stdio，不会静默回退到
   WebSocket/legacy）。
4. 首次写操作测试仅在**临时** git 仓库上进行，而非生产 monorepo。

### 3. 单元测试（贡献者）

```bash
pip install -e ".[test]"
pytest tests -q
```

---

## 传输层说明

容易混淆的两层：

| 层级 | 含义 | 谁连接 |
|---|---|---|
| **MCP ↔ 宿主** | 本软件包始终为 **stdio** JSON-RPC | Claude / Codex / Cursor 等启动子进程 |
| **桥 ↔ Grok agent** | 服务器内部选择的 **backend 传输** | `legacy`、`stdio`（ACP）或 `websocket`（ACP） |

### Backend 传输（任务包 / 工具参数）

| 值 | 作用 |
|---|---|
| `legacy` | Grok CLI headless 路径（`grok --single` / 旧版 delegate） |
| `stdio` | 通过每任务 `grok agent stdio` 进程的 ACP v1（**默认**；`auto` 仅别名到此） |
| `websocket` | 通过**回环** WebSocket 连接托管或运维自启的 `grok agent serve` 的 ACP v1 |
| `auto` | 仅为 `stdio` 的别名——**无**回退级联 |

MCP **不是**到宿主的 WebSocket。WebSocket 仅是到**本地**回环 Grok agent 的
可选 ACP 路径。详见 `docs/ACP-TRANSPORTS.md`。

---

## 环境变量

文档与示例中仅使用占位符。建议使用绝对路径。

| 变量 | 必需 | 说明 |
|---|---|---|
| `GROK_DELEGATE_ALLOWED_ROOTS` | 是* | 精确项目根 allowlist（`;` 分隔或 JSON 数组） |
| `GROK_DELEGATE_REPO_ROOT` | 是* | 未设置 `ALLOWED_ROOTS` 时的单根钉扎 |
| `GROK_DELEGATE_LANES_PARENT` | 建议 | 外部 git worktree 的父目录 |
| `GROK_DELEGATE_JOBS_DIR` | 建议 | 持久化任务记录（及可选同目录日志） |
| `GROK_DELEGATE_BIN` | 否 | 仅限 `grok` / `grok.exe` 的路径或名称 |
| `GROK_DELEGATE_SANDBOX` / `GROK_SANDBOX` | 否 | 沙箱配置覆盖（`off` 关闭） |
| `GROK_DELEGATE_CONCURRENCY` | 否 | 1–2（默认 1） |
| `GROK_DELEGATE_MAX_QUEUED` | 否 | 1–32（默认 8） |
| `GROK_DELEGATE_GIT_TIMEOUT_SECONDS` | 否 | git 探测超时（默认 60） |
| `GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS` | 否 | `worktree add` 预算（默认 600） |
| `GROK_DELEGATE_LOG_FILE` | 否 | 日志路径（切勿将 MCP JSON-RPC 写到 stdout） |
| `GROK_DELEGATE_LOG_LEVEL` | 否 | 例如 `INFO` |
| `GROK_DELEGATE_WS_ENDPOINT` | 可选高级 | 仅回环 WS URL，例如 `ws://127.0.0.1:<PORT>/ws` |
| `PYTHONPATH` | 未安装时 | 使用 `python -m` 且未 editable 安装时设为 `<REPO_PATH>` |

\* `GROK_DELEGATE_ALLOWED_ROOTS` 与 `GROK_DELEGATE_REPO_ROOT` 至少其一须产生
非空 allowlist。

### 密钥——仅进程内，永不写入配置文件

| 变量 | 规则 |
|---|---|
| `GROK_AGENT_SECRET` | **绝不**出现在 MCP JSON、git 或示例中。仅用于可选运维自启 WS 守护进程的 process env；managed 模式在内存中生成临时密钥。 |
| OAuth 令牌 / API 密钥 | **绝不**为本服务器设置。请在本机使用 Grok CLI 登录。 |

---

## 配置安全规则

1. **绝不**在配置文件中放入 `GROK_AGENT_SECRET`、API 密钥或 OAuth 令牌。
2. **绝不**提交真实主目录路径或私有根路径；共享模板仅用占位符。
3. 尽量将 lane 与 job 目录放在源码仓库**之外**。
4. 合并前检查 worktree diff；本服务器从不 push 或 merge。
5. 通过 GitHub Security Advisories 报告漏洞——见根目录 `SECURITY.md`。

---

## 故障排除

| 现象 | 检查项 |
|---|---|
| `ALLOWED_ROOTS_EMPTY` / 配置错误 | 设置 `GROK_DELEGATE_ALLOWED_ROOTS` 或 `GROK_DELEGATE_REPO_ROOT` |
| 客户端 root 被拒绝 | root 必须与 allowlist **精确**匹配（不能是子路径） |
| self-test 显示无认证 | 在本 OS 用户下执行 Grok CLI 登录；不要把令牌写进 MCP 配置 |
| `GROK_MISSING` | 安装 CLI 或将 `GROK_DELEGATE_BIN` 设为安全的 `grok` 路径 |
| 宿主中看不到 tools | 改配置后重启宿主；确认 command 在宿主 PATH 中 |
| 手动启动时“卡住” | 正常：stdio 正在等待宿主的 JSON-RPC |
| WebSocket 失败 | 仅用回环；密钥不进配置；优先 managed 模式 |
| `QUEUE_FULL` | 降低并发负载，或在上限内提高 `GROK_DELEGATE_MAX_QUEUED` |
| 重启后任务 stale / `unknown` | 持久记录可能标记为 orphaned；检查 worktree，勿当作成功 |
| ChatGPT 远程连接器 | 本服务器是本地 stdio——见上文 ChatGPT 小节 |

日志：设置 `GROK_DELEGATE_LOG_FILE`，或在配置 jobs dir 后使用
`<JOBS_DIR>/grok-delegate.log`。不要记录密钥。

---

## 相关文档

- `docs/ACP-TRANSPORTS.md` — ACP stdio/WebSocket 细节  
- `docs/SECURITY.md` — 强制措施与残留风险  
- 根目录 `SECURITY.md` — 报告流程与凭证策略  
- `examples/` — 仅含占位符的 JSON 与 shell 模板  
