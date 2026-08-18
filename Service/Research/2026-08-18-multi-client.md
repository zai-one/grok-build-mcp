# 2026-08-18 — многоклиентность внутри одного процесса (инженерный срез)

Мета:

- дата: 2026-08-18
- репозиторий: `D:\ZAI\MCP\Grok CLI` (`zai-one/grok-mcp`), ветка `main`
- SHA HEAD: `faab02ec430dd8f9c127741132be986b278a9232` (`git rev-parse HEAD`, только чтение)
- версия: `0.12.0` (`grok_delegate/guard.py:22`, `pyproject.toml`)
- режим: только чтение и анализ; код не менялся; единственный записанный файл в репозитории — этот
- вне среза: соответствие MCP-спецификации, threat model (ими занимаются соседние срезы в `Service/Research/`)

## Исходные точки из аудита 2026-08-17

Аудит конкурентности (`Service/Audits/2026-08-17-concurrency.md`, baseline тогда `e79e0bf1`) уже зафиксировал: межпроцессового lane-lock нет (`_START_LOCK` / `list_jobs` — память одного процесса); если `jobs.start_job` запишет `running` до `thread_starter`, а `submit` бросит, запись не терминализуется → `LANE_BUSY` + `JOB_NOT_OWNED` до рестарта. Здесь это known, заново не нумерую. Срез ниже — про несколько клиентов на **один** процесс (сетевой режим), не про два процесса.

## Находки

F-1. Детерминированный `job_id` + идемпотентный replay без скоупа клиента
- серьёзность: высокая
- где: `grok_delegate/agent_runtime.py:957-964` (`_job_id` = `job-` + sha256(JSON `{task, transport, lane}`)[:16]); `:135-145` (любая существующая запись → `ok: true`, `idempotent_replay: true`, worker не стартует)
- репро: `py -3 %TEMP%\mcp-multi\probe_multi.py` → `%TEMP%\mcp-multi\probe-multi.json` ключ `q1`
- последствие: два клиента с одинаковым пакетом получают один job. Пока запись жива, второй не запускает свою работу: на `running` смотрит чужой ход, на `done`/`error` получает чужой исход и не может повторить упавший пакет. Окно — не время, а «пока запись в реестре» (до вытеснения при >64 finished или рестарта без `GROK_DELEGATE_JOBS_DIR`).
- уверенность: проверено
- цена ошибки, если я не прав: если оператор сознательно хочет идемпотентность одного хоста на одном процессе, «чинка» в uuid сломает повторный submit при ретрае сети и тест `test_concurrent_idempotent_start_reserves_exactly_one_worker`.

F-2. `bind_session_job` без `session_id` вешает job на последнюю подходящую сессию
- серьёзность: высокая
- где: `grok_delegate/session.py:193-227` (fallback: `reversed(_sessions)` — новейшая unbound сессия с mode ∈ {execute, verify, operate, fix}); `grok_delegate/server.py:1225-1229` (после execute/fix/start **не** передаёт `session_id`, только `correlation_id` из пакета)
- репро: `py -3 %TEMP%\mcp-multi\q2_bind.py` (вывод `%TEMP%\mcp-multi\q2_bind.out.txt`)
- последствие: две живые execute-сессии ждут привязки. `bind_session_job("job-shared-1")` без sid/cid вернул `e9d725f978a5` (сессия B, вторая), у A `job_id` остался `null`. Два `session_begin` с одним `correlation_id` → bind по cid тоже берёт новейшую. Сессия A дальше не получит poll-карточку на этот job; сессия B будет поллить работу, которую не заказывала.
- уверенность: проверено
- цена ошибки, если я не прав: на navigator-пути карточка execute несёт `correlation_id` сессии (`session.py:325-326`, `:309-316`), и при уникальных cid bind попадёт в нужную сессию. Слом заденет только typed execute с чужим/пустым cid и дубликаты cid.

