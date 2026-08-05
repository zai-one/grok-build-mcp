# MCP / Grok CLI

Рабочая папка для интеграции **Grok Build CLI** через MCP.

## Round 8: единый MCP → три transport

`grok_delegate` 0.4.0 предоставляет один typed MCP surface над тремя явно
выбираемыми backend-ами:

| Transport | Назначение | Процесс |
|---|---|---|
| `legacy` | Совместимый Grok CLI/headless путь | отдельный `grok --single`/legacy delegate |
| `stdio` | Основной ACP v1 transport (`auto` указывает только сюда) | отдельный `grok agent stdio` на задачу |
| `websocket` | ACP v1 через loopback WebSocket | управляемый `grok agent serve` на задачу либо явно настроенный loopback daemon |

Общий lifecycle: `TaskPacket → TransportRouter → ACP/legacy executor → git
readback → WorkReceipt`. Роли `consult`/`skeptic` read-only; `execute`/`fix`
работают в отдельном git worktree. `completed` для write-роли требует непустой
diff, изменённые ожидаемые артефакты и независимое test evidence.
Для `execute`/`fix` обязательны явные `test_commands`; bridge повторно запускает
их сам без shell и помечает evidence как `source=bridge-verifier`.
Переиспользуемый worktree снимается до запуска: старый diff или заранее
созданный ожидаемый файл не может подтвердить новую задачу. Любой changed file,
не перечисленный в `expected_artifacts`, блокирует receipt.

Новые инструменты: `grok_agent_status`, `grok_agent_start`,
`grok_agent_poll`, `grok_agent_cancel`, `grok_agent_consult`,
`grok_agent_review`, `grok_agent_execute`, `grok_agent_fix`. Старые
`grok_delegate*` сохранены как compatibility surface legacy backend-а.

Quick start для Windows после merge в канонический каталог:

```powershell
$env:PYTHONPATH = 'D:\ZAI\MCP\Grok CLI'
$env:GROK_DELEGATE_ALLOWED_ROOTS = 'D:\ZAI\MCP\Grok CLI'
$env:GROK_DELEGATE_LANES_PARENT = 'D:\ZAI\MCP\grok-lanes'
$env:GROK_DELEGATE_JOBS_DIR = "$env:LOCALAPPDATA\grok-delegate\jobs"
py -3 -m grok_delegate.server
```

Подробности: [Codex setup](docs/CODEX-MCP-SETUP.md),
[transports](docs/ACP-TRANSPORTS.md), [security](docs/SECURITY.md) и
[Round 8 handoff](ROUND8-HANDOFF.md).

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
| **`grok_delegate_start` + `grok_delegate_poll`** | **делегирование в фоне (используй для реальных лейнов)**: `start` возвращает `job_id` сразу, `poll` отдаёт `state` + результат (`branch`, `changed_files`, `commits`, `diffstat`) |
| `grok_delegate` / `grok_delegate_plan` | синхронное делегирование / plan-only |
| `grok_delegate_status` | health JSON (бинарь, auth presence, git, roots, sandbox) |
| `grok_delegate_doctor` | `doctor --json` only |
| `grok_delegate_models` | `models` |
| `grok_delegate_inspect` | `inspect --json` для allowlisted root |

Мультипроектность: `GROK_DELEGATE_ALLOWED_ROOTS` (`;`-список) или один `GROK_DELEGATE_REPO_ROOT`.
Пустой allowlist → fail-closed.

> **Почему `start`/`poll`, а не `grok_delegate`.** Реальный лейн идёт минутами, а MCP-клиент
> обрывает синхронный вызов за секунды: процесс убивается на полпути, worktree остаётся пустым, и
> это неотличимо от «исполнитель ничего не сделал». `start` кладёт ту же guarded-делегацию в фон и
> сразу отдаёт `job_id`; права, изоляция worktree и запрет push/merge не меняются. Реестр задач
> живёт в процессе сервера (перезапуск сервера теряет незабранные результаты — работа при этом
> остаётся в ветке `grok/<lane>`).
>
> *Оговорка к первой фразе (2026-07-26):* с Claude Code v2.1.212 синхронный MCP-вызов из
> основного диалога через две минуты сам уходит в фоновую задачу, а стена по умолчанию —
> `MCP_TOOL_TIMEOUT` ≈ 28 часов. Клиент больше не рубит вызов «за секунды». Реальные потолки
> синхронного пути теперь другие: idle-таймаут stdio-сервера (30 минут без ответа и progress-
> уведомлений) и вызовы из субагентов, которые в фон не переводятся никогда.

