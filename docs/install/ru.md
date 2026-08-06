# Руководство по установке (русский)

`grok-delegate` — это **локальный stdio MCP-сервер**. Он общается с MCP-хостами
через stdin/stdout и повторно использует **уже выполненную локальную сессию
Grok CLI**. Сервер не реализует OAuth внутри MCP-конфига и **не должен**
получать API-ключи или `GROK_AGENT_SECRET` в JSON-файлах хоста.

Версия пакета: **0.5.0**

---

## Предварительные требования

| Требование | Примечание |
|---|---|
| **Python 3.10+** | `python3 --version` или `py -3 --version` |
| **Grok CLI** | Установлен и доступен в `PATH` (или задайте `GROK_DELEGATE_BIN`) |
| **Вход в Grok CLI** | Один раз выполните обычный login CLI на этой машине |
| **git** | Нужен для worktree и readback |
| **Клон репозитория** | Установка из исходников ниже |

Проверка CLI и сессии (никогда не вставляйте токены в чат или конфиг):

```bash
grok --version
grok models    # должен успешно отработать при валидной локальной сессии CLI
```

---

## Установка из исходников

```bash
git clone <REPO_URL> <REPO_PATH>
cd <REPO_PATH>
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
# опционально: зависимости для тестов
pip install -e ".[test]"
```

Редактируемая установка добавляет console script `grok-delegate` (точка входа:
`grok_delegate.server:main`).

---

## Как запускать сервер

Сервер — долгоживущий процесс **stdio**. MCP-хосты порождают его сами; вручную
можно запустить для отладки (будет ждать данные на stdin).

```bash
# После pip install -e .
grok-delegate

# Эквивалентные модульные формы
python -m grok_delegate.server
python -m grok_delegate
```

Операторские проверки (не через MCP-хост):

```bash
python -m grok_delegate --self-test
python -m grok_delegate --smoke-delegate   # опциональный live plan-only smoke
python -m grok_delegate --help
```

Минимально полезное окружение (пути — плейсхолдеры, замените своими):

```bash
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_REPO_ROOT="<PROJECT_ROOT>"
export GROK_DELEGATE_LANES_PARENT="<LANES_PARENT>"
export GROK_DELEGATE_JOBS_DIR="<JOBS_DIR>"
# опционально
# export GROK_DELEGATE_BIN="grok"
# export PYTHONPATH="<REPO_PATH>"   # только если пакет не установлен editable
```

Пустой allowlist → fail-closed (`ALLOWED_ROOTS_EMPTY`).

---

## Подключение Claude Desktop

Отредактируйте MCP-конфиг Claude Desktop (путь зависит от ОС; типичное имя —
`claude_desktop_config.json`). Добавьте запись `mcpServers` — **без секретов**:

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

Если console script недоступен в PATH процесса Claude, вызывайте интерпретатор
явно:

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

После сохранения перезапустите Claude Desktop. См. также
`examples/claude_desktop.mcp.json`.

---

## Подключение Claude Code

Проектный `.mcp.json` в корне проекта (или путь, указанный в документации
Claude Code):

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

Шаблон: `examples/claude-code.mcp.json`.

---

## Подключение Codex CLI

```bash
codex mcp add grok-delegate \
  --env "GROK_DELEGATE_ALLOWED_ROOTS=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_REPO_ROOT=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_LANES_PARENT=<LANES_PARENT>" \
  --env "GROK_DELEGATE_JOBS_DIR=<JOBS_DIR>" \
  -- grok-delegate
```

Или модульная форма:

```bash
codex mcp add grok-delegate \
  --env "PYTHONPATH=<REPO_PATH>" \
  --env "GROK_DELEGATE_ALLOWED_ROOTS=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_REPO_ROOT=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_LANES_PARENT=<LANES_PARENT>" \
  --env "GROK_DELEGATE_JOBS_DIR=<JOBS_DIR>" \
  -- python -m grok_delegate.server
```

Проверка: `codex mcp list`. Шаблон: `examples/codex.cli.example.sh`.