F-3. Отмена и poll не знают, кто вызывает: достаточно `job_id`
- серьёзность: высокая
- где: `agent_runtime.py:206-256` (`cancel_agent_job(job_id)` — нет session/caller); `server.py:1185-1192` (MCP cancel = только `job_id`); `server.py:1168-1183` (poll без `job_id` → `jobs.list_jobs` всего процесса); `server.py:1696-1738` (`handle_jsonrpc` не несёт идентичности клиента)
- репро: `%TEMP%\mcp-multi\probe-multi.json` ключ `q5`: один и тот же пакет → `job-64b45579b9247cd3`; `cancel_agent_job` этого id вернул `ok: true`, `cancel_requested: true` без session-binding; `grok_agent_poll` без id вернул этот job в общем списке
- последствие: второй клиент, который знает пакет (тот же objective/correlation_id/lane/transport) или просто вызывает poll без id, оперирует чужим job. Это не «угадать 64 бита хеша с нуля», а отсутствие изоляции в API.
- уверенность: проверено
- цена ошибки, если я не прав: на stdio с одним хостом на процесс это намеренный контракт (`poll`/`cancel` карточки — `{job_id}` only, `session.py:322-324`). Фильтр «только своя сессия» сломает хост, который поллит по id, не держа session_begin.

F-4. Allowlist, cwd и env — свойства процесса, не клиента
- серьёзность: высокая (если на одном HTTP-процессе два проекта); иначе средняя
- где: `server.py:498-545` (`load_allowed_roots` читает `os.environ`); `contracts.py:114-119` + `guard.py:1003-1008` (корень задачи должен **точно** совпасть с записью allowlist); `agent_runtime.py:270-278`, `:362` (cwd = `project_root`, затем worktree задачи); `agent_runtime.py:937-940` (`GROK_DELEGATE_LANES_PARENT` из env процесса); `server.py:1813-1828` (`GROK_DELEGATE_JOBS_DIR` процесса)
- репро: `probe-multi.json` ключ `q4`: env с двумя корнями → оба пакета валидны; тот же пакет B на allowlist только из A → `PROJECT_ROOT_UNTRUSTED`
- последствие: клиент A может стартовать job в проекте B, если B есть в процессном allowlist. Полоса пишется в `<project_root>/.grok/lanes/<slug>` **задачи**, не «домашний» каталог клиента. `os.environ` у worker'а общий. Два клиента на разные проекты на одном процессе ничего не изолирует, кроме точности `project_root`.
- уверенность: проверено
- цена ошибки, если я не прав: оператор с одним проектом в `GROK_DELEGATE_ALLOWED_ROOTS` уже fail-closed; второй клиент с чужим корнем получит `PROJECT_ROOT_UNTRUSTED` и находка не бьёт.

F-5. Второй клиент не ждёт в очереди на ту же полосу: отказ. Очередь есть только на другие полосы и она не fair-queue клиентов
- серьёзность: средняя
- где: `agent_runtime.py:41-44` (`_CONCURRENCY` дефолт 1, жёсткий потолок 2; `_MAX_QUEUED` дефолт 8, потолок 32; `_ADMISSION = BoundedSemaphore(concurrency+queued)`); `:153` (`acquire(blocking=False)`); `:146-152` (`LANE_BUSY`); `:153-156` (`QUEUE_FULL`); `http_server.py:61-63`, `:156-157` (отдельный семафор HTTP, дефолт 16, `429 too_many_requests`)
- репро: `probe-multi.json` ключ `q3` (семафор 1+1): та же полоса → `LANE_BUSY` / `lane grok/lane-one already has a running job`; другая полоса → `ok`, `state=running` при ещё не стартовавшем worker; третья → `QUEUE_FULL`. После release worker'ы пошли `q3-one`, затем `q3-two`.
- последствие: RPC не блокируется. Клиенту нужно самому ретраить `start` (нет `retry_after` в ошибке). На одной полосе очереди нет — все ждут снаружи, stampede. На разных полосах `ThreadPoolExecutor` FIFO, но при `_CONCURRENCY=1` длинный первый job держит всех (head-of-line). HTTP inflight — другой контур: он ограничивает одновременные JSON-RPC, не длительность job (start возвращается сразу).
- уверенность: проверено
- цена ошибки, если я не прав: встроить wait-очередь на `LANE_BUSY` удержит HTTP/stdio вызов минутами и упрётся в таймаут хоста; текущий отказ безопаснее для канала.

