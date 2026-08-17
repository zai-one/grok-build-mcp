# Аудит: доки, кроссплатформенность, установка (2026-08-17)

Краткий контекст: репозиторий `D:\ZAI\MCP\Grok CLI` (`zai-one/grok-mcp`), ветка `main`, HEAD `e79e0bf1f173c19173ab855248f170757f63b9dc` (`e79e0bf docs(audits): a home for independent audit findings`). Версия `0.10.0` в `pyproject.toml` и `grok_delegate/guard.py` (`SERVER_VERSION`). Дата аудита: 2026-08-17. ОС аудитора: Windows 11, PowerShell; Grok CLI `1.0.4 (d846eb93d9) [stable]`. Linux/macOS не было.

Сверка с `Service/Handoffs/grok-mcp-production-ready-evidence.md`: пустой `"env": {}` в Windows-сниппете **исправлен** в текущем `scripts/install.ps1` (сниппет несёт env). `scripts/capture_acp_initialize.py` в текущих операторских доках (`README.md`, `AGENTS.md`, `docs/**`, `skills/grok-mcp/**`, `.github/**`) **не упоминается** — только в CHANGELOG как заменённый и в старом handoff. Unpin по умолчанию в коде держится. Ниже — только то, что снова расходится или не было закрыто evidence.

## Метод

Читал целиком: `AGENTS.md`, `Service/Handoffs/grok-mcp-production-ready-evidence.md`, `README.md`, `CLAUDE.md`, `docs/**`, `skills/grok-mcp/**`, `.github/ISSUE_TEMPLATE/**`, `pyproject.toml`, `scripts/install.ps1`, `scripts/install.sh`, `grok_delegate/server.py` (`list_tools`), `grok_delegate/acp.py` (`_ALLOWED_COMMAND`), `grok_delegate/economy.py`, `grok_delegate/project_config.py`, `grok_delegate/__main__.py`, `grok_delegate/updater.py`, `grok_delegate/status.py`, `grok_delegate/agent_runtime.py`, `grok_delegate/runner.py`.

Команды в temp clone `%TEMP%\grokmcp-install` (клон `git clone -- "D:\ZAI\MCP\Grok CLI"`, отдельный `.venv`, `pip install -e ".[test]"`; интерпретатор `Python 3.14.5`):

