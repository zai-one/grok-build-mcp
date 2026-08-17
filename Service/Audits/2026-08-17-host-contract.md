# Аудит хост-контракта grok-mcp

- Дата: 2026-08-17
- Срез: host-contract
- Режим: read-only (код не менялся, коммитов нет)
- Версия моста: 0.10.0 (`pyproject.toml`, `grok_delegate/guard.py:22` `SERVER_VERSION`)
- Ветка: `main`
- SHA HEAD: `e79e0bf1f173c19173ab855248f170757f63b9dc` (`v0.10.0-2-ge79e0bf`)
- Репозиторий: `D:\ZAI\MCP\Grok CLI` (zai-one/grok-mcp)

Закрытые в `Service/Handoffs/grok-mcp-production-ready-evidence.md` дефекты (auto/`tests/` как verify, `curl | bash` на Windows, verifier/`tests_skipped_reason`, автокоммит lane, `base_ref` в SHA) **не переоткрывались**.

Прогон симуляции и замер: `C:\Users\codex\AppData\Local\Temp\grokmcp-host\audit_host_contract.py` → `navigator_traces.json`, `token_measure.json`. Хост моделировался как исполнитель **только `card`**, без догадок.

---

## Замер экономии (токены)

Метод: `tiktoken` encoding `cl100k_base` (поставлен в user-site на время замера). Дополнительно — символы, не как единственная метрика.

A = JSON ответа `grok_agent_poll` = `{ok: true, **compact_job_record(envelope)}` — то, что хост реально читает после poll.
B = полный envelope job record, как `jobs.get_job` (вложенный `result`, 20 ACP-events, `stdout_preview`).
C = `git show <SHA>` того же набора файлов (unified diff).

Синтез envelope реалистичного write-job (completed, bridge-verifier tests, events) вокруг **реальных** diff этого репозитория. Diff не выдумывался.

| Сценарий | Компактный receipt | Полный receipt | git diff | Вывод |
|---|---:|---:|---:|---|
| Маленькая: `e79e0bf` README аудитов, 1 файл, +12 | 728 | 3477 | 208 | **не экономит vs diff** (×3.5 дороже); vs полный record экономит |
| Средняя: `9ea40f4` session.py + тест, 2 файла, +174/−15 | 3509 | 9039 | 2653 | **не экономит vs diff** (×1.32 дороже); vs полный record экономит |
| Крупная: `2332359` live-ACP WS, 7 файлов, +440/−21 | 5400 | 44245 | 19062 | **экономит vs оба** (diff обрезан потолком 16 KiB) |

Порог: `economy.py:20` `ECONOMY_MAX_UNIFIED_DIFF = 16_384` байт. `compact_job_record` **всегда кладёт `unified_diff`** в то, что читает хост (`economy.py:125,170-171`). Пока diff короче потолка, compact ≈ токены diff + ~500 токенов обёртки (`compact_no_diff`: 487 / 511 / 712). Экономия относительно `git diff` появляется **только после ~16 KiB unified diff**. Относительно полного job record (events/stdout/`result`) экономия есть уже на маленькой задаче.

Символы (доп. колонка, не вывод): small 2779 / 16370 / 729; medium 12817 / 36446 / 10020; large 17825 / 162316 / 66184. Large compact diff = 16399 байт (обрезка с суффиксом `…(truncated)`).

`lane_commit` в compact **нет** (нет в `keep_keys`, `economy.py:105-129`). Хост видит `worktree_path` и `branch`.

---

## Находки

### F-1 Execute-цикл отдаёт ровно один poll, затем `done` — живой job нельзя дождаться вслепую