F-6. Один JSON-реестр на процесс: лимит 64, вытеснение чужих finished, без файловой блокировки
- серьёзность: средняя
- где: `jobs.py:42` `MAX_JOBS = 64`; `:121-134` (вытесняются только не-`running`, старые `finished_at` первыми); `jobs_store.py:30`, `:42` (`DEFAULT_MAX_JOBS=64`, `MAX_JOB_FILE_BYTES=1_000_000`); `:215-264` (temp + `os.replace`, flock нет — по репозиторию `flock`/`LockFile` не встречаются); `:314-372` (`evict_on_disk`)
- репро: `probe-multi.json` ключ `q6`: 69 файлов → `removed: 5`, осталось 64
- последствие: jobs всех клиентов в одном `_JOBS` и одном каталоге. Клиент B, нагенерировав finished-записи, вытесняет идемпотентный ключ клиента A → тот же пакет A стартует **новый** worker (обратная сторона F-1). Рост диска ограничен 64×≤1 МБ. sqlite нет.
- уверенность: проверено
- цена ошибки, если я не прав: на одном клиенте 64 — сознательный cap (тест `test_registry_is_bounded`); раздельный стор ради второго клиента усложнит rehydrate и STALE_RUNNING.

F-7. Таблица `_sessions` без замка и без удаления после `session_end`
- серьёзность: средняя
- где: `session.py:149` (`_sessions: dict` модуля); во всём `session.py` нет `threading.Lock`; `:1234-1235` (`ended = True`, запись не `pop`); единственный `clear` — `reset_sessions_for_tests` (`:1239-1241`). HTTP: `http_server.py:50-64` `ThreadingHTTPServer` + `daemon_threads = True`
- репро: чтение указанных строк; q2 показал, что две сессии живут в одном процессеном словаре. Живой HTTP не поднимался.
- последствие: параллельные bind/begin/end с двух HTTP-клиентов гоняют один словарь без замка. Законченные сессии копятся до смерти процесса. Fallback F-2 итерирует весь словарь.
- уверенность: проверено (нет lock / нет pop); вероятно (повреждение при гонке HTTP) — гонку потоков отдельно не ставил
- цена ошибки, если я не прав: stdio MCP — один поток JSON-RPC, замок не нужен; TTL сессий на короткоживущем процессе не стоит lock-аудита.

## Ответы на вопросы 1–7

### 1. `_job_id` детерминированный: фича или коллизия?

Оба. В одном клиенте это идемпотентность (тест `tests/test_round8_bridge.py:1483-1526`: два параллельных start → один worker, `[False, True]` по `idempotent_replay`). Для второго клиента на том же процессе — коллизия.

Как считается (`agent_runtime.py:957-964`): SHA-256 от канонического JSON `{"lane", "task", "transport"}` с `sort_keys=True`, префикс `job-`, 16 hex. В `task` после `validate_task_packet` входят все поля пакета, включая дефолты процесса (`model`, `reasoning_effort`, `max_turns`, `base_ref=HEAD`, …) — список ключей в `probe-multi.json` `q1.validated_task_keys`. Другой `correlation_id` / `transport` / `lane` → другой id (там же `diff_* : true`).

Окна по времени нет. Повторный submit:

| Состояние записи | Что видит второй | Worker |
|---|---|---|
| нет записи | `idempotent_replay: false`, новый start | да |
| `running` | `ok: true`, тот же `job_id`, `state: running`, `idempotent_replay: true` | нет (уже один) |
| `done` | `ok: true`, `state: done`, `idempotent_replay: true` | нет |
| `error` | `ok: true`, `state: error`, `idempotent_replay: true` | нет |
| запись вытеснена | новый `job_id`, `idempotent_replay: false` | да |

Доказательство: `probe-multi.json` `q1.submit_while_*`. После eviction пакет с другим cid дал `job-55251f497c7469c4` vs исходный `job-2ac180dc49aead6f`.

`ok: true` на replay упавшего job — ловушка для хоста, который смотрит только `ok`.

