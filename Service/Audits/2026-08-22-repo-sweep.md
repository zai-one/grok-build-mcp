# Аудит grok-delegate 0.26.1 — экономия токенов хоста, дрейф, живые косяки

Дата: 2026-08-22. Дерево: `D:\ZAI\MCP\Grok CLI`, ветка `main`. Мост на проводе: **0.26.1**, Grok CLI **1.0.5 (5115b46bc9)**, Session Protocol **v1.2**. Продуктовый код **не менялся**.

Это список претензий, не патч. Каждая находка ниже либо воспроизведена в этой сессии, либо прочитана в текущем дереве с `file:line`. Слова агента о себе доказательством не считаются.

## Как проверялось

1. Живой navigator: `intent=triage` (цель — аудит репозитория) и `intent=brainstorm` + consult.
2. Измерения `py -3` по `list_tools()` и `_auto_mode`.
3. Чтение `grok_delegate/*`, схем, skill, README, `docs/*`, инсталляторов.
4. Три независимых read-only прохода по коду (economy / docs / leftover bugs).
5. Consult `job-c8eb5267606746fc` через typed poll — compact обрезал его отчёт; то, что доехало, совпало с чтением кода.

Рабочий MCP крутится из `%LOCALAPPDATA%\grok-mcp` (SHA совпал с origin). Аудит читал **этот** чекаут. Если они когда-нибудь разъедутся, `grok_agent_status` будет врать про код, который вы правите.

## Короткий вердикт

Продукт обещает: хост (Claude / Cursor / Codex) платит мало токенов, Grok делает тяжёлую петлю, хост читает bounded receipt. В 0.26.1 это **частично правда для poll после execute** и **ложно для первого часа жизни сессии**.

Navigator, который должен экономить контекст оркестратора, в этой сессии:

- на triage сжёг `grok_agent_status` (двойной `legacy`+`runtime`) и `grok_delegate_doctor` (TUI: clipboard, voice, темы) — цель аудита кода в план не попала;
- на brainstorm стартовал consult и сразу велел `session_end`;
- `session_end` закрыл сессию с `"job": "none"`, хотя consult уже был `completed`;
- compact poll обрезал отчёт consult до ~1500 символов и соврал `events_total: 4` при `sequence: 63`.

То есть ровно тот путь, которым skill велит пользоваться «чтобы не жечь токены», потратил токены хоста и спрятал работу воркера.

---

## P0 — ломает обещание «хост платит мало»

### 1. Dual envelope: `indent=2` + `structuredContent`

`tools/call` кладёт один и тот же объект дважды, причём текстовая копия — pretty-print:

```2065:2075:grok_delegate/server.py
        payload = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(tool_result, ensure_ascii=False, indent=2),
                }
            ],
            "structuredContent": tool_result,
```

Бюджет `ECONOMY_MAX_RECORD = 16384` считает **внутреннюю запись** без indent (`economy.py:25, 227-238`). AGENTS.md уже честно говорит: конверт ~2×. Pretty-print добавляет ещё ~1.4–1.6× к `content.text`, плюс экранирование переносов внутри JSON-строки.

Насыщенный poll: обещание 16 KiB → на проводе порядка **40–48 KiB**. Клиент, который кладёт в контекст обе копии, платит почти втрое.

**Фикс:** `json.dumps(..., separators=(",", ":"))` для `content.text`. Бюджет мерить на готовом JSON-RPC `result`, не на inner record. Dual-copy оставить только если без него ломаются старые клиенты — и назвать это в changelog, не обещать 16 KiB «на проводе».

### 2. `tools/list` — 23 жирных схемы на каждый ход хоста

Измерено в этом дереве:

| | байт UTF-8 |
|---|---|
| `list_tools()` compact | **25 838** |
| то же `indent=2` | **41 486** |

15 `grok_agent_*` + 8 compat `grok_delegate_*`. Три копии `_INPUT_SCHEMA`, пять копий task-packet. Хост держит каталог в системном промпте, даже если вызывает только `session_next`.