### Poll — наблюдение, а не механизм продвижения

Историческое измерение 2026-07-26 показывало зависимость старого фонового пути
от частоты poll. В Round 8 работа исполняется собственным bounded
`ThreadPoolExecutor`; она продолжает выполняться без MCP poll. Poll только читает
in-memory/durable record. Старое измерение оставлено ниже как контекст устранённого
дефекта:

| | поллинг 20s | поллинг 5s |
|---|---|---|
| `git --version` | 20.018s | 4.912s |
| `git rev-parse --verify dev` | 20.005s | 5.011s |
| `git worktree add` | **отказ по таймауту** | 15.024s, успех |

Round 8 default: concurrency `1`, очередь `8`, отдельная ACP session на job.
Значения ограничены через `GROK_DELEGATE_CONCURRENCY` (1–2) и
`GROK_DELEGATE_MAX_QUEUED` (1–32); переполнение возвращает `QUEUE_FULL`.

### Таймаут — это не отказ окружения

Измерено 2026-07-27, и это стоило суток отладки не той болезни. `git worktree add` упёрся в
потолок, обёртка вернула `WORKTREE_CREATE_FAILED` — а на диске лежал **полный worktree**: 34
записи, чистый `git status`, нужная ветка, lock снят. Убийство подпроцесса не отменяет работу:
`git worktree add` раскладывает дерево в порождённом дочернем процессе, и на Windows тот
переживает `TerminateProcess` и спокойно доводит чекаут до конца. То есть обёртка не прерывала
операцию — она переставала на неё смотреть.

Тот же барьер раньше приходил под именами `GIT_MISSING` (на `git --version`) и
`BASE_UNREACHABLE` (на `origin/dev`) — оба ровно на 60 секундах, при том что те же команды из
оболочки отрабатывали за 0.13s и 1.5s. Диагноз «канал дохлый» был следствием этой подмены
ярлыков, а не наблюдением.

Что теперь:

- **`GIT_TIMEOUT`** — отдельный код, и он **retryable**. Он не в `hard_codes` драйвера, поэтому
  лейн получает повтор вместо мгновенной блокировки. Повтор обычно проходит сразу: подготовка
  подбирает worktree, оставленный прошлой попыткой.
- **Таймаут проверяет факт.** Если после него дерево на месте, на нужной ветке и git снял свой
  `initializing`-lock — это **успех** (`recovered_after_timeout: true`), а не отказ. Иначе —
  `GIT_TIMEOUT`, и worktree остаётся на месте для следующей попытки.
- **`WORKTREE_INITIALIZING`** — новый код на случай, когда чекаут ещё идёт. Раньше такой worktree
  мог быть отдан исполнителю наполовину разложенным. Тоже retryable.
- **`message` называет причину.** Раньше он говорил «git worktree add failed», а правда была
  видна только в `detail`.

| Переменная | Что бюджетирует | По умолчанию |
|---|---|---|
| `GROK_DELEGATE_GIT_TIMEOUT_SECONDS` | пробы: `--version`, `rev-parse`, `status` | 60s |
| `GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS` | `worktree add` (раскладка дерева) | 600s |

Один бюджет на всё был неверен в обе стороны: потолок чекаута задавался тем, что нужно пробе.
Обе переменные ограничены сверху 3600s; мусор и неположительное значение молча падают на дефолт.
Чекаут никогда не получает меньше пробы — поднятый только пробный knob означает «хост медленный»,
и это относится ко всему. Отсутствующий git по-прежнему падает мгновенно (`FileNotFoundError` →
`GIT_MISSING`), так что щедрый бюджет не замедляет настоящую поломку.

Для большого репозитория на медленном хосте: поднять чекаут до 600–900s **и** держать интервал
поллинга в 1–3 секунды. Одного второго без первого мало.