### 2. Две сессии ждут bind — что происходит? (воспроизведено)

Алгоритм (`session.py:209-227`):

1. Если передан существующий `session_id` — пишем туда.
2. Иначе ищем с конца словаря сессию с тем же `correlation_id` (не `ended`).
3. Иначе — последнюю unbound сессию с mode execute/verify/operate/fix.

Словарь сохраняет порядок вставки; `reversed` = новейшая первая.

Команда: `py -3 %TEMP%\mcp-multi\q2_bind.py` (импорт модулей, живой MCP не нужен). Сценарии:

- Две execute-сессии, `job_id: null`. `bind_session_job("job-shared-1")` без sid/cid → bind вернул сессию B `e9d725f978a5`, у A `job_id` остался `null`, у B стал `job-shared-1`.
- Bind с `correlation_id=corr-client-a` → только A получила `job-only-a`.
- Две сессии с **одним** `corr-same` → bind по этому cid повесил job на новейшую (`9e0ece34268a`, goal `tests/b.py`), первая осталась без job.
- Bind с явным `session_id` A → только A.

Production execute (`server.py:1225-1229`) идёт по ветке 2, не 1. Уникальные cid navigator-карточек обычно спасают; пустой/чужой cid и дубликаты — нет.

Полный stdout: `%TEMP%\mcp-multi\q2_bind.out.txt`.

### 3. Одна полоса: отказ или ожидание?

Отказ на той же полосе, очередь на другой, RPC не ждёт.

Admission (`agent_runtime.py:135-157`), всё под `_START_LOCK`:

1. Есть запись с этим `job_id` → идемпотентный replay (вопрос 1), не очередь.
2. Есть другой `running` с той же `lane` → `LANE_BUSY`, текст `lane {name} already has a running job`.
3. Семафор не берётся (`blocking=False`) → `QUEUE_FULL`, текст `agent queue is full (concurrency=N, max_queued=M)`.
4. Иначе submit в `ThreadPoolExecutor(max_workers=_CONCURRENCY)` и сразу return `state: running`.

Дефолт процесса (импорт, без monkeypatch): `_CONCURRENCY=1`, `_MAX_QUEUED=8` (`probe-multi.json` верхние ключи). Env `GROK_DELEGATE_CONCURRENCY` режется до 1..2, очередь до 1..32.

Что делать второму клиенту:

- `LANE_BUSY` — не ждать в вызове; poll/ретрай start после терминала первого, либо другая полоса. Очереди на полосу нет, FIFO между ждущими `LANE_BUSY` нет, starvation в смысле очереди нет (все получают отказ), возможен stampede ретраев.
- другая полоса при свободном слоте семафора — принять свой `job_id`, poll; `phase` сначала `queued` (`jobs.py:71-72`, `:241`), worker начнётся когда освободится поток. Порядок двух разных полос в репро: FIFO (`q3-one` затем `q3-two`).
- `QUEUE_FULL` — ретрай позже. Поля retry-after нет (поиск по репозиторию: только коды ошибок).

HTTP inflight и `_CONCURRENCY` — **два контура**. HTTP (`http_server.py:61-63`, `:156-162`): `GROK_DELEGATE_HTTP_MAX_INFLIGHT` дефолт 16, `acquire(blocking=False)` → HTTP 429 `{error: too_many_requests}`, затем `handle_jsonrpc`, в `finally` release. Это лимит одновременных RPC, не job: `start_agent_job` возвращается сразу, слот HTTP освобождается, пока job ещё `running`. Job-admission живёт в `agent_runtime`.

### 4. Общий allowlist и общий каталог: что мешает двум проектам?

`load_allowed_roots` — процесс-глобальный env (или injected в тестах). Клиент A трогает проект B тогда и только тогда, когда `project_root` B **равен** записи allowlist (`path_in_allowlist` — не префикс, `guard.py:1003-1008`; тест `tests/test_round8_bridge.py:70-82` отвергает child path). На одном сервере с `GROK_DELEGATE_ALLOWED_ROOTS=A;B` оба корня валидны для любого вызова (`q4`).

Разделение:

| Ресурс | Скоп |
|---|---|
| allowlist | процесс (`os.environ`) |
| lane path | `<project_root задачи>/.grok/lanes/<slug>` (`runner.py:453-455`, `:427-430`), если не задан процессный `GROK_DELEGATE_LANES_PARENT` |
| cwd git/ACP | worktree этой задачи (`agent_runtime.py:362`) |
| `os.environ` worker'а | процесс |
| `GROK_DELEGATE_JOBS_DIR` | процесс |
| `_JOBS` / `_sessions` / `_ADMISSION` | процесс |

Мешает только совпадение корня с allowlist и то, что полосы разных `project_root` лежат в разных деревьях. Клиентской границы нет.

### 5. Cancel по `job_id`, id из пакета. Проверка

Да. Одинаковый валидированный пакет + transport + lane → одинаковый id (`q5.same_packet_same_id: true`, id `job-64b45579b9247cd3`). `cancel_agent_job` принимает только строку id; серверный инструмент — только `job_id` (`server.py:1185-1192`). Session-binding для cancel не проверяется: даже карточка cancel компилируется в `{job_id}` (`session.py:322-324`). Вызов без сессии в репро отменил running job (`cancel_requested: true`). Неизвестный id → `JOB_UNKNOWN`. Это отсутствие изоляции клиента в API, не инструкция «как атаковать».

### 6. `jobs_store` при нескольких клиентах на процесс

Один in-memory `_JOBS` + опционально каталог JSON. Лимит 64 здесь и на диске. Finished вытесняются, `running`/`unknown` на диске держатся предпочтительно (`jobs_store.py:360-361`). Файл одной записи режется/отвергается выше 1 МБ (`_serialize_record`). Блокировка: `jobs._LOCK` внутри процесса; на файлах только atomic replace. Два клиента делят cap и eviction: чужие finished съедают окно идемпотентности. sqlite нет. Межпроцессовый last-`os.replace`-wins — known аудита 2026-08-17, не повторяю.

### 7. Пять прогонов pytest под нагрузкой

Нагрузка: `py -3 %TEMP%\mcp-multi\cpu_load.py` (PID 9348, tight loop + периодическая запись tick; tick дошёл до `35600000` в `%TEMP%\mcp-multi\cpu-load.tick`). Параллельно из `D:\ZAI\MCP\Grok CLI`: `py -3 -m pytest tests -q` пять раз подряд, логи `%TEMP%\mcp-multi\pytest-run-1.txt` … `pytest-run-5.txt`, сводка `%TEMP%\mcp-multi\pytest-summary.json`. Worktree не копировался.

| N | exit | pytest time | итог | упавшие |
|---|---|---|---|---|
| 1 | 0 | 121.16 с | 727 passed, 1 skipped, 79 subtests | нет |
| 2 | 0 | 112.11 с | то же | нет |
| 3 | 0 | 102.78 с | то же | нет |
| 4 | 0 | 104.41 с | то же | нет |
| 5 | 0 | 104.37 с | то же | нет |

Третьего флейка нет. 1 skipped стабильно — `test_permission_and_artifact_readback_reject_symlink_escape` (`tests/test_round8_bridge.py:165`, directory symlink на этой Windows). Под нагрузкой прогон ~103–122 с против ~63–78 с в аудите 2026-08-17 без этого CPU loop — нагрузка была живая. Не чинил ничего.

## Что нужно для многоклиентности

Оценки — один знакомый с репозиторием инженер, без учёта ревью/релиза. Ломающие изменения MCP-схемы по правилам проекта = minor.

| Работа | Минимум «второй не ломает первого» | «Правильно» (tenant внутри процесса) |
|---|---|---|
| Убрать fallback newest в `bind_session_job`; bind только по `session_id` (и/или cid без `reversed` при коллизии) | 8 ч | 8 ч |
| `job_id` не глобальный хеш пакета: uuid + опциональный idempotency key в скоупе сессии/клиента | 8 ч | 12 ч |
| `poll`/`cancel` только для job своей сессии; list-all не для чужого tenant | 8 ч | 10 ч |
| Замок + TTL/`pop` на `_sessions` | 4 ч | 8 ч |
| Per-connection / per-token allowlist, не процессный env | — | 12 ч |
| Admission per-client или честная очередь на полосу + retry-after | — | 16 ч |
| Namespace `jobs_store` (префикс tenant) + политика eviction | — | 12 ч |
| Идентичность клиента на HTTP сверх одного общего bearer | — | 16 ч |
| Тесты «два клиента / две сессии» на bind, cancel, replay, allowlist | 8 ч | 20 ч |
| Документация контракта | 2 ч | 4 ч |
| **Сумма** | **~38 ч** | **~118 ч** |