**Фикс:** compat-инструменты за `GROK_DELEGATE_COMPAT_TOOLS=1` (default off). Один `$defs.task_packet`. Однострочные description. `session_tick` не рекламировать, пока skill требует `session_next`.

### 3. `session_next` ест и `tool_calls`, и `polls`

```902:904:grok_delegate/session.py
    # count as a poll/tool for budget
    sess["polls_used"] = int(sess.get("polls_used") or 0) + 1
    sess["tool_calls_used"] = int(sess.get("tool_calls_used") or 0) + 1
```

Живой triage: три `session_next` → `polls_used=3` при `max_polls=4`, ни одного `grok_agent_poll`. Пресет `tiny` (3/2) не дотягивает до poll карточки execute-плана (execute → poll → end = минимум три next, плюс удержание на running job).

**Фикс:** next считает только `tool_calls`. `polls` — только карточки `grok_agent_poll` / `session_tick` по живому job. Tiny: минимум 4 next, иначе план лжёт.

### 4. Brainstorm/consult: нет poll, job не биндится, `session_end` говорит `none`

План brainstorm: consult → session_end (`session.py:541-545`). Удержание на running есть **только** если текущая карточка — `grok_agent_poll` (`1102-1108`).

`bind_session_job` вызывается лишь для execute/fix/start (`server.py:1427-1431`). Комментарий прямо запрещает consult/review занимать poll-слот (`session.py:201-202`).

Живой прогон:

- consult `job-c8eb5267606746fc` → `completed`;
- следующий `session_next` → карточка `session_end`, без poll;
- `session_end.receipt.job` = **`"none"`**.

Хост, который «делает только карточку», **никогда не увидит consult**. Skill велит именно это.

**Фикс:** для consult/review — poll-карточка с удержанием, как у execute. Либо `bind_session_job` по `correlation_id` и для read-only ролей. `session_end` без терминального job в brainstorm/verify — `need-human`, не `ok` / `none`.

### 5. Compact после begin врёт про события и режет consult

`session_begin` делает process-wide latch:

```157:161:grok_delegate/session.py
def enable_session_economy() -> None:
    ...
    os.environ.setdefault("GROK_DELEGATE_ECONOMY", "1")
    os.environ.setdefault("GROK_DELEGATE_ECONOMY_COMPACT_POLL", "1")
```

Дальше **все** typed poll в этом процессе компактные, даже если оператор compact не просил.

Порядок poll (`server.py:1381`):

1. `compact_job_record` режет events до 4 **без** `events_omitted` (`economy.py:170-171`);
2. `_bounded_poll` считает `events_total = len(уже обрезанного списка)`.

Живой poll consult: `sequence: 63`, `events_total: 4`. Хост читает «было четыре события». Тесты `_bounded_poll` это не ловят: они не композируют compact+bound.

`ECONOMY_MAX_SUMMARY = 1500` уничтожает consult — единственный смысл consult для хоста как раз длинный ответ. В этой сессии отчёт воркера обрезался на полуслове.

**Фикс:** `events_total` / `events_omitted` **до** среза. Compact не latch на процесс — флаг сессии. Для роли consult/skeptic не резать `summary` до 1500, либо отдельный потолок (хотя бы 8–12 KiB внутри общего бюджета). Тест: compact(64 events) → poll → `events_total==64`.

### 6. Triage игнорирует цель; doctor — TUI-дамп

`compile_plan` для triage всегда status → doctor → end (`session.py:536-540`). Goal аудита кода не используется. Вторая ветка triage (`566-572`) мертвая.

`grok_delegate_doctor` отдаёт сырой `grok doctor --json` без проекции (`status.py:394-420`, `server.py:849-878`). В этой сессии: clipboard routes, Remote Audio 44100 Hz, пять тем оформления. К CLI/auth это не относится.