- `.\.venv\Scripts\python.exe -m pytest tests -q` → **604 passed, 1 failed, 1 skipped**, 79 subtests, 82.43s. Упавший: `tests/test_round8_bridge.py::test_managed_websocket_stderr_is_cumulatively_bounded`. Изолированно тот же тест: 3/3 passed (флейк под полной нагрузкой, не стабильный ред). `--collect-only`: 606 collected (605 runnable + 1 skip). `tests/test_live_acp_fixtures.py`: 33 collected — совпадает с CHANGELOG/evidence.
- `list_tools()` / `initialize`: 23 инструмента, protocol `2024-11-05`, server `0.10.0`.
- `_command_allowed` на типичных `test_commands`.
- `_apply_project_gate` без `.grok-mcp.json` → `PROJECT_NOT_ENABLED`.
- `python -m grok_delegate --self-test` с grok в PATH (PASS) и с PATH без `grok` (FAIL + plain English).
- Логика записи JSON из `install.ps1` воспроизведена в `%TEMP%\grokmcp-snippet-sim\` (скрипт **не** запускался: он всегда пишет в `%USERPROFILE%\.config\grok-mcp`, dry-run нет). Реальные `%APPDATA%\Claude`, `%USERPROFILE%\.cursor`, live MCP **не** писались.

Не трогал: рабочее дерево `D:\ZAI\MCP\Grok CLI` кроме этого файла; `C:\Users\codex\AppData\Local\grok-mcp` и её `.venv`; git checkout/stash/restore/reset; чужие audit-файлы параллельных агентов. `install.ps1` / `install.sh` на POSIX не гонялись (нет Linux/macOS).

## Находки

### F-1 `tools/list` — 23 инструмента, не 21
- Серьёзность: major
- Статус: подтверждено
- Где: docs/CODEX-MCP-SETUP.md:51-53
- Написано: «`tools/list` must include all 13 `grok_agent_*` tools and all 8 `grok_delegate*` compatibility tools — 21 in total.»
- На самом деле: `list_tools()` возвращает **15** `grok_agent_*` + **8** `grok_delegate*` = **23**. Лишние относительно «13»: `grok_agent_update`, `grok_agent_project`. `initialize` отдаёт `serverInfo.version=0.10.0`, `protocolVersion=2024-11-05` — эти два числа из того же абзаца верны. CHANGELOG 0.9.0 утверждал, что счётчики в Codex setup поправили; документ снова отстал.
- Репро: `py -3 -c "from grok_delegate.server import list_tools; n=list_tools(); print(len(n), sum(t['name'].startswith('grok_agent_') for t in n), sum(t['name'].startswith('grok_delegate') for t in n))"` → `23 15 8`
- Последствие для оператора: чеклист приёмки «21 tool» не сойдётся с живым сервером; два реальных инструмента (`update`, `project`) выглядят как лишние/неожиданные.

### F-2 После Windows-установки README ведёт на POSIX-путь self-test
- Серьёзность: major
- Статус: подтверждено
- Где: README.md:33-39; docs/EASY.md:16-29
- Написано: сразу после `irm … install.ps1 | iex` — «Then: `~/.local/share/grok-mcp/.venv/bin/python -m grok_delegate --self-test`». EASY.md повторяет тот же POSIX-блок для обеих ОС.
- На самом деле: `install.ps1:8` ставит в `$env:LOCALAPPDATA\grok-mcp`, интерпретатор — `.\.venv\Scripts\python.exe`. POSIX `$HOME/.local/share/grok-mcp/.venv/bin/python` на этой машине после Windows-install не существует. Сам инсталлятор в конце печатает правильную команду (`install.ps1:110`), но канонические README/EASY её перебивают.
- Репро: сравнить `install.ps1:8,41-43,110` с README.md:33-39. В temp clone: `Test-Path "$env:LOCALAPPDATA\grok-mcp\.venv\bin\python"` vs `Scripts\python.exe`.
- Последствие для оператора: Windows-оператор после «одной команды» копирует self-test из README, получает «не найден файл» и думает, что установка сломалась.

### F-3 Документированный Windows one-liner не передаёт `-Project` и открывает весь профиль
- Серьёзность: major
- Статус: подтверждено
- Где: README.md:33; docs/EASY.md:16-17; scripts/install.ps1:6-8,45-54,73-78
- Написано: POSIX-пример — `bash -s -- --project "$HOME/code/my-project"`. Windows — `irm … | iex` без `-Project`.
- На самом деле: default `-Project = $env:USERPROFILE`. Воспроизведение логики сниппета дало `GROK_DELEGATE_ALLOWED_ROOTS = C:\Users\codex` и `GROK_DELEGATE_LANES_PARENT = C:\Users\.grok-mcp-lanes` (родитель профиля). `irm | iex` не прокидывает параметры скрипта; комментарий в шапке `install.ps1:4` показывает `-Project`, но copy-paste в README его не содержит. `install.sh` без `--project` тоже падает на `$HOME`, но **задокументированная** POSIX-команда путь задаёт.
- Репро: смоделировать блок `$envJson` из `install.ps1:73-78` с default `$Project = $env:USERPROFILE`.
- Последствие для оператора: «одна команда с GitHub» выдаёт allowlist на весь домашний каталог. POSIX-инструкция так не делает.

### F-4 `install.ps1` пишет MCP-сниппеты с UTF-8 BOM — строгий JSON это не принимает
- Серьёзность: major
- Статус: подтверждено
- Где: scripts/install.ps1:90,102 (`Set-Content -Encoding UTF8`)
- Написано: (подразумевается валидный JSON для merge в Claude/Cursor; docs/EASY.md:32-34 «merge: ~/.config/grok-mcp/mcp/claude_desktop.snippet.json»).
- На самом деле: Windows PowerShell 5.1 `Set-Content -Encoding UTF8` пишет BOM `EF BB BF`. Воспроизведение в `%TEMP%\grokmcp-snippet-sim\claude_desktop.snippet.json`: `json.loads(raw.decode('utf-8'))` → `JSONDecodeError: Unexpected UTF-8 BOM`; `utf-8-sig` проходит; `ConvertFrom-Json` проходит. Node `JSON.parse` BOM не глотает. Сам объект после снятия BOM валиден, `env` заполнен (баг пустого `env` из прошлого цикла **не** вернулся).
- Репро: см. моделирование `Set-Content -Encoding UTF8` + `python -c json.loads`. Скрипт нельзя было запустить целиком: путь сниппетов зашит в `%USERPROFILE%\.config\grok-mcp` (`install.ps1:47`), dry-run нет.
- Последствие для оператора: если хост читает файл сниппета как JSON целиком, конфиг MCP не парсится. «Merge» вручную из редактора, который прячет BOM, может случайно сработать — и тогда баг кажется флейком хоста.

### F-5 Лаунчер `grok-mcp` с установщика на Windows не создаётся
- Серьёзность: major
- Статус: подтверждено
- Где: README.md:192-196; docs/EASY.md:20
- Написано: «`grok-mcp          # launcher from installer`»; EASY.md: «This installs Python if needed, the package, env, **launcher**, and Claude/Cursor snippets.»
- На самом деле: `install.sh:220-232` пишет `$HOME/.local/bin/grok-mcp` (wrapper, который source'ит env). `install.ps1` wrapper не пишет; в сниппете `command` = `…\.venv\Scripts\grok-delegate.exe`. Команды `grok-mcp` после Windows-install на PATH нет.
- Репро: прочитать `install.ps1` целиком — нет `grok-mcp`. Сравнить с `install.sh:223`.
- Последствие для оператора: README «day-to-day: grok-mcp» на Windows — команда не найдена.

### F-6 `docs/ACP-TRANSPORTS.md` снова обещает fail-closed по версии CLI
- Серьёзность: major
- Статус: подтверждено
- Где: docs/ACP-TRANSPORTS.md:103-106 (противоречит тому же файлу :17-22 и AGENTS.md:60)
- Написано: «A different Grok agent version fails version negotiation instead of silently claiming compatibility.»
- На самом деле: `DEFAULT_EXPECTED_AGENT_VERSION = None`; mismatch — warning/`version_mismatch` event, typed-путь не блокируется (`acp.py:37-43,56`; `status.py:80` `mismatch_blocks_typed_path: false`). Это как раз то, что evidence 0.10.0 называет исправленным. Нижний раздел того же файла не обновлён и утверждает обратное.
- Репро: `from grok_delegate.acp import DEFAULT_EXPECTED_AGENT_VERSION, expected_agent_version` → `None`, `None`.
- Последствие для оператора: решит, что CLI 1.0.x «не совместим», и начнёт пиннить/даунгрейдить, хотя контракт — ACP v1 без пина.

### F-7 EASY.md обещает установку Python «если нужно» — Windows-скрипт этого не делает
- Серьёзность: minor
- Статус: подтверждено
- Где: docs/EASY.md:20; scripts/install.ps1:21-23
- Написано: «This installs Python if needed, the package, env, launcher, and Claude/Cursor snippets.» (абзац общий для macOS/Linux/Windows).
- На самом деле: `install.sh` при отсутствии Python 3.10+ ставит `uv` и `uv python install 3.12`. `install.ps1` печатает URL python.org и `exit 1`.
- Репро: `install.ps1:21-23` vs `install.sh:121-129`.
- Последствие для оператора: на чистой Windows «one command» падает с «поставь Python сам», хотя EASY обещал, что скрипт поставит.

### F-8 `install.sh` и `install.ps1` — разный контракт готовности
- Серьёзность: minor
- Статус: подтверждено
- Где: scripts/install.sh vs scripts/install.ps1 (нет построчного близнеца)
- Написано: EASY.md:20 подразумевает один и тот же результат на обеих ОС.
- На самом деле, только в sh: `--lanes/--home/--repo/--yes`, `GROK_MCP_HOME`/`GROK_MCP_REPO_URL`, проверка `grok` на PATH, запуск `--self-test`, exit 2 если CLI нет или self-test красный, wrapper `grok-mcp`, Claude-сниппет с `"env": {}` (это **нормально**: wrapper source'ит env). Только в ps1: env внутри JSON (нужно, wrapper нет). ps1 не вызывает self-test, не проверяет grok, всегда exit 0 после pip. sh `cursor.snippet.json` без ключа `env`; ps1 оба сниппета одинаковые. Codex-сниппета нет ни там, ни там (`docs/CODEX-MCP-SETUP.md` — отдельный ручной путь).
- Репро: diff функций; Windows-сторона подтверждена чтением и моделированием JSON. POSIX-скрипт на этой машине не исполнялся.
- Последствие для оператора: Windows «Installed» ≠ «ready» (нет grok/self-test). На Linux «ready» проверяется. Хост без merge сниппета на Windows ещё и без wrapper остаётся с пустым allowlist, если скопировать только `command` без `env`.

### F-9 Skill указывает несуществующий `scripts/draft_issue.py`
- Серьёзность: minor
- Статус: подтверждено
- Где: skills/grok-mcp/references/feedback.md:6 (зеркала `.cursor/.claude/.codex/.agents` те же)
- Написано: «`python scripts/draft_issue.py --repo zai-one/grok-mcp --title "…" --body-file body.md`»
- На самом деле: файла `scripts/draft_issue.py` в корне нет. Живой скрипт: `skills/grok-mcp/scripts/draft_issue.py`. Плюс `Path.read_text()` без `encoding` (`draft_issue.py:18`) на Windows возьмёт cp1252.
- Репро: `Test-Path scripts/draft_issue.py` → False; `Test-Path skills/grok-mcp/scripts/draft_issue.py` → True.
- Последствие для оператора/агента: шаг «завести issue» из skill падает FileNotFound.

### F-10 Self-test без grok отправляет на несуществующий skill `install-grok-mcp`
- Серьёзность: minor
- Статус: подтверждено
- Где: grok_delegate/__main__.py:214; docs/EASY.md:39-43 (таблица «Grok CLI not found»)
- Написано: в PLAIN ENGLISH: «Agent setup skill: install-grok-mcp». Доки/скилл называются `grok-mcp`.
- На самом деле: skill `install-grok-mcp` в репозитории нет. Сообщение в остальном верное («поставь Grok CLI, `grok login`, тот же OS-user»). На Windows-консоли эл-тире из `skipped — binary missing` печатается как `skipped � binary missing` (cp1252).
- Репро: PATH без grok → `.\.venv\Scripts\python.exe -m grok_delegate --self-test` (temp clone): FAIL binary `not found: grok`, doctor `GROK_MISSING`, exit 1, блок PLAIN ENGLISH с `install-grok-mcp`. С grok на PATH: 11 PASS, RESULT PASS, exit 0.
- Последствие для оператора: ошибка в целом ведёт к `grok login`, но skill-имя из вывода нельзя открыть.

### F-11 ACP-TRANSPORTS всё ещё зовёт live rebaseline «remaining check»
- Серьёзность: minor
- Статус: подтверждено
- Где: docs/ACP-TRANSPORTS.md:28-29
- Написано: «A live ACP rebaseline on the currently installed CLI is a remaining check — do not invent new fixtures.»
- На самом деле: evidence 0.10.0 и CHANGELOG фиксируют четыре живых сценария в `evidence/live-acp/` и 33 теста `test_live_acp_fixtures.py`. В temp clone: 33 collected. Верх файла уже описывает unpin; этот абзац — не обновлённый хвост 0.2.118.
- Репро: `pytest tests/test_live_acp_fixtures.py --collect-only -q` → 33; файлы `evidence/live-acp/session-*.jsonl` на месте.
- Последствие для оператора: будет считать, что live capture ещё не делали, и либо пропустит уже существующие фикстуры, либо снимет лишние.

### F-12 `docs/economy.md` снова говорит, что ECONOMY=1 сажает worker в `low`/12
- Серьёзность: minor
- Статус: подтверждено
- Где: docs/economy.md:33; README.md:162-165
- Написано: «`GROK_DELEGATE_ECONOMY=1` → Default `max_turns=12`, `timeout_seconds=600`, `reasoning_effort=low` when the client omits them». README: «Turning economy on … no longer forces the worker down to `low`.»
- На самом деле: `apply_task_economy_defaults` всё ещё подставляет `low`/12, **если** полей нет. Но job-путь сначала `_apply_project_gate` (`server.py:1195`), и пресеты `cheap/standard/max` всегда кладут effort+turns (`project_config.py:27-31`). Инсталлятор пишет `ECONOMY=1`, поэтому compact poll включён, а бюджет worker'а после opt-in берётся из пресета/`GROK_DELEGATE_REASONING_EFFORT`, не из таблицы economy.md. `timeout_seconds=600` economy по-прежнему дописывает, если клиент молчит.
- Репро: читать порядок `server.py:1195` → `contracts.py:84`.
- Последствие для оператора: при пресете `max` и `ECONOMY=1` (как после install) поверит, что worker сидит на `low`/12, и будет «чинить» несуществующее занижение — или наоборот не выставит явный budget, думая, что ECONOMY его уже задала.

### F-13 Три разных дефолта `lanes_parent`, в доках фигурирует только `.grok-mcp-lanes`
- Серьёзность: minor
- Статус: подтверждено
- Где: README.md:66; scripts/install.ps1:45; scripts/install.sh:48,202; grok_delegate/agent_runtime.py:874-876; grok_delegate/runner.py:414-418; grok_delegate/server.py:538-540; grok_delegate/status.py:421
- Написано: README — `GROK_DELEGATE_LANES_PARENT=/path/to/.grok-mcp-lanes`. Инсталляторы пишут `<parent-of-project>/.grok-mcp-lanes`.
- На самом деле: если env **не** задан: typed execute (`_default_lanes_parent`) → `<repo_parent>/<repo_name>-grok-lanes`; legacy `resolve_lanes_parent` и `status.roots.lanes_parent_by_root` → sibling `pcp-lanes`. Три имени. README показывает только четвёртое (то, что пишет installer).
- Репро: чтение указанных строк. На этой машине после ручной донастройки live-сниппет держит `D:\grok-mcp-lanes` — это уже не дефолт скрипта.
- Последствие для оператора: `grok_agent_status` без env покажет `pcp-lanes`, а execute положит worktree в `{repo}-grok-lanes`. Поиск работы по доке `.grok-mcp-lanes` ничего не найдёт.

### F-14 Дефолтная `test_commands` навигатора — `python -m pytest -q`, не `py -3`
- Серьёзность: minor
- Статус: подтверждено
- Где: grok_delegate/session.py:278; AGENTS.md:80,91; `_ALLOWED_COMMAND` grok_delegate/acp.py:1187-1196
- Написано: AGENTS.md велит гонять тесты этого репо как `py -3 -m pytest tests -q`. Skill execute просит передать `test_commands` на `session_begin`, но не фиксирует Windows-лаунчер.
- На самом деле: если host не передал `test_commands`, execute-карточка получает `python -m pytest -q`. Regex **разрешает** и `python -m pytest`, и `py -3 -m pytest`, и `npm.cmd test`. Запрещает: `python3.12 -m pytest`, `py -3.11 -m pytest`, `pnpm.cmd test` (при том что `pnpm test` и `npm.cmd test` проходят). На Windows 11 `python` часто отсутствует, есть только `py`.
- Репро (temp clone): `_command_allowed('py -3 -m pytest tests -q', cwd)` ALLOW; `'python3.12 -m pytest -q'` DENY; `'pnpm.cmd test'` DENY.
- Последствие для оператора: карточка «запусти тесты» на Windows исполняется дословно, `python` не найден, worker жжёт ходы; `pnpm.cmd` на Windows (реальный бинарь) permission-гейт отвергнет.

## Проверено и совпадает

- Версия моста `0.10.0`: `pyproject.toml`, `guard.py`, badge README, `initialize.serverInfo.version`.
- MCP protocol `2024-11-05`; ACP protocol integer `1`; `runtime_status()["default_transport"]=="stdio"`, `auto_behavior=="stdio-only-no-fallback"` — как в docs/CODEX-MCP-SETUP.md:50-55 (кроме счётчика tools, см. F-1).
- Unpin по умолчанию: `DEFAULT_EXPECTED_AGENT_VERSION is None`; opt-in `GROK_DELEGATE_EXPECTED_AGENT_VERSION`; mismatch не блокирует typed path. AGENTS.md:60 и верх ACP-TRANSPORTS.md:17-22 верны (низ файла — F-6).
- Пресеты `.grok-mcp.json`: `off` / `cheap` (low, 12) / `standard` (high, 24) / `max` (xhigh, 40) — `project_config.py:27-31` = AGENTS.md:38 / README.md:129-133. Модель в пресет не входит.
- `PROJECT_NOT_ENABLED` без `.grok-mcp.json`: сообщение называет файл, путь `config_path`, меню пресетов и что сделать («write one naming a preset to opt in»). Не «падает молча».
- Job-инструменты без allowlist → `ALLOWED_ROOTS_EMPTY` с текстом про `GROK_DELEGATE_ALLOWED_ROOTS` / `REPO_ROOT`.
- `tests_skipped_reason` enum в схеме: `NOT_A_WRITE_ROLE` / `CANCELLED` / `NO_CHANGES` / `NO_TEST_COMMANDS` — как AGENTS.md:30.
- `HARD_CAP_MAX_TURNS=60` = README «1..60».
- `scripts/capture_acp_live.py` существует; `scripts/capture_acp_initialize.py` удалён и **не** торчит в README/AGENTS/docs/skills/.github. `py -3 scripts/sync_skills.py` — путь живой.
- Windows-сниппет больше не несёт пустой `"env": {}`: текущий `install.ps1` кладёт ALLOWED_ROOTS, LANES_PARENT, ECONOMY, ECONOMY_COMPACT_POLL (это как раз фикс из evidence/CHANGELOG 0.9.0 — держится).
- `grok login` / CLI missing: `--self-test` без grok даёт FAIL binary + GROK_MISSING + «install Grok CLI AND grok login» (имя skill неверно — F-10, направление верное). С установленным grok 1.0.4 self-test PASS.
- Runtime-зависимости пакета: `dependencies = []`. Импорты `grok_delegate/**` — stdlib (+ относительные внутри пакета). `jsonschema`/`pytest` только в extra `test`; `pip install -e ".[test]"` их поставил. Скрытого «случайно установленного» runtime-пакета не видно.
- Команды `py -3 -m pytest tests -q` и `python -m grok_delegate --self-test` из корня temp clone работают (когда grok на PATH). `grok --version` → 1.0.4.
- Ссылки `docs/EASY.md`, `docs/install/*`, `docs/economy.md`, `docs/SKILLS.md`, `docs/ACP-TRANSPORTS.md`, `SECURITY.md`, `examples/`, `examples/vps.systemd.service`, `examples/http.env.example`, `examples/fastmcp_proxy.py`, `schemas/grok-work-receipt.v1.schema.json`, `.github/ISSUE_TEMPLATE/` — файлы на месте.
- `CLAUDE.md` только импортирует AGENTS.md — утверждений, которые можно опровергнуть, нет.
- Skill `grok-mcp` v1.1.0 / README «v1.1»: цикл `session_begin` → `session_next` → `session_end` совпадает со схемами. Poll `{job_id}` only, execute несёт `task` — как AGENTS.md.
- `GROK_DELEGATE_TRUST_HOST_ROOTS` / `CLAUDE_PROJECT_DIR`: код делает то, что пишет README (opt-in, widens, exact equality; Windows `paths_equal` case-insensitive).
- Production I/O в `grok_delegate` (кроме `updater.py`, см. ограничения) читает/пишет с `encoding="utf-8"`; git/subprocess в runner/gates/acp/agent_runtime — тоже utf-8. Регрессия cp1252 в основном пути не найдена.
- Тесты live ACP: 33, как в evidence. Skip symlink-escape на этой Windows — как evidence §7.

## Ограничения

- Нет Linux/macOS: `install.sh` разобран чтением, не исполнялся. Паритет POSIX подтверждён только статически (F-8).
- Боевая установка `C:\Users\codex\AppData\Local\grok-mcp` не изменялась (LastWriteTime до и после: 17/8 2:48:35 AM). Её `.venv` и конфиги хостов не писались. Пользовательский `%USERPROFILE%\.config\grok-mcp` читался только для справки (сниппеты от 7/8, env уже заполнен — не артефакт этого прогона).
- `install.ps1` целиком не запускался: он клонирует в `-HomeDir` (можно увести в TEMP) **и** безусловно пишет сниппеты в `%USERPROFILE%\.config\grok-mcp`. JSON смоделирован копией той же логики в TEMP.
- Полный pytest в temp clone: 604 passed / 1 failed / 1 skipped. Evidence/CHANGELOG: 605/1. Упавший тест изолированно 3/3 зелёный — флейк `test_managed_websocket_stderr_is_cumulatively_bounded`, не расследовался глубоко (чужой срез). Число «605» в CHANGELOG — снимок зелёного дерева, не инвариант одного прогона.
- Параллельные агенты работают в том же дереве. Git-мутаций не было. Чужой `Service/Audits/2026-08-17-host-contract.md` не трогался.
- Вне среза (не копалось): permission-гейт как безопасность, правдивость receipt, гонки, схемы карточек навигатора, мутационные тесты.
- `.gitattributes` в репозитории нет, `core.autocrlf=true`. Поломки из-за CRLF в этом прогоне не воспроизведены; `updater.py:32-38` гоняет git с `text=True` без `encoding="utf-8"` — на не-ASCII имени ветки это теоретический риск Windows, без репро здесь не заводился как отдельная находка.
- Недокументированные, но читаемые кодом env (не находки, а хвост): `GROK_DELEGATE_CONCURRENCY`, `GROK_DELEGATE_MAX_QUEUED`, `GROK_DELEGATE_GIT_TIMEOUT_SECONDS`, `GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS`, `GROK_DELEGATE_LOG_LEVEL`, `GROK_DELEGATE_QUEUE`, `GROK_DELEGATE_VERDICTS_DIR`. В README/docs их нет; поведение при отсутствии — дефолты в коде.
- Переводные `docs/install/ru.md` / `es.md` / `zh-CN.md` содержат только `curl | bash` и отсылают к EASY.md; отдельной Windows-команды там нет. Это указатель, не ложный путь, если оператор откроет EASY (где Windows есть, но см. F-2/F-3).