- Серьёзность: **blocker**
- Статус: подтверждено репро
- Где: `grok_delegate/session.py:527-540` (план execute = execute → poll → session_end); `session.py:1012-1016` (poll без `job_id` скипается); `session.py:1049-1067` (следующий шаг — `kind: end`, `done: true`); `agent_runtime.py:185-193` (execute возвращает `state` сразу, job фоновый)
- Что хост получает: карточка execute; после bind — одна карточка `{job_id}`; затем `done=true` / `Call session_end now.`
- Что ему нужно, чтобы продолжить: крутить `grok_agent_poll` **вне** навигатора, пока state не terminal; либо ждать десятки секунд между execute и next (карточка этого не говорит). Live evidence: job на `max` шёл 32.3 с.
- Репро: `session_begin intent=execute` → `session_next` → execute → `session_next` (poll) → `session_next` → `kind: end`. Зафиксировано в `navigator_traces.json` / mode `execute`.
- Последствие: хост закрывает сессию на бегущем job. `session_end` при живом job ставит `status=need-human`, `next=Job still running — cancel or wait.` (`session.py:1133-1135`) и **не** кладёт карточку cancel/poll. Skill (`skills/grok-mcp/SKILL.md:20-24`) после `done=true` велит `session_end` и stop.

### F-2 `session_next` сам ест `max_polls`; tiny обрывает execute до poll и triage до doctor

- Серьёзность: **major**
- Статус: подтверждено репро
- Где: `session.py:32-36` (`tiny`: tools=3, polls=2; `small` 6/4; `normal` 12/8); `session.py:876-900` (каждый next: `polls_used += 1` и `tool_calls_used += 1`, затем `>=` → `why: budget exhausted`)
- Что хост получает на `host_budget=tiny` + execute: 1) execute; 2) `done=true`, `host_message=Budget exhausted — call session_end now.`, poll **не был**. Job, если уже стартовал, остаётся бегущим. Tiny + triage: status, затем exhausted, **без** `grok_delegate_doctor`.
- Что ему нужно: не использовать tiny для execute/triage, либо поднять `max_tool_calls`, либо invent poll вне цикла. Skill по умолчанию `small` (`SKILL.md:19`) — tiny при этом рекламируется схемой begin (`server.py:1506-1509`).
- Репро: begin `host_budget=tiny`, intent=execute, bind job после первой карточки, второй next. Трасса `execute_tiny` / `triage_tiny`.
- Последствие: бюджет «polls» — это лимит вызовов навигатора, не `grok_agent_poll`. Хост, который копит polls «на job», обнаруживает ноль раньше первого настоящего poll. `session_end.budget_report.was_capped=true` (`session.py:1155-1180`), job не отменяется.

### F-3 Verify нельзя нацелить на существующий job: `job_id` в begin нет, poll молча скипается

- Серьёзность: **major**
- Статус: подтверждено репро
- Где: `server.py:1049-1061` (begin принимает intent/goal/budget/project_root/artifacts/tests/correlation_id — **нет** `job_id`); `session.py:1012-1016`; план verify `session.py:541-546`; `references/verify.md:3` («tick/poll only»)
- Что хост получает: карточка `grok_agent_status` (пустые args), затем end. Poll из плана исчез без сообщения.
- Что ему нужно заранее: `job_id` живого execute-job, который **некуда передать** через навигатор. `session_tick` принимает `job_id` (`server.py:1093-1104`), но cheap-loop skill его не вызывает.
- Репро: begin `intent=verify` (gate ready) → два next. Трасса `verify`. Тот же skip: `tests/test_session_protocol.py:192-199`.
- Последствие: режим «проверить работу» не проверяет работу. Хост invent `grok_agent_poll {job_id}` (откуда id?) или начинает новый execute.

### F-4 Update: `kind: tool` ломает хост 0.9.0; confirm в цикл не входит; `next_step` всё ещё про shell