**Никогда** не передавайте `--env GROK_AGENT_SECRET=...` в `codex mcp add`.

---

## Подключение Cursor

Конфиг MCP Cursor (пользовательский или проектный `mcp.json` — точный путь
смотрите в актуальной документации Cursor):

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

Шаблон: `examples/cursor.mcp.json`.

---

## Подключение VS Code / Continue

### VS Code (сборки с MCP / Copilot MCP)

Там, где продукт документирует JSON серверов MCP (user или workspace), добавьте
stdio-сервер с теми же **несекретными** ключами `env`:

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

Имена полей могут отличаться в релизах VS Code — сохраняйте **command / args /
env** и транспорт stdio; не добавляйте remote URL или OAuth-поля для этого
сервера.

### Continue

В конфигурации MCP / серверов Continue (YAML или JSON в зависимости от версии)
зарегистрируйте **stdio** MCP-сервер на `grok-delegate` или
`python -m grok_delegate.server` с теми же переменными окружения. Не
настраивайте HTTP/SSE endpoints для этого пакета.

---

## ChatGPT / OpenAI

Пользовательские MCP-коннекторы ChatGPT рассчитаны на **удалённый HTTP** (или
аналогичный hosted) endpoint. **Этот пакет — локальный stdio-процесс**, а не
hosted remote MCP-сервер OpenAI.

Варианты:

1. **Предпочтительно:** используйте `grok-delegate` только с **локальными**
   хостами, которые порождают stdio MCP (Claude Desktop, Claude Code, Codex
   CLI, Cursor, локальный VS Code / Continue).
2. **Если** вы выставляете его через **доверенный MCP-мост**, которым
   управляете сами (stdio на машине ↔ удалённый фронт), относитесь к мосту как
   к высокорисковой инфраструктуре: никогда не кладите OAuth-секреты, API-ключи
   или `GROK_AGENT_SECRET` в удалённый или общий конфиг; минимизируйте
   allowlist корней; секреты WebSocket держите только в памяти процесса на
   машине с Grok.

Это руководство **не** выдумывает шаги UI ChatGPT, которых нет в поверхности
данного репозитория. Актуальные remote MCP-возможности OpenAI — отдельный
продукт; следуйте их документации.

---

## Универсальный JSON MCP-хоста

Любой хост, умеющий запускать локальный stdio MCP-сервер:

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

Несколько точных корней: разделяйте `;` в `GROK_DELEGATE_ALLOWED_ROOTS`.
Потомки allowlisted-корня **не** доверяются неявно — каждый `project_root`
должен **точно** совпадать с записью allowlist.

---

## Первая проверка

### 1. Операторский self-test

```bash
cd <REPO_PATH>
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
python -m grok_delegate --self-test
```

Ожидайте таблицу PASS/FAIL: бинарь, версия, наличие auth, git, JSON-RPC
status-tools. Полностью зелёный результат требует валидной локальной сессии
Grok CLI.

### 2. Из MCP-хоста

После того как хост показал список tools:

1. Убедитесь, что версия сервера **0.5.0** (initialize / status).
2. Вызовите **`grok_agent_status`** (или совместимый `grok_delegate_status`).
3. Проверьте транспорт по умолчанию: **stdio** (`auto` → только stdio, без
   тихого каскада на WebSocket/legacy).
4. Первый write-тест делайте только во **временном** git-репозитории, не в
   продовом monorepo.

### 3. Юнит-тесты (для контрибьюторов)

```bash
pip install -e ".[test]"
pytest tests -q
```

---

## Транспорты

Два разных слоя часто путают:

| Слой | Что это | Кто соединяется |
|---|---|---|
| **MCP ↔ хост** | Всегда **stdio** JSON-RPC для этого пакета | Claude / Codex / Cursor и т.д. порождают процесс |
| **Мост ↔ Grok agent** | Выбранный **backend-транспорт** внутри сервера | `legacy`, `stdio` (ACP) или `websocket` (ACP) |

### Backend-транспорты (аргумент task packet / tool)