`grok_agent_status` — два дерева (`legacy` + `runtime`) с двумя копиями `compatibility` (`server.py:1346-1358`).

**Фикс:** triage по цели: если goal про код/receipt — consult/review, не doctor. Doctor: `{ok, binary, auth, issues[], compatibility}`; сырой JSON только `verbose`. Status: один плоский объект (`cli`, `auth`, `roots`, `update.available`, `transport`, `lanes_total`).

### 7. `_auto_mode` не видит русский; goal режется до 500

```450:500:grok_delegate/session.py
_EXECUTE_WORDS = re.compile(r"(?i)\b(fix|implement|...)\b")
...
    for word in re.findall(r"[a-z]+", goal.lower()):
```

Кириллица выбрасывается. Измерено:

- «детально проверь репозиторий…» → **`operate`** (status → end «pick a new begin»);
- «check the repo for errors» → **`verify`**.

`AGENTS.md` требует отвечать оператору по-русски. Дефолт `intent=auto` на этом проекте систематически миссроулит.

`_GOAL_MAX = 500`, пакетный objective — 12 000. Navigator отдаёт воркеру обрубок ТЗ.

**Фикс:** русские глаголы (исправь/реализуй/добавь/проверь/ревью/аудит) **или** skill: всегда явный `intent`, auto не угадывать. `_GOAL_MAX` хотя бы 2000 на execute-карточках (пакет уже 12k).

---

## P1 — хост идёт не туда, потому что документы врут

### 8. Три разных «правильных» цикла

| Источник | Что велит |
|---|---|
| `AGENTS.md`, `README` Host loop, `docs/EASY.md`, skill `SKILL.md` | `session_begin` → loop `session_next` → `session_end` |
| `docs/economy.md:49-58`, `economy_playbook()` (`economy.py:374-383`) | status → economy → consult → execute → poll |
| `docs/CODEX-MCP-SETUP.md:62` | начать с `grok_agent_consult` |
| `skills/.../verify.md` | `session_tick` (петля v1.1 снята) |

Playbook всё ещё: «Set max_turns low (8–16)». На этом репо пресет `max` → карточка несёт **xhigh / 40**. `enable_session_economy` не понижает воркера — и не должен, по AGENTS. Врёт текст `grok_agent_economy`, не политика пресета.

Skill не просит `project_root` / `expected_artifacts` / `test_commands`. Тогда execute-карточка сама подставляет `["src"]` и `["python -m pytest -q"]` (`session.py:296-297`). На Windows часто нет `python`, есть `py`. Аудит 2026-08-17 F-14 жив.

Update-карточка: `kind: "tool"` (`session.py:981`). Skill/operate.md знают только `host_cmd` | `mcp_tool` | `end`. CHANGELOG это ломающее назвал — skill не догнали.

**Фикс:** один цикл везде. `economy_playbook()` = navigator + «typed = fallback». Playbook печатает фактический budget из пресета. SKILL: шаг 0 = status; в begin — artifacts/tests; `kind: tool` для update. Verify.md — `session_next`. Fallback тестов: `py -3 -m pytest tests -q` на win32, не `src`.

### 9. Инсталлятор кладёт lane не туда, куда пишут docs

Канон: `<project>/.grok/lanes` (`README.md:198-199`, `AGENTS.md`, `ENVIRONMENT.md:20`).

`install.sh:201-214` и `install.ps1:55-63` пишут `GROK_DELEGATE_LANES_PARENT=<parent>/.grok-mcp-lanes` (соседний каталог) и включают economy. README в примере всё ещё `/path/to/.grok-mcp-lanes`.

Схема compat `lanes_parent`: «rejected if inside repo_root» (`server.py:331-336`). Код как раз **разрешает** скрытый `.grok/lanes` внутри проекта.

**Фикс:** инсталлятор не выставлять `LANES_PARENT`, если оператор не просил — тогда совпадёт с `.grok/lanes`. Schema/docstring: in-project dot-dir можно, видимое дерево — нет.