- Серьёзность: **major**
- Статус: подтверждено репро
- Где: карточка `session.py:945-962`; `_ROUTES["update"]["next"]` `session.py:91` = `"Run update_mcp.sh then session_begin."`; `_ALLOW` `session.py:39-57` **без** `grok_agent_update`; skill `SKILL.md:20` и `server.py:1548-1550` — только `host_cmd | mcp_tool | end`; CHANGELOG 0.10.0: breaking `kind: host_cmd` → `kind: tool`; тег `v0.9.0` отдавал `bash skills/grok-mcp/scripts/update_mcp.sh …`
- Что хост получает: `{"kind":"tool","tool":"grok_agent_update","args":{}}` (preview); `why` просит `confirm=true`; следующий next — end. `recommended_tools` = next/end/tick, **без** `grok_agent_update`.
- Что ему нужно: понять `kind: tool` как вызов MCP-инструмента (в skill этого kind нет); затем **изобрести** второй вызов `{confirm: true}` и перезапуск MCP. Карточки на apply нет.
- Репро: begin `intent=update` → next → next. Трасса `update`. Args `{}` схему `grok_agent_update` проходят (`additionalProperties: false`, `confirm` optional).
- Последствие: хост 0.9.0, который исполняет только `host_cmd|mcp_tool|end`, пропускает update молча. Хост 0.10.0 по skill делает preview и stop — мост не обновляется. Если пойдёт за `next_step` begin — запустит устаревший `update_mcp.sh`.

### F-5 `PROJECT_NOT_ENABLED` не выводит из навигатора: карточки `grok_agent_project` нет

- Серьёзность: **major**
- Статус: подтверждено репро
- Где: `server.py:941-978` (ошибка); `server.py:1462-1477` (описание инструмента — единственное место, где сказано «call this first»); `_ALLOW` без `grok_agent_project`; в 0.9.0 этого гейта не было (`git show v0.9.0:grok_delegate/server.py` — пусто)
- Что хост получает: валидную execute-карточку; вызов → `{ok:false, error:"PROJECT_NOT_ENABLED", message:"…write one naming a preset to opt in", config_path, presets}`. Имя инструмента в payload **нет**. Следующий next: poll скипается (job не создан) → end.
- Что ему нужно: вызвать `grok_agent_project` с `project_root` + `preset` (required: `project_root`, `server.py:1476`) или сам написать `.grok-mcp.json`. Это догадка: не карточка, не поле ошибки.
- Репро: begin execute на корне без конфига → next → execute с card.args → next. Трасса `execute_no_config`.
- Последствие: цикл 0.9.0 «begin → execute» на 0.10.0 упирается в отказ и закрывается. Оператор видит «сессия завершена», работы нет.

### F-6 Компактный poll на типичном write-job дороже `git diff` того же изменения

- Серьёзность: **major**
- Статус: подтверждено репро
- Где: `economy.py:101-175`; poll `server.py:1148-1158` (`{"ok": True, **compact}`); потолок `economy.py:20`
- Что хост получает: JSON с `changed_files` + `diffstat` + **bounded `unified_diff`** + slim tests + 4 events. Это не «вместо diff», это diff плюс обёртка.
- Что ему нужно, чтобы «сэкономить»: либо не читать `unified_diff` (тогда ~500 токенов метаданных vs весь diff — тогда да, дешевле, но skill/playbook как раз просят читать bounded diff: `economy.py:198`); либо задача должна быть крупнее 16 KiB diff.
- Репро: замер в таблице; SHA `e79e0bf`, `9ea40f4`, `2332359`. Скрипт `%TEMP%\grokmcp-host\audit_host_contract.py`.
- Последствие: заявленная выгода «компактный receipt вместо репозитория» vs **полного job record / дампа events** — правда. Vs «просто посмотреть diff» на 1–2 файлах — хост платит больше. Порог появления экономии vs diff: обрезка на 16 KiB.

### F-7 Вторая install-карточка `grok login && grok --version` неисполнима в Windows PowerShell 5.1

- Серьёзность: **minor**
- Статус: подтверждено репро
- Где: `session.py:926-943`; платформенный installer починен (`session.py:416-424`, evidence §4) — **login-карточка нет**
- Что хост получает: `kind: host_cmd`, `cmd: "grok login && grok --version"`. На этой машине PowerShell **5.1.26100.9168**; `&&` там синтаксическая ошибка.
- Что ему нужно: переписать команду (`grok login; grok --version` или cmd.exe), то есть **не** исполнить карточку дословно.
- Репро: begin `intent=install`, `which=None` → второй next. Трасса `install`. Первая карточка `irm … install.ps1 | iex` на Windows корректна.
- Последствие: цикл install на стоковом Windows-хосте ломается на шаге auth после успешного installer-card. Починка curl|bash этот шаг не закрыла.