| Значение | Назначение |
|---|---|
| `legacy` | Headless путь Grok CLI (`grok --single` / legacy delegate) |
| `stdio` | ACP v1 через per-task процесс `grok agent stdio` (**по умолчанию**; `auto` — алиас сюда) |
| `websocket` | ACP v1 через **loopback** WebSocket к managed или операторскому `grok agent serve` |
| `auto` | Только алиас `stdio` — **без** каскада fallback |

MCP **не** является WebSocket к хосту. WebSocket — только опциональный ACP-путь
к **локальному** Grok agent на loopback. См. `docs/ACP-TRANSPORTS.md`.

---

## Переменные окружения

В документации и примерах — только плейсхолдеры. Предпочтительны абсолютные
пути.

| Переменная | Обязательна | Описание |
|---|---|---|
| `GROK_DELEGATE_ALLOWED_ROOTS` | Да* | Allowlist точных корней проектов (`;` или JSON-массив) |
| `GROK_DELEGATE_REPO_ROOT` | Да* | Один корень, если `ALLOWED_ROOTS` не задан |
| `GROK_DELEGATE_LANES_PARENT` | Рекомендуется | Родительский каталог внешних git worktree |
| `GROK_DELEGATE_JOBS_DIR` | Рекомендуется | Durable job records (+ опциональный лог рядом) |
| `GROK_DELEGATE_BIN` | Нет | Путь или имя только `grok` / `grok.exe` |
| `GROK_DELEGATE_SANDBOX` / `GROK_SANDBOX` | Нет | Профиль sandbox (`off` отключает) |
| `GROK_DELEGATE_CONCURRENCY` | Нет | 1–2 (по умолчанию 1) |
| `GROK_DELEGATE_MAX_QUEUED` | Нет | 1–32 (по умолчанию 8) |
| `GROK_DELEGATE_GIT_TIMEOUT_SECONDS` | Нет | Таймаут git-проб (по умолчанию 60) |
| `GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS` | Нет | Бюджет `worktree add` (по умолчанию 600) |
| `GROK_DELEGATE_LOG_FILE` | Нет | Путь лога (MCP JSON-RPC никогда не в stdout) |
| `GROK_DELEGATE_LOG_LEVEL` | Нет | Например `INFO` |
| `GROK_DELEGATE_WS_ENDPOINT` | Опционально, advanced | Только loopback WS URL, напр. `ws://127.0.0.1:<PORT>/ws` |
| `PYTHONPATH` | Если не установлен | `<REPO_PATH>` при `python -m` без editable install |

\* Хотя бы один из `GROK_DELEGATE_ALLOWED_ROOTS` / `GROK_DELEGATE_REPO_ROOT`
должен дать непустой allowlist.

### Секреты — только в процессе, никогда в конфигах

| Переменная | Правило |
|---|---|
| `GROK_AGENT_SECRET` | **Никогда** в MCP JSON, git или примерах. Только process env для опционального operator-run WS daemon; managed mode генерирует ephemeral-секрет в памяти. |
| OAuth-токены / API-ключи | **Никогда** не задавайте для этого сервера. Используйте login Grok CLI на машине. |

---

## Правила безопасности для конфигов

1. **Никогда** не кладите `GROK_AGENT_SECRET`, API-ключи или OAuth-токены в
   файлы конфигурации.
2. **Никогда** не коммитьте реальные home-пути или приватные корни; в общих
   шаблонах — только плейсхолдеры.
3. Каталоги lanes и jobs по возможности держите **вне** исходного репозитория.
4. Перед merge просматривайте diff worktree; сервер никогда не делает push/merge.
5. Уязвимости — через GitHub Security Advisories; см. корневой `SECURITY.md`.

---

## Устранение неполадок