### Что видно, пока лейн идёт

Первые минуты диспатча внешне неотличимы от повисшего сервера: процесса `grok.exe` нет, каталога
лейна нет, ветки нет. Раньше `poll` в состоянии `running` отдавал только pid и `started_at` —
именно на этом наблюдении и рождается ошибочный вывод «канал мёртв».

`poll` теперь отдаёт:

| Поле | Смысл |
|---|---|
| `phase` | `queued` → `preflight` → `worktree` → (`worktree_recover`) → `anchors` → `executor` → `collect` |
| `elapsed_s`, `phase_elapsed_s` | сколько идёт весь лейн и текущая фаза |
| `last_step` | последняя git-команда |
| `worker_pid` | pid **исполнителя**, появляется на фазе `executor` |
| `server_pid` | pid MCP-сервера — владелец записи, не цель для kill |

Два pid вместо одного — потому что старое поле `pid` держало pid сервера. Из-за этого проверка
зависших записей (`is_stale_running`) не срабатывала никогда — процесс сервера жив всегда, — а
инструкция «убей pid зависшей задачи» означала «убей сервер вместе со всеми остальными лейнами».
Теперь `server_pid` решает вопрос «жива ли инкарнация, владеющая записью» (запись от другого pid
после перезапуска честно становится `unknown`), а `worker_pid` — это то, что оператор может убить.

Лог на диске: `GROK_DELEGATE_LOG_FILE` (или автоматически `<GROK_DELEGATE_JOBS_DIR>/grok-delegate.log`,
если включены durable jobs), уровень — `GROK_DELEGATE_LOG_LEVEL`, ротация на 2 МБ × 3. В stdout
не пишется ничего: там живёт JSON-RPC.

### `lane_verdict: false` для целей с разведкой

Схема вердикта — это то, чем **заканчивается** прогон. С `--json-schema` в argv deliverable
исполнителя есть сам объект вердикта, поэтому на цели, которую нельзя начать без чтения кода, он
выдаёт вердикт про намерение («изучаю конвенции пакета»), и лейн закрывается пустым.

Измерено 2026-07-26: пять лейнов подряд пустые по этой причине, при том что цель, выполнимая без
разведки, укладывалась в два хода с файлом на диске. Инструкция «не выдавай вердикт, пока не
закончишь» инвертировала отказ — 50 ходов вхолостую и `VERDICT_MISSING`.

Важная деталь про `_WRITE_FIRST_NUDGES`: **при включённой схеме подсказка «сначала пиши» ускоряет
не работу, а вердикт** — с `[driver retry 2]` он вышел на первом ходу. Логично: подсказка требует
немедленного вывода, а вывод при включённой схеме и есть объект вердикта. Ретрай с подсказкой
имеет смысл, когда схема **выключена**.

Та же цель с `lane_verdict: false` — четыре хода и реальный дифф на два файла. Но разброс есть:
другая цель в том же режиме вернулась пустой за пять ходов, поэтому выключенная схема снижает
вероятность пустого лейна, а не устраняет её — ретрай остаётся частью нормального цикла.

Дефолт остаётся включённым: разобранный вердикт полезен, когда цель достаточно мала, чтобы до него
дойти.

| Цель | `lane_verdict` |
|---|---|
| «создай файл X с содержимым Y», точечная правка по известному адресу | `true` (дефолт) |
| «найди, где собирается envelope, добавь поле, покрой тестом» | `false` |

С выключенной схемой отчёт исполнителя приходит прозой в `summary`, а `verdict` остаётся `null` —
судить о результате надо по `changed_files`, `commits` и `diffstat`.

### Пустой лейн не выдаётся за успех

`runner.delegate` намеренно рапортует `ok` на уровне запуска и оставляет детект пустоты
`driver.is_empty_result` — драйвер владеет обоими. MCP-клиент драйвера не видит, поэтому сервер
помечает такой случай сам: `ok:false`, `status: "no_changes"`, `error: "EXECUTE_NO_CHANGES"`,
`is_empty_result: true`, а `summary` исполнителя остаётся нетронутым — причина живёт там.
`plan_only`-лейны освобождены: ничего не менять — их работа.

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