---

## Режимы навигатора (хост исполняет только card)

Обязательное с begin: `session_id` (возвращается). `project_root` / `correlation_id` / artifacts / tests — если не переданы, карточка execute/consult **собирает пакет сама** (`session.py:272-323`): root = первый allowlisted, `correlation_id` = `sess-{sid}` или переданный, artifacts из якорей цели иначе `["src"]`, tests иначе `["python -m pytest -q"]`. `job_id` хост знать не должен — кроме verify (F-3).

| Режим | Последовательность карточек (small, gate ready кроме install) | Обязательные args карточек | Хост должен знать заранее | Тупик вслепую? |
|---|---|---|---|---|
| brainstorm | `mcp_tool` consult `{task:{objective,project_root,correlation_id,role:consult}}` → `end` session_end `{session_id}` | consult: task с тремя полями + role | `session_id`; root подставляется | Нет, если consult ок. Review в план не входит (есть в recommended). |
| execute | execute `{task: полный write-пакет}` → poll `{job_id}` → end | execute: task (objective, project_root, correlation_id, expected_artifacts, test_commands); poll: job_id | `session_id`; job_id биндится с execute (`server.py:1206-1210`) | **Да (F-1)**: один poll. Tiny — ещё и без poll (F-2). |
| verify | status `{}` → end (poll скипается) | status: пусто | job_id **некуда подать** | **Да (F-3)** |
| install | `host_cmd` installer → `host_cmd` login → end | cmd строкой | ничего; на Windows installer ps1 | Login-карточка на PS 5.1 (F-7) |
| update | `kind: tool` grok_agent_update `{}` → end | пусто = preview | ничего | **Да (F-4)**: apply/confirm не карточкой |
| triage | status `{}` → doctor `{}` → end | пусто | ничего | На small нет. На tiny — без doctor (F-2) |
| feedback | `mcp_tool` session_end `{session_id, suggest_issue:true, note}` при `done=false` → затем `end` | schema end допускает эти поля | `session_id` | Нет. Двойной end: skill велит next до `done=true`, первая карточка уже end. |
| operate (только auto, intent=operate → `INTENT_INVALID`) | status `{}` → end `why: Pick execute/brainstorm via new begin` | пусто | ничего | Цель не делается. Skill после done — **stop**, а not begin заново. |

Цикла «next всегда одна и та же рабочая карточка» нет: после плана — `done=true`. Повторные next после done снова отдают end, пока бюджет не кончится, затем тот же end с `why: budget exhausted`. Ловушка только если игнорировать `done`.

`args` **выданных** карточек прошли `inputSchema` из `list_tools` (0 schema_errors в трассах). Отвергается не card, а **`plan[].args_hint` из begin**: execute hint `{objective, max_turns}` → extra properties + missing `task`. Consult hint `{objective}` — то же. Хост, который исполняет `plan` begin, а не `card` next, ломает схему. Skill это запрещает (`Do not re-plan`); begin всё равно отдаёт план с битым hint (`session.py:533-536`, `test_project_config.py:231-235` фиксирует hint, не схему инструмента).

---

## Проверено и оказалось в порядке

