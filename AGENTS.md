# AGENTS.md — grok-delegate / unofficial Grok MCP

Неофициальный мост. Не xAI / Grok. Цель: **Grok = дешёвый worker**, хост (Claude / Cursor / Codex) = **оркестратор и verifier**.

Текущий цикл: unpin CLI; валидные карточки `session_next`; bounded evidence pack; правила агентов; multi-version без пина.

## Язык

Оператор проекта пишет по-русски — **отвечать ему по-русски**, включая объяснения, отчёты, вопросы и summary. Это правило, а не догадка по языку последнего сообщения: одна английская фраза от оператора его не отменяет.

Русский — язык общения с оператором и внутрипроектной документации (`AGENTS.md`, `Service/Handoffs/*`). Артефакты, которые читает не только оператор, остаются английскими:

| Английский | Русский |
|---|---|
| commit messages, ветки, теги | ответы оператору в чате |
| код, идентификаторы, docstrings, комментарии | `AGENTS.md`, handoff-и, evidence |
| `README.md`, `CHANGELOG`, GitHub Issue / PR | операторские runbook-и и объяснения |

Смешивать в одном артефакте не надо: файл либо русский, либо английский целиком.

## Typed tools vs navigator

| Путь | Когда |
|---|---|
| Navigator: `session_begin` → loop `session_next` → `session_end` | Дешёвый цикл. Карточка `grok_agent_execute` = полный `task`; `grok_agent_poll` = только `{job_id}`. |
| Typed: consult → execute → poll → review | Fallback, если карточка отвергнута / schema error / хост без navigator. |

Хост не скармливает весь репозиторий в контекст. Читает compact receipt: `changed_files`, `diffstat`, bounded `unified_diff`, `tests`, `worktree_path`.

В `tests` попадает только прогон, который сделал сам мост (`source: "bridge-verifier"`). То, что о своих тестах сказал агент, помечено `agent-reported` и доказательством не считается: живой capture показал `exit_code: 0` при упавшем pytest, потому что в цепочке `a; b` код возврата принадлежит последней команде. Verifier запускается независимо от того, доработал агент свой ход или упёрся в `max_turns`. Если `tests` пуст — `tests_skipped_reason` говорит почему (`NO_CHANGES` / `NO_TEST_COMMANDS` / `CANCELLED` / `NOT_A_WRITE_ROLE`), и пустой список больше не путается с «тесты не прогонялись».

`changed_files` и `unified_diff` — **разные срезы** и 1:1 не совпадают: первый отфильтрован «что изменилось за этот запуск», второй берётся от base. Это описано в `schemas/grok-work-receipt.v1.schema.json`.

Гейт приёмки судит и то, что лежит в lane, а не только то, что сделал этот запуск. Пустой `changed_files` больше не коротит проверку: job, который ничего не изменил, но сидит на чужом файле, отдаёт `blocked` / `UNEXPECTED_CHANGED_FILES`, а не `no_changes`. `base_ref` резолвится в **основном** репозитории, а не в worktree: на переиспользованной lane `HEAD` — это коммит прошлого job'а, и всё, что он оставил, исчезало из следующего diff.

Артефакт, который написал **verifier**, работой worker'а не считается: дерево снимается и до тестов тоже, и `verifier_touched_files` в receipt называет, что появилось между. Пересечение с `expected_artifacts` → `ARTIFACT_WRITTEN_BY_VERIFIER`. Иначе тестовый набор сертифицировал бы сам себя.

Тестовые команды worker должен запускать **дословно**: permission-гейт сверяет строку точно, и `python -m pytest -q; echo EXIT_CODE=$LASTEXITCODE` будет отклонён. `build_prompt` говорит об этом worker'у.

## Проект должен сам включить мост

Без `.grok-mcp.json` в корне проекта job-инструменты **отказывают** (`PROJECT_NOT_ENABLED`) — это не баг, а opt-in. Пресеты: `off` / `cheap` (low, 12) / `standard` (high, 24) / `max` (xhigh, 40). Читать и писать — `grok_agent_project`; пишет только внутрь allowlisted root.