### 10. `docs/SECURITY.md` обещает гейт на Read

> Permission requests are deny-by-default. … Reads/searches and writes must resolve inside cwd.

`AGENTS.md` и `Service/Research/2026-08-20-read-gate-reachability.md`: CLI 1.0.5 на Read **не спрашивает**. `_paths_confined` на чтение не срабатывает. Защита — дерево lane + редактор на выходе.

Файл всё ещё начинается с «Round 8 is a local engineering bridge». То же: `docs/CODEX-MCP-SETUP.md`, `docs/ACP-TRANSPORTS.md`, slug lane `round8-{role}-{cid}` (`agent_runtime.py:1256-1260`). Живой consult: `grok/round8-consult-audit-2026-08-22-consult`.

**Фикс:** SECURITY — два слоя: «когда CLI спросил» vs «CLI 1.0.5 на Read не спрашивает». Убрать Round 8 из операторских docs. Default slug без `round8-`.

### 11. Версии, дефолты, CONTRIBUTING

| Документ говорит | Код делает |
|---|---|
| `CONTRIBUTING.md:55-56`: держать `__version__` в `__init__.py` в синхроне | реэкспорт из `guard.py` (`__init__.py:11`); второй литерал тесты запрещают |
| `README.md:312-313`, `ENVIRONMENT.md:29`: `MAX_TURNS` **clamp** к 60 | out-of-range env → `None` («нет мнения») (`guard.py:939-953`) |
| `ENVIRONMENT.md:68`: `GROK_DELEGATE_CLI_LOG` default **off** | `(env or "1") != "0"` — default **on** (`worker_health.py:45-46`) |
| `pyproject.toml` classifiers 3.10–3.12 | CI: 3.10 и **3.13**; локально AGENTS — 3.14 |
| `session_begin` принимает `job_id` (handler `server.py:1271,1301`) | `inputSchema` поля нет, `additionalProperties: false` (`1731-1768`) → хост не может нацелить verify |
| пакетный `grok_delegate/README.md:100,110-113` | worktree «outside main repo»; shell «git-only, no pytest»; sandbox как жёсткий контроль. Дефолт `.grok/lanes` внутри проекта; verifier гоняет pytest; на Windows `--sandbox` ≠ OS-изоляция |

Версия **0.26.1** в четырёх канонических местах совпадает. Это не дыра релиза, это дыра остальных текстов.

**Фикс:** CONTRIBUTING = формулировка AGENTS. ENVIRONMENT: CLI_LOG on, MAX_TURNS «out of range игнорируется». Classifiers 3.13 (+ 3.14). `job_id` в схему begin. Пакетный README пометить legacy и починить три ложных hard bound.

---

## P2 — приёмка, гонки, схемы

### 12. `poll.ok` всегда True

```1386:grok_delegate/server.py
            return typed_return({"ok": True, **compact})
```

`ok` из receipt не поднимается (`economy.py:148-151` не копирует поверх envelope). Хост, читающий верхний `ok`, видит успех у `blocked` / `failed`. Живой consult это маскирует ещё сильнее: compact + `"ok": true`.

**Фикс:** не затирать `result.ok`. Envelope `ok` = «poll доставил запись», отдельное поле; либо `ok = (status == completed)`.

### 13. `tests_skipped_reason: null` = «verifier бегал»

Схема (`schemas/grok-work-receipt.v1.schema.json:172-184`): `null` значит, что verifier бегал. `_base_receipt` и `diff_snapshot_failure` поле не ставят; `setdefault(..., None)` (`contracts.py:355`). Пустой `tests` + `null` неотличим от «прогнали, результатов нет».

Enum не знает `DIFF_SNAPSHOT_FAILED`. Схема **не описывает** `lane_commit` / `ok` / `worker_written_files`. Ни один тест не открывает `schemas/*.json`. `contracts.py:3-5` обещает alignment — его нет.

