# Grok bridge contracts

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

## Экономика и worktree

- Lane лежит **внутри проекта**: `<project>/.grok/lanes/<slug>`. Это незамерженная работа, которую человек ревьюит, — она живёт рядом с проектом, а не в соседнем каталоге. Точка спереди — не косметика: pytest пропускает `.*`, ripgrep и индексаторы пропускают скрытое, а `.grok/` мост один раз дописывает в `.gitignore` проекта. Путь в **видимом** дереве по-прежнему отвергается. `GROK_DELEGATE_LANES_PARENT` перебивает дефолт.
- **Запись job'а переживает рестарт сервера.** Реестр держали только в памяти, и `grok_agent_poll` после перезапуска отвечал `JOB_UNKNOWN` на законченную работу: ответ воркера лежал на диске, код — в ветке lane, а оркестратор наверху засчитывал провал и переделывал. Персистентность была написана ещё в R7-D, но включалась переменной `GROK_DELEGATE_JOBS_DIR`, которую никто не ставит. Теперь дефолт — пользовательский state-каталог (`%LOCALAPPDATA%\grok-delegate\jobs`, иначе `$XDG_STATE_HOME/grok-delegate/jobs`); каталог **не** внутри проекта, потому что один процесс обслуживает все проекты хоста, а rehydrate идёт до того, как известен хоть один корень. Выключить: `GROK_DELEGATE_JOBS_DIR=off`.
- **Один job за раз — дефолт, а не потолок.** `GROK_DELEGATE_CONCURRENCY` держали зажатым в 2 с первого коммита, без комментария и без замера, так что запрошенные восемь воркеров превращались в двух, а пачка job'ов — в очередь за одним, с `QUEUE_FULL` на десятом. Потолок теперь 8, а дефолт — по размеру машины (половина ядер, максимум 4). Прежний дефолт «1» был обоснован тем, что lane это незамерженная работа, — но `consult` и `review` worktree **не создают вовсе**, а именно они и составляют большую часть вызовов; серийность для них стоила оператору шести заданий за сорок минут при одном исполненном. Вернуть строгую последовательность: `GROK_DELEGATE_CONCURRENCY=1`. Замерено: четыре read-only job'а при 4 укладываются в 15 с, где три при 1 заняли 13.7 / 30.8 / 95.0 с.
- **Длинная работа — нормальная работа.** Мост асинхронный: dispatch отдаёт `job_id`, `grok_agent_poll` показывает `phase`, `last_step`, `elapsed_s` и умеет **ждать** (`wait_seconds`, до 1800 с), присылая `notifications/progress`. Потолок `timeout_seconds` был 3600 и никакой настройкой не поднимался — теперь 24 часа, дефолт по-прежнему 1800. Карточка навигатора ждать не просила, поэтому хост опрашивал раз и уходил: включается `GROK_DELEGATE_POLL_WAIT_SECONDS`, по умолчанию 0, потому что блокирующий вызов безопасен только если таймаут запроса у хоста больше блокировки.
- **`ACP_STOP_cancelled` — три разные вещи под одним именем**: отмена оператором, исчерпание ходов и ход, который CLI оборвал после отказа гейта. В квитанции есть `stop_detail`, он называет причину: `permission_refused`, `test_command_<reason>`, `turns_exhausted: used/limit` — или `null`, когда объяснения действительно нет. Бюджет worker'а — решение оператора, не агента: `GROK_DELEGATE_REASONING_EFFORT` и `GROK_DELEGATE_MAX_TURNS`. Не занижать их «ради экономии» — экономия хоста и бюджет worker'а это разные вещи.
- Worktree / ветка только `grok/*`. **Никогда** `git push` / `merge` из моста — человек ревьюит и мержит.
- Lane, которая ничего не произвела, **не переживает свой job**: если ветка осталась на закреплённой базе, а дерево чистое, мост убирает и worktree, и ветку — после receipt, никогда до него, потому что приёмка читается с того дерева, которое оставил verifier. Всё остальное остаётся и называет причину: `lane_retained_reason` = `WORK_PRESENT` (receipt показывает работу), `UNCOMMITTED_CHANGES`, `LANE_HAS_COMMITS`, `CLEANUP_DISABLED` или `NOT_A_LINKED_WORKTREE`. Выключить: `GROK_DELEGATE_LANE_CLEANUP=0`. Живые lane перечисляет `grok_agent_status` — незамерженная работа должна быть видна в начале сессии, а не находиться случайно через месяц.

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

## GitHub Issue (чтобы следующий агент мог сделать)

Поля: symptom; host (Cursor/Claude/Codex); grok CLI `grok --version`; bridge `SERVER_VERSION` / `grok_agent_status`; excerpt `grok_delegate_doctor` (без секретов); expected vs actual; redacted fixture/logs; repro. Шаблоны: `.github/ISSUE_TEMPLATE/`. Не создавать issue с секретами.