Пресеты не задают модель — намеренно, иначе проект запиннится на устаревшую. Явные поля в конфиге бьют пресет, поле в task бьёт оба. Битый конфиг = ошибка, а не «выключено».

## Экономика и worktree

- Lane лежит **внутри проекта**: `<project>/.grok/lanes/<slug>`. Это незамерженная работа, которую человек ревьюит, — она живёт рядом с проектом, а не в соседнем каталоге. Точка спереди — не косметика: pytest пропускает `.*`, ripgrep и индексаторы пропускают скрытое, а `.grok/` мост один раз дописывает в `.gitignore` проекта. Путь в **видимом** дереве по-прежнему отвергается. `GROK_DELEGATE_LANES_PARENT` перебивает дефолт.
- Один job за раз. Бюджет worker'а — решение оператора, не агента: `GROK_DELEGATE_REASONING_EFFORT` и `GROK_DELEGATE_MAX_TURNS`. Не занижать их «ради экономии» — экономия хоста и бюджет worker'а это разные вещи.
- Worktree / ветка только `grok/*`. **Никогда** `git push` / `merge` из моста — человек ревьюит и мержит.

## Кто что коммитит

| Роль | Что делает | Куда коммитит |
|---|---|---|
| Grok (worker) | пишет код и тесты по ТЗ | **только** своя lane-ветка `grok/*` |
| Claude / хост (оркестратор) | верификация, финальные правки, приёмка | `main` |

Grok не трогает `main`, не мержит, не пушит. Хост не принимает работу по `summary` агента — только по прогону тестов у себя.

**Коммит в lane делает мост, а не worker.** Просить коммит в ТЗ больше не нужно: после того как job закончился (в том числе по `ACP_STOP_cancelled` из-за `max_turns`), мост сам коммитит незакоммиченное в `grok/*`. Инструкция, которая работает только пока у worker'а остались ходы, — это не механизм. Коммит идёт **после** verifier'а: иначе тест, который откатил артефакт, всё равно выглядел бы как сделанная работа. Ветка не `grok/*` → мост отказывается коммитить.

`base_ref` фиксируется в SHA **до** запуска worker'а. Дефолт — `HEAD`, а `HEAD` уезжает на первом же коммите в lane, и тогда любой последующий diff «относительно базы» схлопывается в пустоту, а законченный job репортится как `no_changes`.
- Секреты не в receipts, не в MCP config, не в issue. Auth = локальный `grok login`. Редактор знает не только `xai-`/`sk-`, но и GitHub/GitLab/AWS/Slack/Google/JWT/npm и пароль внутри URL; маркер, разрезанный переводом строки, склеивается до редакции.
- HTTP-транспорт требует `GROK_DELEGATE_HTTP_TOKEN` **везде**, включая loopback: loopback делят все процессы машины и браузер, а за этим эндпоинтом job-инструменты. Без токена сервер не поднимется; открыт только `/healthz`. Это **частный Bearer JSON-RPC**, не Streamable HTTP из спецификации MCP. Один процесс — один клиент; второй `initialize` получает `ONE_CLIENT_PER_PROCESS`. Bind по умолчанию только loopback. Не-loopback bind требует `GROK_DELEGATE_HTTP_ALLOW_NONLOOPBACK=1`; TLS в процессе нет — его снимает обратный прокси.
- Тестовая команда проверяется вторым контуром, а не только точным совпадением со списком: argv[0] не должен лежать **внутри** worktree (worker может написать себе `python.exe`), остальные пути — наоборот, только внутри. `.env`, `id_rsa`, `*.pem` запрещены в любой форме, включая `git show HEAD:.env`.
- Pin версии CLI **выключен**. Любая установленная CLI с ACP v1. Opt-in: `GROK_DELEGATE_EXPECTED_AGENT_VERSION`. Mismatch = warning, typed-путь не блокируется.
- Модель тоже **не пинится**. Пусто = дефолт CLI (сейчас `grok-4.6`), `--model` в argv не идёт. Задать: `GROK_DELEGATE_MODEL`. Не хардкодить id модели в коде — устареет так же, как устарел `grok-4.5`.