Минимум (~1 неделя) закрывает F-2/F-3/часть F-1 и F-7 и **не** делает два проекта на одном HTTP безопасной арендой: allowlist и env остаются процессными (F-4), полоса и реестр — общие (F-5, F-6). «Правильно» — по сути новый слой tenant, сопоставимый с циклом 0.10→0.12, плюс ломающая смена семантики `job_id`.

## Почему этого может быть не надо

Сейчас процесс и есть единица изоляции: stdio MCP порождается хостом на workspace, `_CONCURRENCY=1`, один job, один allowlist. Это дёшево и совпадает с кодом.

In-process multi-tenant на HTTP означает: общий GIL-процесс, общий env, общий реестр, общий семафор, детерминированные id, bind по «последней сессии», cancel по id. Чтобы второй клиент был **корректен**, нужно перенести границу изоляции с процесса на session/token — ~100 человеко-часов и новый класс багов (реhydrate, eviction, fair queue, совместимость карточек `{job_id}`).

Дешевле оставить 1 процесс на клиента: stdio как есть; если нужен сетевой доступ — reverse proxy, который поднимает отдельный `grok-delegate` (свой `ALLOWED_ROOTS`, свой `JOBS_DIR`, свой token) на клиента, а не шарит runtime. Обвязка: несколько часов systemd/скрипта, ноль переписывания admission. Потолок `_CONCURRENCY` в 2 всё равно не сделает этот процесс фермой.

Когда in-process всё же имел бы смысл: много лёгких consult-poll с одного оператора на несколько корней, без требования «клиент A не видит B». Это не второй клиент, это один оператор с широким allowlist — так сервер уже работает.

Вердикт: **не делать in-process multi-client**. Держать 1 процесс на клиента. Находки F-2 (fallback bind) и `ok: true` на replay `error` стоит чинить и для одного клиента — это не аренда, это кривой контракт.

## Не запускалось и почему

- Живой MCP stdio/HTTP и настоящий Grok CLI / ACP — не нужны: вопросы закрываются импортом модулей и unit-репро. HTTP-гонку `_sessions` (F-7 «вероятно») не ставил: это уже потребовало бы сервер и два сокета.
- Два процесса на один `GROK_DELEGATE_JOBS_DIR` — known аудита 2026-08-17, вне этого среза.
- Падение `submit` с незавершаемой `running`-записью — known, не воспроизводил.
- `git worktree`, `grok_agent_*` MCP, правки тестов — запрещены заданием.
- Третий флейк pytest — искал пятью прогонами под CPU-нагрузкой, не нашёл; чинить было нечего.

## Доказательства

- SHA: `faab02ec430dd8f9c127741132be986b278a9232`
- Команды: `git rev-parse HEAD`; `py -3 %TEMP%\mcp-multi\q2_bind.py`; `py -3 %TEMP%\mcp-multi\probe_multi.py`; `py -3 %TEMP%\mcp-multi\cpu_load.py` параллельно с `py -3 %TEMP%\mcp-multi\run_pytest5.py` (внутри пять раз `py -3 -m pytest tests -q`, cwd репозиторий)
- `%TEMP%\mcp-multi\q2_bind.py`, `q2_bind.out.txt`
- `%TEMP%\mcp-multi\probe_multi.py`, `probe-multi.json`
- `%TEMP%\mcp-multi\pytest-run-1.txt` … `pytest-run-5.txt`, `pytest-summary.json`, `cpu-load.tick`
- Соседние файлы в `Service/Research/` (threat model, transport conformance) не читались как источник этого среза и не перезаписывались.