| Симптом | Что проверить |
|---|---|
| `ALLOWED_ROOTS_EMPTY` / setup error | Задайте `GROK_DELEGATE_ALLOWED_ROOTS` или `GROK_DELEGATE_REPO_ROOT` |
| Client root rejected | Корень должен **точно** совпадать с allowlist (не дочерний путь) |
| Auth absent в self-test | Выполните login Grok CLI под этим OS-пользователем; не добавляйте токены в MCP-конфиг |
| `GROK_MISSING` | Установите CLI или задайте безопасный путь в `GROK_DELEGATE_BIN` |
| Tools отсутствуют в хосте | Перезапустите хост после смены конфига; command должен быть в PATH хоста |
| Сервер «висит» при ручном запуске | Ожидаемо: stdio ждёт JSON-RPC от хоста |
| Сбои WebSocket | Только loopback; секреты не в конфиге; предпочтителен managed mode |
| `QUEUE_FULL` | Снизьте нагрузку или поднимите `GROK_DELEGATE_MAX_QUEUED` в пределах cap |
| Stale / `unknown` jobs после рестарта | Durable records могут помечать orphaned runs; смотрите worktree, не считайте это success |
| Remote-коннектор ChatGPT | Сервер — local stdio; см. раздел ChatGPT выше |

Логи: `GROK_DELEGATE_LOG_FILE` или `<JOBS_DIR>/grok-delegate.log` при
настроенном jobs dir. Не логируйте секреты.

---

## Связанные документы

- `docs/ACP-TRANSPORTS.md` — детали ACP stdio/WebSocket  
- `docs/SECURITY.md` — enforcement и residual risk  
- Корневой `SECURITY.md` — reporting и политика credentials  
- `examples/` — JSON и shell-шаблоны только с плейсхолдерами


---

## Отказ от ответственности (неофициальный продукт)

> **Сообщественный проект.** Это **не** официальный продукт **xAI**, **Grok**,
> Anthropic, OpenAI или Codex. Нет аффилиации и поддержки. Аутентификация —
> только **локальная сессия Grok CLI** (`grok login`). **Никогда** не кладите
> OAuth, API-ключи или `GROK_AGENT_SECRET` в MCP-конфиг.

---

## Token economy (экономия токенов)

Хост-агент (Claude / Cursor) оркестрирует короткими промптами; **Grok CLI**
делает длинный coding loop на машине или VPS.

| Переменная | Назначение |
|---|---|
| `GROK_DELEGATE_ECONOMY=1` | Более низкие default `max_turns` / timeout / reasoning |
| `GROK_DELEGATE_ECONOMY_COMPACT_POLL=1` | Компактные poll/job payload для окна хоста |

Инструмент сессии: **`grok_agent_economy`**.

Последовательность: `status` → `economy` → `consult`/`review` → `execute` → `poll`.

Гид: [../economy.md](../economy.md) (EN).

---

## FastMCP

| Путь | Как |
|---|---|
| Локальный stdio | Хост/FastMCP запускает `python -m grok_delegate.server` |
| Удалённый proxy | HTTP на VPS + TLS; локальный FastMCP `create_proxy` с bearer |

[fastmcp.md](fastmcp.md) · [../../examples/fastmcp_proxy.py](../../examples/fastmcp_proxy.py)

---

## VPS (HTTP bearer, не OAuth)

```bash
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_HTTP_TOKEN_FILE="<TOKEN_FILE>"
python -m grok_delegate.server --transport http --host 127.0.0.1 --port 8765
```

Bearer — **операторский секрет**, не Grok OAuth.  
[vps.md](vps.md) · [../../examples/vps.systemd.service](../../examples/vps.systemd.service) ·
[../../examples/http.env.example](../../examples/http.env.example)

---

## Переменные economy / HTTP

| Переменная | Описание |
|---|---|
| `GROK_DELEGATE_ECONOMY` | Включает defaults бюджета задачи |
| `GROK_DELEGATE_ECONOMY_COMPACT_POLL` | Компактный poll |
| `GROK_DELEGATE_HTTP_TOKEN` | Bearer в env (взаимоисключён с file) |
| `GROK_DELEGATE_HTTP_TOKEN_FILE` | Путь к файлу bearer (`<TOKEN_FILE>`) |
| `GROK_DELEGATE_HTTP_HOST` / `PORT` | По умолчанию `127.0.0.1:8765` |