- **Карточки next vs схемы инструментов.** Execute/poll/consult/status/doctor/update-args/`session_end` из навигатора валидны. Poll без `session_id` (`additionalProperties: false`) — лишний `session_id` даёт `ARGUMENTS_UNKNOWN` (трасса `poll_session_id`). Это контракт 0.9.0, не регресс 0.10.0. Cancel с пустым args не эмитится.
- **Bind job_id.** Execute/fix биндят poll; consult не крадёт slot (`tests/test_session_protocol.py:245-271`, `server.py:1206-1210`). Хосту не нужно копировать id вручную **внутри той же сессии**.
- **Skill-зеркала.** `py -3 scripts/verify_skills.py` → `SKILL VERIFY PASS` (5 зеркал, body_words=99). SHA-12 `SKILL.md` = `4fdcea055a0b` во всех: `skills/`, `.claude/`, `.cursor/`, `.codex/`, `.agents/`. Расхождения зеркал нет; устаревшее — **содержимое** (нет `kind: tool`), не drift.
- **Коды ошибок различимы, если смотреть правильное поле.** Job-инструменты: `error` = код, `message` = текст (`guard.py:822-830`). Сессия: `error_code` = код, `error` = текст (`session.py:605-616, 866-874`). `ARGUMENTS_UNKNOWN` (хост вызвал неправильно) ≠ `JOB_UNKNOWN` ≠ `PROJECT_NOT_ENABLED` ≠ `INTENT_INVALID`/`BUDGET_INVALID` ≠ `SESSION_UNKNOWN`. Сервисный внутренний сбой в этом срезе не маскируется под ARGUMENTS. Путаница возможна, если хост читает только `error` как код — на session там предложение. Это не тупик цикла, поэтому не отдельная F.
- **Схемы list_tools vs `contracts.py` vs `schemas/*.json`.** MCP execute: role не required, пакет без role валиден; `validate_task_packet` подставляет role инструментом (`forced_role`). Опубликованный `schemas/grok-task-packet.v1.schema.json` требует `role` всегда — расхождение для того, кто валидирует карточку **файлом схемы**, не `list_tools`. Дефолты: schema `reasoning_effort=high` / `max_turns=40`; economy/env могут дать `low`/`12`. `additionalProperties: false` на task и на poll совпадает с отказом unknown fields. Не дыра навигатора, если хост берёт схему из `tools/list`.
- **`_shrink` / `_clip`.** Soft max 4096 символов JSON (`session.py:61,326-356`). Карточка сохраняется; режутся playbook/job/строки. На next-карточках симуляции truncation не срабатывал. Исчерпание бюджета — явное сообщение, не молчаливый clip.
- **Operate** не зависает и не повторяет одну рабочую карточку: status → end. Это не дыра протокола, это пустой цикл для цели без глагола (`_auto_mode` → operate, `session.py:481`). Хост, который stop по skill, цель не делает — см. таблицу режимов.
- **Feedback** схема-валиден; `suggest_issue` есть в schema end. Черновик issue хост получает, если исполнит первую карточку, а не только вторую.
- **Brainstorm/triage/status на small** проходятся без догадок (consult/status/doctor args пустые или полный task).
- **0.9.0 → 0.10.0 кроме update.** Poll без `session_id` уже в 0.9.0. Новые поля receipt (`tests_skipped_reason`, `lane_commit`) — output, `additionalProperties: true` в `grok-work-receipt.v1`. Тихий слом кроме F-4: opt-in проект (F-5), которого в 0.9.0 не было.

Вне среза (не разбиралось): permission-гейт, правдивость полей receipt, гонки, качество тестов, инсталлятор как продукт.

---

## Вердикт

**Вслепую цикл write-job до конца не пройти.** Навигатор отдаёт самодостаточные карточки consult/status/doctor/execute-start, но: (1) живой execute нельзя дождаться одним poll; (2) verify не принимает `job_id`; (3) update preview не apply; (4) `PROJECT_NOT_ENABLED` не чинится карточкой; (5) tiny съедает бюджет до poll. Brainstorm и triage на `small` — да, если гейт зелёный.

**Компактный receipt экономит vs полный job record всегда** на замеренных сценариях (79% / 61% / 88% токенов меньше). **Vs `git diff` того же изменения — нет, пока diff не упрётся в 16 KiB.** На 1 файле (+12) compact 728 токенов против 208 у diff. Заявленная экономия «не открывать репозиторий» работает как «не глотать events/stdout»; как «дешевле самого diff» — только после порога обрезки.
