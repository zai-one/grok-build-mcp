# MCP / Grok CLI

Рабочая папка для интеграции **Grok Build CLI** через MCP.

## Что здесь подключено

| Компонент | Как подключён | Файл |
|---|---|---|
| `grok` (консультации) | глобально + project-scope, `npx -y grok-cli-mcp` | `.mcp.json`, `claude_desktop_config.json` |
| Infra Ops (плагин `infra-ops@zai` v0.3.2) | project-scope, скиллы + агенты | `.claude/settings.json` |
| `grok-delegate` (делегирование) | **глобально**, `python grok_delegate/server.py` | `claude_desktop_config.json` |

### Два сервера, а не один

Они **дополняют** друг друга, а не заменяют:

- **`grok`** (`grok-cli-mcp`) — спросить Grok: `grok_chat`, `grok_consult`, `grok_review`,
  `grok_challenge`. Используется для второго мнения и adversarial-разбора.
- **`grok-delegate`** (этот проект) — поручить Grok работу: `grok_delegate`,
  `grok_delegate_plan` + статус-тулы. Чата не даёт.

Пересечения по именам инструментов нулевые. Убрать `grok` = потерять консультации и ревью.

## `grok_delegate` — собственный делегирующий сервер

Python-пакет `grok_delegate/` — dev-only MCP-сервер: принимает кодинг-цель, заводит изолированный
git worktree на ветке `grok/*`, запускает локальный `grok` headless и возвращает ветку + diffstat.
Без auto-merge, без push, без `--always-approve`.

Round 3 (R1–R5): [EVIDENCE.md](EVIDENCE.md), [GOAL-ROUND3.md](GOAL-ROUND3.md).  
Round 4 (статус-тулы, multi-root allowlist, `--sandbox`, self-test): [EVIDENCE-ROUND4.md](EVIDENCE-ROUND4.md),
[GOAL-ROUND4.md](GOAL-ROUND4.md). Пакетный README: [grok_delegate/README.md](grok_delegate/README.md).

### Как проверить, что MCP жив (без перезапуска Claude)

```bash
cd "C:\Users\codex\Documents\Projects\MCP\Grok CLI"
py -3 -m grok_delegate --self-test        # PASS/FAIL таблица, без делегирования
py -3 -m grok_delegate --smoke-delegate   # живой plan-only headless smoke
py -3 -m pytest tests -q                  # unit (mocked)
```

### Инструменты `grok_delegate`

| Tool | Назначение |
|---|---|
| `grok_delegate` / `grok_delegate_plan` | делегирование / plan-only |
| `grok_delegate_status` | health JSON (бинарь, auth presence, git, roots, sandbox) |
| `grok_delegate_doctor` | `doctor --json` only |
| `grok_delegate_models` | `models` |
| `grok_delegate_inspect` | `inspect --json` для allowlisted root |

Мультипроектность: `GROK_DELEGATE_ALLOWED_ROOTS` (`;`-список) или один `GROK_DELEGATE_REPO_ROOT`.
Пустой allowlist → fail-closed.

**Подключён глобально** (2026-07-24) в `claude_desktop_config.json` как `grok-delegate`:

```json
"grok-delegate": {
  "command": "…\\Python314\\python.exe",
  "args": ["…\\MCP\\Grok CLI\\grok_delegate\\server.py"],
  "env": {
    "GROK_DELEGATE_BIN": "…\\.grok\\bin\\grok.exe",
    "GROK_DELEGATE_ALLOWED_ROOTS": "…\\Phone Control Plane;…\\MCP\\Grok CLI"
  }
}
```

Новый проект добавляется строкой в `GROK_DELEGATE_ALLOWED_ROOTS` (разделитель `;`) — корни вне
списка отвергаются (`REPO_ROOT_UNTRUSTED`), пустой список = fail-closed.

> Sandbox-профиль по умолчанию включён в argv; OS-enforcement на Windows **не** заявлен как
> гарантия (см. EVIDENCE-ROUND4). Изоляция держится на worktree + запрете push/merge в коде.

## Авторизация

Ключ **не нужен**. `grok-cli-mcp` переиспользует OAuth-сессию установленного Grok CLI из
`C:\Users\codex\.grok\auth.json` (вход по SuperGrok / X Premium+). Поэтому в `.mcp.json`
нет блока `env` — секретов в репозитории не хранится.

Если сервер перестанет видеть сессию — перелогиниться в обычном терминале (`grok login`),
это обновит `auth.json`.

## Инструменты

От `grok`: `grok_chat` (one-shot), `grok_consult` (многоходовой), `grok_review` (ревью git-диффа,
`format=json` для CI-гейтинга), `grok_challenge` (adversarial-разбор кода).

Известное ограничение: короткие запросы проходят стабильно, объёмные payload-ы
(`grok_challenge` по большому файлу) упирались в таймаут MCP.

От Infra Ops: скиллы/агенты для Proxmox, Windows, SSH, ADB-туннелей, Passbolt.
Транспорт — центральный `zai-mcp` (`https://mcp.zai.one/mcp`), он подключён на уровне
пользователя, поэтому отдельной настройки в проекте не требует.

## Границы

`grok-cli-mcp` — **консультативно-ревьюшный** канал: он не заводит ветки и не правит файлы сам.
Делегирование кодинг-целей с worktree-изоляцией — это `grok_delegate/` (см. выше), и оно
подключается отдельным решением.

Grok также остаётся подключённым глобально в `claude_desktop_config.json` — здешний
project-scope его дублирует, а не заменяет.