**Фикс:** всегда ставить явную причину, если verifier не вызывался. Тест: properties schema ⊇ поля `finalize_receipt`. Добавить `lane_commit` в receipt schema.

### 14. `LANE_COMMIT_MISSING` затирает лучшую причину

Если `lane_commit` есть, но без `sha`, причина **всегда** переписывается (`contracts.py:487-495`), в том числе поверх `TEST_FAILED` / `UNEXPECTED_CHANGED_FILES`. Для отсутствующего поля — только если иначе был `completed` (асимметрия).

Коммит в lane идёт **до** `finalize_receipt` (`agent_runtime.py:708-829`) — blocked-job всё равно сеет `grok/*`. Sweep 19.08 оставил это намеренно; побочный эффект гейта — нет.

**Фикс:** `LANE_COMMIT_MISSING` только если иначе `completed`. Либо коммитить после зелёного гейта.

### 15. Legacy write не может стать `completed`

Write-legacy не заполняет `lane_commit` / `worker_written_files` / `verifier_touched_files` (`agent_runtime.py:533-610`). Гейт честно блокирует. Успешного write по `transport=legacy` нет, хотя docs/ACP-TRANSPORTS обещают те же гейты.

Compat `grok_delegate_poll` отдаёт **полный** record без compact и без `fit_poll_budget` (`server.py:1445-1452`). Неверный инструмент из каталога из 23 взрывает контекст.

**Фикс:** тот же commit+поля, что у ACP, **или** ранний `LEGACY_WRITE_UNSUPPORTED`. Compat poll — тот же bounded путь.

### 16. Cancel: окно `JOB_NOT_OWNED`

`cancel_agent_job` читает state и `_CANCEL_EVENTS` под разными замками (`agent_runtime.py:252-273`). Между `finally` (сняли event) и `_finish` (ещё `running`) cancel отвечает `JOB_NOT_OWNED` на job, который этот процесс ещё ведёт.

Межпроцессового lane-lock нет: два MCP на один `JOBS_DIR` поднимут две полосы.

`_windows_pid_alive`: `OpenProcess` + close → True без `STILL_ACTIVE` (`jobs_store.py:120-135`). Зомби PID с живым handle = «жив».

**Фикс:** одно сечение state+event. File lock `(jobs_dir, lane)`. GetExitCodeProcess == STILL_ACTIVE.

### 17. Два нормализатора lane; ответ start — сырое имя

`_lane_name` (агент) vs голый `normalize_lane` (compat start / `review_lane`). `"Foo"` у execute → `grok/foo`, у start — отказ. `grok_delegate_start` отдаёт сырой `lane`, в реестр кладёт нормализованный. `normalize_lane` вне `try/except GuardError` → сырой JSON-RPC, не `LANE_INVALID`.

Default slug всё ещё `round8-…`.

**Фикс:** один нормализатор. В ответе — то имя, что в реестре. GuardError → structured error.

### 18. Status: MCP-корни не видны как provenance

`roots.host_root_trusted` = флаг `GROK_DELEGATE_TRUST_HOST_ROOTS` (default off). `roots.host_root` = `CLAUDE_PROJECT_DIR` только если флаг on. MCP `roots/list` (default **on**) в этих полях **не отражён** — только в общем `allowed`.

В этой сессии: `host_root_trusted: true`, `host_root: null` — флаг включён, env-корня нет. Имя поля читается как «этому корню можно доверять».

**Фикс:** отдельные `mcp_roots_enabled` + список declared paths. Переименовать `host_root_trusted`.

### 19. Прочее, что стоит починить заодно

