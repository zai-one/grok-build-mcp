# Аудит конкурентности grok-mcp 0.10.0 (2026-08-17)

Срез: потоки, процессы, таймауты, отмена, файловые блокировки, состояние между запусками. Не трогал permission-гейт, содержимое receipt, схемы MCP, качество тестов как отдельную тему.

- Репозиторий: `D:\ZAI\MCP\Grok CLI` (`zai-one/grok-mcp`), ветка `main`
- Baseline SHA: `e79e0bf1f173c19173ab855248f170757f63b9dc`
- Версия: `SERVER_VERSION = "0.10.0"` (`grok_delegate/guard.py:22`)
- Читал: `AGENTS.md`, `Service/Handoffs/grok-mcp-production-ready-evidence.md` (закрытое не повторяю), `grok_delegate/acp.py`, `agent_runtime.py`, `jobs.py`, `jobs_store.py`, `runner.py` (`prepare_worktree`), `server.py` (`configure_durable_jobs`, `shutdown_runtime`), связанные тесты в `tests/test_round8_bridge.py`, `tests/test_jobs_durable.py`
- Pytest: 8 последовательных полных прогонов `py -3 -m pytest tests -q --tb=line` из корня репозитория. Выводы: `%TEMP%\grokmcp-conc\pytest-run-N.txt`
- Targeted-репро только в `%TEMP%\grokmcp-conc\` (python-sleepers, не живой Grok CLI). После прогонов своих `grok.exe` / sleep-потомков не осталось.

Уже закрыто в evidence 2026-08-17 и здесь не находка: независимый grace-deadline отмены (флейк теста починен), `server_pid` vs `pid` для STALE_RUNNING, persist running→terminal под тем же lock, reconnect без повтора prompt (`ACP_RETRY_REQUIRED`), verifier после `max_turns`.

## Находки

### F-1 `timeout_seconds` не общий дедлайн job: после ACP lane ещё занята verifier/commit

- Серьёзность: major
- Статус: подтверждено кодом (не гонка)
- Где: `grok_delegate/agent_runtime.py:328-329`, `:251` через `acp.py:251`, `agent_runtime.py:478-516`, `:637-655`; `acp.py:65`, `:1474-1491`
- Последовательность: write-job стартует → `prepare_worktree` может жечь до `min(timeout_seconds, 600)` на checkout и `min(timeout_seconds, 60)` на git-пробы → ACP-дедлайн заново `now + timeout_seconds` → по `ACP_TIMEOUT` `cancel_event` **не** ставится → `_verify_or_explain` гоняет каждую `test_commands` ещё `min(timeout_seconds, 600)` с → `commit_lane_work` ещё до 60 с. `LANE_BUSY` держится, пока это не закончится.
- Частота: не флейк; так устроены бюджеты. 0 из 8 pytest.
- Репро: чтение указанных строк. Число: при дефолте `timeout_seconds=1800` (`contracts.py:138`) wall-clock до ≈ checkout 600 + агент 1800 + N×600 + grace 5 + `_graceful_stop` до ≈11 с. Две тестовые команды → около 3600 с при «таймауте 1800».
- Последствие для оператора: единственная полоса занята дольше, чем он задал; poll показывает `running` / `phase=collect` после того, как агент уже сорвался по `ACP_TIMEOUT`. Отмена оператора по-прежнему снимает verifier (`agent_runtime.py:628-629`) — таймаут агента этим путём не идёт. Это тот же «verifier независимо от stop reason», что сознательно сделали для `max_turns`; для таймаута побочный эффект — умножение wall-clock и удержание lane.

### F-2 `_graceful_stop` не вызывает `taskkill /T`, если лидер уже умер; `worker_alive_after_shutdown` смотрит только на лидера

- Серьёзность: minor
- Статус: подтверждено репро на синтетическом дереве; для штатного порядка моста (Job Object сразу после `Popen`, дети после assign) утечки не было
- Где: `grok_delegate/acp.py:1474-1493` (`wait` 3 с → `terminate` → `wait` 3 с → `taskkill /T` только если второй `wait` тоже истёк) → `process_job.close()`; `acp.py:211`, `:559` (`_WindowsKillJob` после spawn); `acp.py:471`, `:629` (`proc.poll() is None`)
- Последовательность: внук рождается **до** `AssignProcessToJobObject` → `terminate()` убивает лидера → `wait` успевает → `taskkill /T` пропускается → Job Object не содержит внука → внук `STILL_ACTIVE`. Receipt при этом может быть `worker_alive_after_shutdown: false`.
- Частота: 2/2 синтетических прогона с детьми до assign (`%TEMP%\grokmcp-conc\probes-tree.json`). 0/1 если внук рождается после assign без `CREATE_BREAKAWAY_FROM_JOB` (`probes-tree-after-assign.json`: `grandchild_leaked: false`).
- Репро:
  ```
  py -3 %TEMP%\grokmcp-conc\conc_tree.py
  py -3 %TEMP%\grokmcp-conc\conc_tree_after_assign.py
  ```
- Последствие для оператора: сироты CPU/порты/локи, если Grok успеет породить процесс до вступления в Job Object или выйдет из job через breakaway. На этой машине родитель уже в Job Object, `AssignProcessToJobObject` всё равно удался; `KILL_ON_JOB_CLOSE` при `os._exit` родителя убивает assigned-ребёнка (`probes.json` `parent_kill_with_job.child_alive_after_parent_exit: false`). Без Job Object ребёнок живёт (`parent_kill_without_job: true`). Тесты моста проверяют только лидера (`test_fake_stdio_cancel_returns_cancelled_and_process_dies` и аналоги).

### Backlog (не в топ)

- `_windows_pid_alive` (`jobs_store.py:120-135`) не проверяет `STILL_ACTIVE` (259): `OpenProcess` на PID с живым handle к уже вышедшему процессу даст «жив». Для текущих записей с `server_pid != os.getpid()` stale и так срабатывает (`jobs_store.py:186-187`). Цена ошибки, если чинить зря: ложный STALE_RUNNING у ещё живого сервера.
- `start_job` кладёт `STATE_RUNNING` в реестр до `thread_starter` (`jobs.py:249-289`). Если `submit` бросит, `start_agent_job` снимает `_CANCEL_EVENTS` и admission, но **не** терминализует запись → `LANE_BUSY` + `JOB_NOT_OWNED` до рестарта. Не воспроизводил (нужен мёртвый executor).
- Межпроцессового lane-lock нет: `_START_LOCK` / `list_jobs` — память одного процесса. Два живых MCP на одну `GROK_DELEGATE_JOBS_DIR` могут поднять две полосы. На этой машине видно несколько `grok-delegate.exe`; столкновение job не ставил.

## Проверено и оказалось в порядке

| # | Вопрос | Вывод | Доказательство |
|---|---|---|---|
| 1 | Живые процессы после cancel/timeout/падения/убийства родителя | Штатный путь (stdio и managed WS): лидер убивается, `worker_alive_after_shutdown` false. Убийство родителя с успешным Job Object убивает assigned-ребёнка. Stdio: `CREATE_NEW_PROCESS_GROUP` + Job Object `KILL_ON_JOB_CLOSE` + fallback `taskkill /T`. WS-демон: тот же `_graceful_stop` в `finally`. Остаток — F-2. | `acp.py:187-211,468-471,533-627,1474-1578`; тесты `tests/test_round8_bridge.py:230-283,449-473,941-956`; `probes.json` parent_kill_*; fake stdio/WS cancel |
| 2 | Cancel между start и регистрацией `_CANCEL_EVENTS` | Не теряется на нормальном пути: event пишется **до** `jobs.start_job`, job_id клиенту отдаётся после submit. `gated_callback` + `future.cancel()` закрывает queued. Окно «event есть, записи job ещё нет» даёт `JOB_UNKNOWN` только если кто-то угадает hash id до return. | `agent_runtime.py:148-193,196-246,152-162`; `test_queued_job_cancel_is_immediately_terminal`; `test_shutdown_terminalizes_queued_future`; probe cancel running → `cancel_requested: true` |
| 3 | LANE_BUSY после смерти сервера | Не залипает навсегда. `configure_durable_jobs` делает `rehydrate_jobs`; чужой/мёртвый `server_pid` → `unknown`/`STALE_RUNNING`, а `LANE_BUSY` смотрит только `state == running`. Без `GROK_DELEGATE_JOBS_DIR` память пустая — тоже не busy. Точное совпадение PID новой инкарнации с записанным `server_pid` оставит `running` до следующего рестарта с другим PID (предположение, редко). | `agent_runtime.py:136-142`; `server.py:1794-1809`; `jobs_store.py:138-212,186-187`; `tests/test_jobs_durable.py:328-381`; `%TEMP%\grokmcp-conc\probes.json` `lane_busy_rehydrate`; `%TEMP%\grokmcp-conc\probes-lane.json` (`LANE_BUSY` на вторую корреляцию той же lane) |
| 4 | Два `start_agent_job` с одним `job_id` | В одном процессе: `_START_LOCK`, второй — `idempotent_replay: true`, один worker. Файл стора: in-process `_persist` под `jobs._LOCK` (кроме стартового persist до старта потока). Два процесса — last `os.replace` wins, JSON не рвётся; это backlog. | `agent_runtime.py:125-135`; `test_concurrent_idempotent_start_reserves_exactly_one_worker`; `jobs.py:151-164,249-268`; probe `same.idempotent_replay: true` |
| 5 | Читатель stdout/stderr vs очередь | Очередь `maxsize=32`, при `Full` backpressure `put(timeout=0.1)`, не `ACP_OUTPUT_LIMIT`. Бюджет байт до постановки в очередь. Flood > cap → `ACP_OUTPUT_LIMIT` за 3.7 с при `timeout_seconds=2`, worker мёртв, дедлока `send()` не поймал. WS stderr: `_bounded_tail_reader` + overflow event. | `acp.py:214-238,1401-1471`; `test_stdio_reader_applies_backpressure_for_bounded_bursts`; `test_stdio_reader_enforces_aggregate_budget_before_queueing`; `test_owned_process_caps_many_chunk_output`; `test_timeout_and_oversized_output_fail_closed`; `probes.json` `stdio_flood_timeout` |
| 6 | Таймауты друг друга | `CANCEL_GRACE_SECONDS=5` проверяется раньше ACP-дедлайна (`acp.py:289-296`) — при далёком timeout grace независим (тест `test_cancel_has_independent_grace_deadline`). Если cancel близко к дедлайну, дедлайн может оборвать grace и отдать `ACP_TIMEOUT`. Git-бюджеты — отдельные, capped. Сложение бюджетов — F-1, не «один отменяет другой». | `acp.py:65,284-306,1474-1491`; `agent_runtime.py:328-329,655`; `runner.py:73-83,103-119`; `test_cancel_has_independent_grace_deadline` |
| 7 | WS reconnect одноразовый | `reconnect_used` ставится до `reconnect_factory`. Повторный drop → `ACP_DISCONNECTED`, replacement закрыт, managed-демон всё равно `_graceful_stop` в `run().finally`. Prompt не реплеится. | `acp.py:660,709-741,623-627`; `test_websocket_disconnect_reconnects_and_loads_without_replaying_prompt`; probe `second_ws_reconnect`: `ACP_DISCONNECTED`, `replacement_closed: true` |
| 8 | Файловый стор | Атомарный temp+`os.replace`+fsync; mid-write `.*.tmp` не грузятся. Повреждённый JSON пропускается. Сверх `MAX_JOB_FILE_BYTES` (1e6) `_serialize_record` режет, иначе `ValueError` → `save_job` False, файла нет (не частичный JSON). Два потока одного процесса сериализует `jobs._LOCK` в `update_job`/`_finish`. | `jobs_store.py:42,215-264,294-299,375-427,430-436`; `jobs.py:151-164,254-268`; `test_atomic_write_no_half_written_file_observed`; `test_durable_record_is_versioned_redacted_and_below_reload_cap`; `test_corrupt_truncated_json_skipped_with_warning`; probe `store_cap_raise.save_ok: false`, `json_files: []` |
| 9 | Рост `_CANCEL_EVENTS` / `_FUTURES` / `_JOB_META` | Словарь только живых job: pop в `work().finally`, на ошибке start, на queued-cancel. Admission `_CONCURRENCY+_MAX_QUEUED` (дефолт 1+8). `jobs._JOBS` capped `MAX_JOBS=64`, running не выселяются — при живом worker это ≤ admission. `shutdown_runtime` сигналит cancel. | `agent_runtime.py:31-39,118-123,171-176,228-232,924-934`; `jobs.py:42,121-134`; queue/shutdown тесты round8 |
| 10 | Pytest 8× подряд | Все зелёные, флейков нет. 1 skipped стабильно (symlink-escape, как в evidence). | таблица ниже |

## Pytest

Команда (все прогоны, последовательно, cwd `D:\ZAI\MCP\Grok CLI`):

```
py -3 -m pytest tests -q --tb=line
```

| N | exit code | длительность (pytest) | упавшие |
|---|---|---|---|
| 1 | 0 | 67.97 с | все зелёные (605 passed, 1 skipped, 79 subtests) |
| 2 | 0 | 72.97 с | все зелёные |
| 3 | 0 | 62.89 с | все зелёные |
| 4 | 0 | 66.25 с | все зелёные |
| 5 | 0 | 78.23 с | все зелёные |
| 6 | 0 | 65.11 с | все зелёные |
| 7 | 0 | 62.37 с | все зелёные |
| 8 | 0 | 62.59 с | все зелёные |

Сырые логи: `C:\Users\codex\AppData\Local\Temp\grokmcp-conc\pytest-run-1.txt` … `pytest-run-8.txt`. Targeted: `probes.json`, `probes-tree.json`, `probes-tree-after-assign.json`, `probes-lane.json`, `probes-breakaway-after-assign.json`.