## Как вызывать

1. `grok_agent_status` один раз за сессию (версия моста, CLI, unpin, `update_hint`).
2. `grok_agent_session_begin` (`goal`, `host_budget=small`; лучше сразу `project_root`, `expected_artifacts`, `test_commands`).
3. Loop `grok_agent_session_next` — исполнять только `card`.
4. Если execute/poll карточка ломается → typed tools с тем же пакетом.
5. `session_end`. Человек мержит `grok/*`.

Обновление: `grok_agent_status` сам сравнивает чекаут с origin и кладёт блок `update`. Если `update.available` — `grok_agent_update` без аргументов покажет план, с `confirm=true` сделает pull + reinstall и попросит **перезапустить MCP** (сам себя сервер перезапустить не может). Обновление отказывает на грязном чекауте. Фонового updater по-прежнему нет: проверка автоматическая, применение — только по подтверждению. Контракт = ACP v1, не номер Grok CLI.

## Когда ломается

| Симптом | Что делать |
|---|---|
| CLI missing | Поставить Grok CLI, `grok login`, снова `grok_agent_status`. |
| auth absent | Тот же OS-user: `grok login`. Мост не читает `auth.json`. |
| ACP handshake / timeout | `grok_delegate_doctor` (без `fix`). Stdio по умолчанию. Не пиннить CLI. Live capture на текущей CLI — `scripts/capture_acp_live.py --scenario {permission-cancel,consult,command}`, затем `py -3 -m pytest tests/test_live_acp_fixtures.py -q`. |
| tests red | Не пушить. Чинить, `py -3 -m pytest tests -q`. |
| `session_next` schema / `additionalProperties` | Execute должен нести `task` (`objective`, `project_root`, `correlation_id`, `expected_artifacts`, `test_commands`). Poll — только `job_id`, **без** `session_id`. Fallback: typed tools. |
| Cursor vs Claude vs Codex | Один skill `grok-mcp` в `.cursor`, `.claude`, `.codex`, `.agents`. Источник: `skills/grok-mcp`. После правки: `py -3 scripts/sync_skills.py`. |

## Как коммитить

- Только по просьбе оператора **или** когда этот репозиторий явно разрешил commit+push (этот цикл — да, если тесты зелёные).
- Стиль: `fix(session): …` / `feat(receipt): …` / `docs: …` — зачем, не что. 1–2 предложения.
- Не коммитить `.env`, cookies, tokens, raw ACP secrets, `GROK_AGENT_SECRET`.
- Не `git config`. Не `--no-verify`. Не force-push. Не pin Grok CLI.
- Windows: `git commit -m "msg"` (без bash HEREDOC).
- Тесты: `py -3 -m pytest tests -q`. Красное дерево не пушить.

## GitHub Issue (чтобы следующий агент мог сделать)

Поля: symptom; host (Cursor/Claude/Codex); grok CLI `grok --version`; bridge `SERVER_VERSION` / `grok_agent_status`; excerpt `grok_delegate_doctor` (без секретов); expected vs actual; redacted fixture/logs; repro. Шаблоны: `.github/ISSUE_TEMPLATE/`. Не создавать issue с секретами.

## Релиз-тег

Зелёный набор → bump `pyproject.toml` + `grok_delegate/guard.py` `SERVER_VERSION` + CHANGELOG → `git tag vX.Y.Z` → `git push origin vX.Y.Z` → `gh release create vX.Y.Z`. Не тегировать красное дерево. Semver: ломающий MCP schema = minor/major + запись в changelog.

Контракт совместимости: ACP protocol integer `1`. Мост поддерживает несколько CLI тем, что **не пинит** `agentVersion`.