- `jsonschema` в `test_tool_schemas.py:152-155` — `skipTest`, если нет extra `[test]`. AGENTS гоняет `pytest tests -q` без extra; самый жёсткий контракт схем исчезает. Routines на это падают. Сделать fail, как routines.
- Effort enum: `guard.ALLOWED_REASONING_EFFORTS` знает `none`/`minimal`; пакетный путь отвергает.
- `_TRIM_ORDER` режет списки **с головы** (`economy.py:313-314`) — у events это выкидывает новые, наоборот от `events[-N:]`.
- `changed_files` **и** `full_changed_files` в одном compact poll — двойная цена путей.
- `compact_poll_enabled` ↔ `session_compact_active` взаимная рекурсия, ловится `except Exception` (`economy.py:35-49`). Разорвать флагом.
- `disclaimer` на каждом ответе — мелочь, но сумма.
- `grok_delegate_inspect` режет списки `[:200]`, не ширину ключей.

---

## Что не баг (не чинить «ради чистоты»)

- Пресет `max` / xhigh / 40 на **этом** репо. AGENTS: экономить контекст хоста, не работу Grok. Баг — что playbook говорит другое.
- Коммит в lane до finalize, если оператор сознательно хочет ветку даже у blocked (зафиксировано 19.08). Баг — затирание `blocked_reason`.
- Unpin CLI / отсутствие пина модели.
- Opt-in `GROK_DELEGATE_TRUST_HOST_ROOTS` + подложный `CLAUDE_PROJECT_DIR` (F-4): documented.
- Windows `--sandbox` без Landlock: status уже пишет `os_enforcement_note`. Баг — пакетный README, который это прячет.
- Dual `content`+`structuredContent` как совместимость со старыми клиентами — решение оператора; баг — `indent=2` и обещание 16 KiB на проводе.
- Закрытый бэклог 17–20.08: `LANE_BUSY` через `list_jobs(64)`, зомби `start_job`, denylist `auth.json.`, гейт `lane_commit` как таковой, удержание poll на running execute.

---

## Что сделать первым (если чинить)

Порядок — сколько токенов хоста возвращает единица работы, без ломки воркера:

1. Убрать `indent=2` у `content.text`. Тест: `len(json.dumps(rpc_result))` на saturating poll.
2. Compat-инструменты скрыть по умолчанию; `tools/list` < 15 KiB.
3. Brainstorm/consult: poll-карточка + bind job; `session_end` не `ok/none` на живом consult.
4. Compact: `events_total` до среза; не latch env на процесс; consult summary не 1500.
5. `session_next` не считать poll. Triage не звать doctor на code-goal.
6. `_auto_mode` + русский **или** запрет auto в skill. Playbook/economy.md = navigator.
7. Инсталлятор vs `.grok/lanes`. SECURITY про Read. `job_id` в схему begin.
8. `poll.ok`, `tests_skipped_reason`, `LANE_COMMIT_MISSING` не затирает.
9. Cancel-окно, schema alignment test, classifiers 3.13, jsonschema не skip.
10. Убрать Round 8 с операторской поверхности и из default lane slug.

Каждый пункт — сначала красный тест, потом правка. Зелёный набор 0.26.1 эти дыры не видит: он не композирует compact+bounded poll, не гоняет русский `_auto_mode`, не открывает `schemas/*.json`, не мерит JSON-RPC конверт.

## Живые экспонаты этой сессии

| Что вызвали | Что получили |
|---|---|
| `session_begin` intent=triage, goal=аудит репо | план status → doctor → end; `skill_ref=references/security.md` |
| `grok_agent_status` | `legacy` + `runtime`, два `compatibility`; `host_root_trusted: true`, `host_root: null` |
| `grok_delegate_doctor` | themes/clipboard/voice; CLI/auth и так были зелёные |
| `_auto_mode("…проверь репозиторий…")` | `operate` |
| `list_tools()` | 23 tools, 25838 / 41486 байт |
| consult `job-c8eb5267606746fc` | lane `grok/round8-consult-…`; compact `events_total: 4` при seq 63; summary обрезан |
| `session_end` после consult | `"job": "none"`, `"status": "ok"` |

Конец отчёта. Код не трогался.
