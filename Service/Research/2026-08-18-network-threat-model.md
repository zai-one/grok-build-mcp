# Модель угроз сети — grok-mcp 0.12.0

- Репозиторий: `D:\ZAI\MCP\Grok CLI` (zai-one/grok-mcp)
- Ветка: main
- Версия: `0.12.0` (`grok_delegate/guard.py:22`, `pyproject.toml`)
- Дата: 2026-08-18
- Режим: только чтение. Единственный созданный файл — этот отчёт.
- Зонд HTTP: только `127.0.0.1`, рабочий каталог `%TEMP%\mcp-threat\`, одноразовый токен, сервер после прогона остановлен. Job-инструменты моста (`grok_agent_execute` и т.п.) **не** вызывались против живого Grok CLI и **не** создавали worktree в репозитории.

Читал: `AGENTS.md`, `docs/SECURITY.md`, `Service/Audits/2026-08-17-security.md`, `Service/Audits/2026-08-17-triage.md`, затем код слоёв 1–7.

---

## 1. Контекст: локальное доверие vs «хост — не этот человек»

Локальная модель, ради которой мост написан: **хост = этот же человек на этой же машине**. Доверие к хосту абсолютно, потому что хост — Cursor/Claude/Codex оператора, а процесс MCP — его же. Allowlist, токен, реестр job’ов, `grok login` и право писать `.gitignore` имеют смысл только пока «клиент» неотличим от «владельца машины».

Как только появляется сетевой режим (HTTP MCP, VPS, второй хост за тем же bearer), «хост» перестаёт быть этим человеком. В 0.12.0 **нет сущности клиента**. Есть процесс и один секрет. Всё, что процесс умеет локально, умеет любой, кто знает токен.

Это не гонка и не «спека транспорта как чеклист». Это сломанная граница тождества.

---

## 2. Что уже закрыто аудитом 2026-08-17 (не находки)

Цитирую триаж (`Service/Audits/2026-08-17-triage.md`) и не переоткрываю:

| Было | Статус в 0.12.0 |
|---|---|
| security F-3: loopback без токена = полный `tools/call`, в том числе simple POST `text/plain` | **применено**: токен обязателен везде, включая loopback; не-JSON POST отвергается |
| security F-1/F-2: узкий redactor (`xai-`/`sk-`, маркер через перевод строки) | **применено**: GitHub/GitLab/AWS/Slack/Google/JWT/npm + пароль в URL; stdio склеивает stderr до редакции |
| security F-5: второй контур `test_commands` (чужой `python.exe`, `.env`) | **применено**: argv[0] не внутри worktree; `.env`/`id_rsa`/`*.pem` запрещены |
| security F-4: `TRUST_HOST_ROOTS=1` + подложный `CLAUDE_PROJECT_DIR` | **отклонено как дефект**: задокументированный opt-in, по умолчанию выкл. Ниже — только новый угол «этот флаг локальный по форме», без переоткрытия F-4 |
| HTTP non-loopback без токена не биндится | держалось в 0.10.0 и держится: без токена не биндится **никакой** host |

Зонд этого среза (приложение) подтверждает, что **фикc F-3 на loopback POST держится**.

---

## 3. Находки

Ранжированы по цене для человека, которому причинят вред (оператор машины, где крутится мост и `grok login`). Максимум 7.

### T-1 Один bearer удостоверяет машину, не человека и не сессию

- Серьёзность: blocker
- Статус: подтверждено репро
- Уверенность: проверено
- Где: `grok_delegate/http_server.py:28-47` (`_configured_token` — один секрет на процесс), `:82-96` (`_authorized`: `Authorization: Bearer` + `hmac.compare_digest` с **одним** `self.server.token`), `:1696-1738` (`handle_jsonrpc` не получает ни соединение, ни Origin, ни client id), `grok_delegate/server.py:1003-1013` (`handle_tool_call(..., principal: str = "local-dev")`), `:1707-1713` (`initialize` отдаёт `serverInfo` и **не сохраняет** `clientInfo`)
- Что ломается, когда клиент не тот же человек: токен отвечает на вопрос «знаешь ли ты секрет процесса?», а не «ты ли оператор / это ли та же сессия редактора». Второго клиента нет в модели. Любой, у кого есть токен, для аудита остаётся `principal=local-dev` (это же значение ушло в stdout-аудит во время зонда). Ротации нет: секрет из env/`TOKEN_FILE` живёт, пока жив процесс. Per-connection identity за пределами «есть/нет тот же Bearer» отсутствует.
- Репро: зонд `%TEMP%\mcp-threat\probe_http_csrf.py` на `127.0.0.1:56017`. POST `/mcp` без Bearer → **401** `{"error":"unauthorized"}`. Тот же POST с throwaway Bearer + `Origin: http://evil.example` + `Host: evil.example` → **200** `tools/list` (23 инструмента). Сервер не смотрит Origin/Host и не отличает «браузер злоумышленника» от «Cursor оператора».
- Что предлагаешь: **запретить** разделять один HTTP-процесс между двумя людьми. Не «защитить ACL поверх того же токена» — токен по конструкции не является личностью. Если сеть нужна одному оператору — один клиент, один процесс, токен только у него, TLS снаружи. Второй человек = второй инстанс с **своим** allowlist и **своим** `grok login`, либо никакого HTTP.
- Цена ошибки, если я не прав: задокументированный VPS-сценарий (`docs/install/vps.md`) как раз «тот же человек со второго устройства». Если оператор никогда не отдаёт токен другому и не светит его странице, T-1 не кусается. Цена ложного запрета — отказ от собственного же remote Claude.

### T-2 Реестр job’ов без владельца: чужой poll читает diff, чужой cancel гасит работу

- Серьёзность: blocker
- Статус: подтверждено репро
- Уверенность: проверено
- Где:
  - генерация id: `grok_delegate/agent_runtime.py:957-964` (`_job_id` = `job-` + SHA256(task+transport+lane)[:16], детерминированный)
  - replay: `agent_runtime.py:135-145` (тот же id → `idempotent_replay: True`, чужой пакет с теми же полями садится на чужой job)
  - LANE_BUSY процесса: `agent_runtime.py:146-152` (любой running с тем же `lane`, без клиента)
  - реестр: `grok_delegate/jobs.py:44` (`_JOBS` глобальный dict), `:328-352` (`list_jobs` без фильтра)
  - poll: `server.py:1167-1183` (`grok_agent_poll` без `job_id` → список; с `job_id` → `compact_job_record` **без проверки владельца**)
  - cancel: `server.py:1185-1192` → `agent_runtime.py:206-256` (`cancel_agent_job`: неизвестный id → `JOB_UNKNOWN`; чужой клиент **не** проверяется; `JOB_NOT_OWNED` = другая **инкарнация сервера**, не другой клиент)
  - утечка id через status: `agent_runtime.py:967-985` (`cancellable_jobs: sorted(_CANCEL_EVENTS)` в `runtime_status`, его отдаёт `grok_agent_status`)
  - legacy poll ещё шире: `server.py:1243-1256` (`grok_delegate_poll` с id возвращает `**record` целиком)
- Что ломается, когда клиент не тот же человек: клиент B с тем же токеном (T-1) вызывает `grok_agent_poll` без аргументов и получает `job_id` клиента A. Затем poll по этому id забирает receipt (абсолютный `worktree_path`, `unified_diff`, `summary`, тесты). `grok_agent_cancel` по тому же id ставит `cancel_requested` на **живой** job A. `LANE_BUSY` — взаимный DoS по имени полосы. Детерминированный id: даже без списка B может вычислить id, если знает пакет A.
- Репро (loopback, без Grok CLI, `run_task` подменён на ожидание события):
  1. В том же процессе, что HTTP-сервер: `start_agent_job(...)` → `job-f4fd373b23221b21`, `state=running`, lane `grok/grok-client-a`.
  2. HTTP POST `tools/call grok_agent_poll {}` с throwaway Bearer → 200, `jobs: [{job_id: job-f4fd373b23221b21, ...}]`.
  3. HTTP POST `grok_agent_poll {job_id}` → 200, чужой running job.
  4. HTTP POST `grok_agent_cancel {job_id}` → 200, `{"ok": true, "cancel_requested": true, "state": "running"}`.
  5. Отдельный dummy с уже готовым `result.unified_diff` / `worktree_path`: poll вернул `D:\victim\project\.grok\lanes\x` и `diff --git a/secret.py ...` в `structuredContent.result`.
- Что предлагаешь: **запретить** по сети `poll` без `job_id`, `grok_delegate_poll`, выдачу `cancellable_jobs` и cancel/poll чужого id. Не «добавить ACL и оставить список» — список и есть вход. Пока нет client id, сеть не должна видеть реестр вообще: только id, который этот же вызов execute только что вернул, и только этой TCP-сессии (а сессий на уровне HTTP всё равно нет — см. T-1). Практически: сетевому клиенту не давать cancel/poll-all; локальному stdio можно оставить как есть.
- Цена ошибки, если я не прав: сломается DX «poll без id, покажите последние job’ы» для одинокого оператора. Это удобство, не контракт приёмки.

### T-3 Allowlist корней — на процесс, не на соединение

- Серьёзность: blocker
- Статус: подтверждено кодом (живой execute в чужой корень не запускался: read-only)
- Уверенность: проверено (проверка глобальная); запись файлов клиентом B — следствие существующего execute, не отдельный сетевой прогон
- Где: `grok_delegate/guard.py:882-908` (`CLAUDE_PROJECT_DIR` / `GROK_DELEGATE_TRUST_HOST_ROOTS`, чтение **env процесса**), `:956-985` (`parse_allowed_roots_env`), `:1003-1008` (`path_in_allowlist` — equality после `resolve()`, без субъекта), `grok_delegate/server.py:498-545` (`load_allowed_roots` из `os.environ` / injected; host-root **дописывается** в тот же список), `:553-599` (`resolve_trusted_repo_root`: клиентский `repo_root` сверяется со **всем** списком), `:910-925` (`_handle_project_tool` — тот же `load_allowed_roots()`), `:1198-1220` (execute берёт `load_allowed_roots()` ещё раз). HTTP-обработчик **не** передаёт per-connection allowlist в `handle_jsonrpc`.
- Что ломается, когда клиент не тот же человек: список корней один на процесс. Клиент B может указать `project_root` / `repo_root` любого корня, выданного «для A» (или выставленного оператором в env). Это не «потомки» и не обход equality — это легальный вход в чужой allowlisted git-корень. `GROK_DELEGATE_TRUST_HOST_ROOTS` в сети ещё хуже по *форме* (F-4 не переоткрываю): `CLAUDE_PROJECT_DIR` — каталог **серверного** процесса, не workspace удалённого клиента. Флаг либо отдаёт всем клиентам корень машины, либо не помогает удалённому человеку открыть *свой* проект.
- Репро: в коде нет ветки «этот HTTP-клиент / этот session_id». Два POST `/mcp` с одним Bearer — два вызова `load_allowed_roots()` по одному `os.environ`. Зонд `tools/list` после bind на loopback показал, что `grok_agent_execute` / `grok_agent_project` рекламируются любому предъявителю токена.
- Что предлагаешь: **запретить** один процесс с несколькими корнями разным людям. Сеть: либо один корень на инстанс, либо HTTP не поднимать. `TRUST_HOST_ROOTS` в сетевом режиме **запретить** (оставить только stdio, где хост = редактор). Не пытаться «размножить allowlist по Origin» — Origin не проверяется (T-7).
- Цена ошибки, если я не прав: оператор с несколькими своими репозиториями в одном `ALLOWED_ROOTS` и одним своим Claude — штатный локальный режим. Запрет «несколько корней» ударит только если его применить и к stdio.

### T-4 Удалённый клиент тратит `grok login` пользователя OS, под которым крутится сервер

- Серьёзность: blocker
- Статус: подтверждено кодом
- Уверенность: проверено
- Где: `grok_delegate/acp.py:194-205` (`StdioACPTransport.run`: `Popen(argv, cwd=..., stdin/stdout/stderr=PIPE)` **без** `env=` → полное наследование окружения процесса, включая `USERPROFILE`/`HOME` и тем самым `~\.grok`), `:541-559` (managed WebSocket: `env = os.environ.copy()` + `GROK_AGENT_SECRET`, учётные данные CLI снова из окружения пользователя сервера), `grok_delegate/status.py:172-228` (`probe_auth_presence` через `grok models`, файл `auth.json` не читается, но факт сессии — сессия **этого** OS-user), `grok_delegate/economy.py:183-186` и `:223-225` (прямо: «Grok CLI … on this host»; «Host tokens buy orchestration only; Grok does the long coding loop»), `docs/install/vps.md:15-21` (`grok login` на VPS, затем HTTP).
- Что ломается, когда клиент не тот же человек: это не сноска про spawn. **Платит оператор машины.** Bearer HTTP — не счёт xAI. Удалённый Claude/Cursor оркестрирует; worker — локальный `grok`, аутентифицированный `grok login` того OS-user, чей процесс слушает порт. Клиент B жжёт квоту/подписку A, читает репозитории A глазами модели, которая ходит в xAI от имени A. Консультация (`grok_agent_consult`) тратит те же credentials, даже без записи в lane.
- Репро: в stdio-пути нет подмены `HOME`/`GROK_*` на «учётку клиента» — такого параметра в task packet нет. Зонд CLI не запускал (read-only, не жечь чужой аккаунт). Наследование env видно по `Popen` без `env=` vs явный `os.environ.copy()` на WS.
- Что предлагаешь: **запретить** сетевой доступ к любым инструментам, которые спавнят `grok`, пока клиент ≠ владелец `grok login`. Не «лимитировать max_turns» — это чужой счёт. Если сеть = тот же оператор со второго устройства, это *задуманная* модель VPS; тогда T-4 не дефект, а контракт, который надо написать в SECURITY.md одной фразой: «удалённый MCP тратит ваш `grok login`». Если клиент может быть другим человеком — HTTP не включать.
- Цена ошибки, если я не прав: запрет execute/consult по сети убивает единственный честный VPS-offload из `docs/install/vps.md` и `economy.py` «vps».

### T-5 Инструменты и побочные эффекты, которые пишут вне lane

- Серьёзность: major
- Статус: подтверждено кодом
- Уверенность: проверено
- Где:
  - `grok_agent_project`: `server.py:892-937` — пишет `<root>/.grok-mcp.json` в **allowlisted корень** (не в lane). После этого job-гейт (`server.py:952-995`) считает проект включённым.
  - `grok_agent_update`: `server.py:822-888` + `updater.py:200-213` — при `confirm=true` делает `git -C <checkout моста> pull --ff-only` и `python -m pip install -e <checkout>`. Это дерево **моста**, не lane клиента. Грязный checkout отказывается; чистый — нет.
  - `ensure_lane_dir_ignored`: `runner.py:624-660` дописывает `.gitignore` проекта; вызывается из `agent_runtime.py:321-335` на каждый write-role **до** `prepare_worktree`. Это не MCP-имя, а побочный эффект `execute`/`fix`.
- Что ломается, когда клиент не тот же человек: B включает чужой репозиторий (`preset=max`), обновляет код моста (supply chain на процесс, который слушает сеть) или меняет `.gitignore` оператора без ревью lane. `update` с `confirm=true` — это право админа машины, выданное тем же Bearer, что и `economy`.
- Репро: `tools/list` на зондовом сервере вернул `grok_agent_update` и `grok_agent_project` в первой тройке. Живые `git pull` / запись `.grok-mcp.json` / `.gitignore` **не** делались (read-only). Поведение записи — по коду выше; тесты `tests/test_project_config.py`, `tests/test_updater.py`, `tests/test_lane_home.py` его фиксируют.
- Что предлагаешь: **запретить по сети целиком**, не «закрыть confirm». Локальный stdio: оставить. `ensure_lane_dir_ignored` при сетевом execute либо не вызывать, либо считать причиной запретить remote execute (T-3/T-4). `project` без `preset` (read) по сети тоже лучше запретить: он отдаёт, включён ли корень и какие пресеты есть.
- Цена ошибки, если я не прав: оператор не сможет обновить мост с телефона. Это приемлемо: `update` уже требует грязный-checkout check и restart руками; его место — за консолью у дерева моста.

### T-6 Таблица navigator-сессий общая; чужой execute приклеивается к чужой сессии

- Серьёзность: major
- Статус: подтверждено кодом
- Уверенность: проверено (привязка); угон по перебору 12 hex — догадка, не вход
- Где: `grok_delegate/session.py:149` (`_sessions: dict` на процесс), `:683-706` (`session_begin` → `sid = uuid.uuid4().hex[:12]`, кладёт goal/roots/job_id в глобальный dict), `:193-226` (`bind_session_job`: если нет session_id — ищет по `correlation_id`, иначе **последнюю** живую сессию с `mode in {execute, verify, operate, fix}` без job_id и **записывает в неё** чужой `job_id`), `server.py:1225-1229` (успешный execute/fix/start вызывает `bind_session_job` без HTTP-клиента), `:881-901` (`session_next` / `session_end` / `session_tick`: знание `session_id` = единственный ACL).
- Что ломается, когда клиент не тот же человек: A делает `session_begin` (execute). B делает `grok_agent_execute` без того же `correlation_id`. `bind_session_job` отдаёт job B сессии A. Дальше `session_next`/`tick` A ведёт чужую работу; `session_end` A закрывает чужое. Это не MCP-сессия протокола — отдельная куча в процессе, тоже без субъекта.
- Репро: чтение `bind_session_job`; unit `tests/test_session_protocol.py` (`test_execute_tool_result_binds_poll_job_id`) закрепляет «приклеить job к сессии», не «не приклеить к чужой». Живой двухклиентный HTTP-сценарий session_begin+execute не гонялся (хватило бы второго begin в том же процессе — не делал, чтобы не плодить состояние).
- Что предлагаешь: **запретить** navigator (`session_*`) по сети, пока сессии не привязаны к чему-то сильнее, чем 12 hex, известные любому держателю токена. Локально оставить. Не чинить last-session bind «для сети» — это костыль поверх T-1.
- Цена ошибки, если я не прав: дешёвый цикл `session_begin → session_next` из skill сломается у одинокого удалённого оператора. Ему останется typed execute/poll.

### T-7 Origin / DNS-rebinding / TLS / ротация / лимиты: чего нет; CSRF simple POST — закрыт

- Серьёзность: major (остаточный риск публичного bind); фикс F-3 — не регресс
- Статус: подтверждено репро
- Уверенность: проверено
- Где: `http_server.py:24` (`LOOPBACK_HOSTS` объявлен и **нигде не используется**), `:61-63` (лимит — `BoundedSemaphore` inflight 1–256, на процесс, не на IP и не на 401), `:115-118` (`/healthz` без auth, тело `{ok, service: grok-delegate, transport: http}`), `:128-168` (POST: Bearer, затем Content-Type, затем `handle_jsonrpc`; **нет** проверки Origin/Host, **нет** TLS, **нет** `do_OPTIONS`), `:171-191` (bind разрешён на любой host, если есть токен; `--host 0.0.0.0` кодом не запрещён), `server.py:152` (`PROTOCOL_VERSION = "2024-11-05"`), `:1958-1980` (`GROK_DELEGATE_HTTP_HOST` / `--host` без ограничения loopback). Ротации токена нет. MCP Streamable HTTP (спека) требует валидировать Origin против DNS-rebinding — этого кода нет.
- Что ломается, когда клиент не тот же человек: страница/rebinding с **украденным** токеном и `Content-Type: application/json` получает 200 (зонд case 4). Браузерный simple POST **без** токена больше не вызывает инструменты (фикc F-3 держится). `OPTIONS` → 501, `Access-Control-Allow-Origin` нет: классический CORS JS с другого origin **не** прочитает ответ; DNS-rebinding делает origin «своим» и CORS не нужен. Пустой Content-Type при валидном Bearer **пропускает** JSON-гейт (`if content_type and content_type != "application/json"`) — это не браузерный simple request (браузер шлёт `text/plain`), но дыра в заявленной «только JSON» линии. TLS в процессе нет: `docs/install/vps.md` честно говорит «терминируйте в Caddy»; сам бинарь может слушать plaintext на всех интерфейсах.
- Репро: см. приложение. Кратко:
  - `text/plain` без Bearer → **401**
  - `text/plain` + Bearer → **415** `content_type_must_be_json`
  - `application/json` без Bearer → **401**
  - `application/json` + Bearer + `Origin: http://evil.example` → **200**
  - bind без токена → `ValueError: HTTP transport requires GROK_DELEGATE_HTTP_TOKEN, including on loopback`
  - `0.0.0.0` **не биндился** (запрет задания); вывод только из кода.
- Что предлагаешь: **запретить** bind не-loopback из этого процесса (оставить TLS terminator как единственный public socket). Origin проверять fail-closed. Пустой Content-Type считать не JSON. Это уже «защитить», не «запретить инструмент». Инструменты из T-5 всё равно **запретить** по сети. Ротацию не имитировать в MCP tools — сменить файл токена и перезапустить.
- Цена ошибки, если я не прав: проверка Origin сломает прокси, который не шлёт Origin; запрет `0.0.0.0` сломает тех, кто уже так публикует plaintext (их надо сломать).

---

## 4. Главный вывод: что по сети ЗАПРЕТИТЬ, а не защищать

Мост, который по сети умеет меньше, чем локально, — нормальный ответ. **Нельзя** сделать из 0.12.0 многопользовательский сервер, «дописав ACL»: субъекта нет (T-1), allowlist один (T-3), job’ы общие (T-2), счёт Grok один (T-4).

### Запретить архитектурно

- Один HTTP-процесс на двух людей. Второй человек → другой OS-user / другой инстанс / никакого HTTP.
- `GROK_DELEGATE_TRUST_HOST_ROOTS=1` на HTTP.
- Bind не-loopback самим `grok_delegate.server` (`--host 0.0.0.0` и т.п.).
- Общий `GROK_DELEGATE_JOBS_DIR` между двумя живыми MCP (это уже backlog триажа; для доверия: на диске receipts без ACL).

### Запретить как MCP-инструменты на HTTP (оставить на stdio)

| Запрет | Почему не «защитить» |
|---|---|
| `grok_agent_update` | Админ машины + supply chain. `confirm` — не граница личности |
| `grok_agent_project` (запись **и** чтение) | Включает job’ы в корне оператора; read палит, какие корни включены |
| `grok_agent_poll` без `job_id`, `grok_delegate_poll` | Это каталог чужих работ |
| `grok_agent_cancel` | Без владельца это cancel чужого job |
| `grok_agent_session_*` | Глобальная куча + last-session bind |
| `grok_delegate`, `grok_delegate_start` | Legacy-запись в allowlisted корень тем же процессом |
| `grok_delegate_inspect` | Чтение проекта через CLI оператора |

### Если сеть = тот же оператор со второго устройства (единственный честный VPS)

Разрешить узкий subset, понимая T-4 (платит его `grok login`):

- `grok_agent_status` — **ограничить**: выкинуть `roots.allowed`, `lanes_parent_by_root`, `runtime.cancellable_jobs`
- `grok_agent_economy`, `grok_delegate_doctor`, `grok_delegate_models`
- `grok_agent_consult` / `grok_agent_review` / `grok_agent_execute` / `grok_agent_fix` / `grok_agent_start` — только если клиент гарантированно он; иначе **запретить** (читают код и тратят его Grok)
- `grok_agent_poll` — только с `job_id`, который этот клиент только что получил (в 0.12.0 этого капа нет → на практике **запретить poll**, пока нет сессии TCP=клиент)

**Безопасное подмножество без личности клиента:** `initialize` / `ping` / `tools/list` (и то палит поверхность) + `GET /healthz`. Всё, что спавнит `grok`, трогает git-корень или читает реестр job’ов — нет.

Не пытаться «защитить» update/project/listing: их не должно быть в HTTP `tools/list`.

---

## 5. Таблица инструментов

Локально = stdio, хост = этот человек. Сеть = HTTP 0.12.0 как есть, клиент может быть не он.

| Инструмент | Локально | По сети |
|---|---|---|
| `grok_agent_status` | разрешить | ограничить (убрать корни и `cancellable_jobs`) или запретить |
| `grok_agent_economy` | разрешить | разрешить (плейбук без секретов; учит слать execute — не давать его, если execute запрещён) |
| `grok_agent_update` | разрешить | **запретить** |
| `grok_agent_project` | разрешить | **запретить** |
| `grok_agent_session_begin` | разрешить | **запретить** |
| `grok_agent_session_tick` | разрешить | **запретить** |
| `grok_agent_session_next` | разрешить | **запретить** |
| `grok_agent_session_end` | разрешить | **запретить** |
| `grok_agent_start` | разрешить | запретить, пока нет личности; тому же оператору — ограничить (как execute) |
| `grok_agent_execute` | разрешить | запретить другому человеку; тому же оператору — ограничить одним корнем на инстанс |
| `grok_agent_fix` | разрешить | как execute |
| `grok_agent_consult` | разрешить | запретить другому человеку (чтение кода + чужой счёт Grok) |
| `grok_agent_review` | разрешить | как consult |
| `grok_agent_poll` | разрешить | **запретить** без id и чужой id; своего id нет в протоколе → практически запретить |
| `grok_agent_cancel` | разрешить | **запретить** |
| `grok_delegate` | совместимость, запись | **запретить** |
| `grok_delegate_plan` | совместимость, read-only профиль | запретить другому человеку (всё ещё спавнит grok по allowlist) |
| `grok_delegate_start` | фоновая запись | **запретить** |
| `grok_delegate_poll` | список + полный record | **запретить** |
| `grok_delegate_status` | разрешить | как `grok_agent_status` — ограничить/запретить |
| `grok_delegate_doctor` | разрешить (`grok doctor`, без `fix`) | ограничить (диагностика OS-user) |
| `grok_delegate_models` | разрешить | ограничить (тот же `grok login`) |
| `grok_delegate_inspect` | разрешить на allowlisted root | **запретить** |

Итого рекламируется 23 имени (`list_tools` в 0.12.0). HTTP `tools/list` на зонде вернул те же 23, включая update/project/execute.

---

## 6. Приложение: зонд loopback POST (фикc 0.12.0)

### Условия

- Bind: `create_http_server(host="127.0.0.1", port=0, token="<throwaway>")` → фактически `127.0.0.1:56017`
- Токен: `probe-throwaway-token-not-oauth-20260818` (не production, не OAuth)
- Каталог: `%TEMP%\mcp-threat\` (`C:\Users\codex\AppData\Local\Temp\mcp-threat\`)
- Не биндился ни один не-loopback интерфейс
- После прогона: `shutdown()` + `server_close()`
- Скрипт зонда: `%TEMP%\mcp-threat\probe_http_csrf.py` (вне репозитория)
- Сырой JSON: `%TEMP%\mcp-threat\probe_http_csrf.out.json`

Не-loopback bind с токеном кодом разрешён; **вызов** `create_http_server(host="0.0.0.0", ...)` не выполнялся.

### Bind без токена

```
create_http_server(host="127.0.0.1", port=0, token=None)
→ ValueError: HTTP transport requires GROK_DELEGATE_HTTP_TOKEN, including on loopback
```

### Запросы и ответы

Тело CSRF: JSON-RPC `tools/call` `grok_agent_economy` (как в старом F-3).

| # | Запрос | Status | Тело / заголовки |
|---|---|---|---|
| 1 | `POST /mcp` `Content-Type: text/plain` `Origin: http://evil.example` **без** Authorization | **401** | `{"error":"unauthorized"}` + `WWW-Authenticate: Bearer`. До JSON-RPC **не** дошло |
| 2 | тот же `text/plain` **с** Bearer | **415** | `{"error":"content_type_must_be_json"}` |
| 3 | `POST /mcp` `Content-Type: application/json` без Bearer | **401** | `unauthorized` |
| 4 | `application/json` + Bearer + `Origin: http://evil.example` + `Host: evil.example` | **200** | `tools/list`, 23 tool names. Origin/Host **игнорируются**. CORS ACAO нет |
| 5 | Bearer, **без** `Content-Type`, JSON body `tools/list` | **200** | JSON-гейт пропускает пустой header (`http_server.py:140`: `if content_type and ...`) |
| 6 | `GET /healthz` без auth | **200** | `{"ok": true, "service": "grok-delegate", "transport": "http"}` |
| 7 | `GET /readyz` без auth | **401** | `unauthorized` |
| 8 | `OPTIONS /mcp` preflight (`Origin`, `Access-Control-Request-Method: POST`, `Access-Control-Request-Headers: authorization,content-type`) | **501** | HTML `Unsupported method ('OPTIONS')` — браузерный CORS preflight не проходит |

Неверный Bearer разной длины (`Bearer short`) → **401**, не 500.

### Кросс-клиентский job (тот же процесс, второй HTTP-клиент = тот же токен)

После `start_agent_job` (Grok не спавнился, `run_task` ждал событие):

```
POST /mcp  Authorization: Bearer <throwaway>  Content-Type: application/json

{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"grok_agent_poll","arguments":{}}}
→ 200  structuredContent.jobs[0].job_id = job-f4fd373b23221b21

{"jsonrpc":"2.0","id":11,"method":"tools/call","params":{"name":"grok_agent_poll","arguments":{"job_id":"job-f4fd373b23221b21"}}}
→ 200  state=running

{"jsonrpc":"2.0","id":12,"method":"tools/call","params":{"name":"grok_agent_cancel","arguments":{"job_id":"job-f4fd373b23221b21"}}}
→ 200  {"ok": true, "cancel_requested": true, "state": "running"}
```

Dummy с готовым receipt: poll вернул `worktree_path` и `unified_diff` чужого job.

`JOB_NOT_OWNED` воспроизвёлся только на `jobs.start_job` **минуя** `start_agent_job` (нет записи в `_CANCEL_EVENTS`). Это «другая инкарнация», не «другой клиент». Живой typed job **отменяется** любым держателем токена.

### Вердикт по фиксу 0.12.0

**Держится.** Браузерный simple POST (`text/plain`, без preflight, без Authorization) больше не вызывает инструменты: 401, а с токеном — 415. Регресс F-3 не найден.

Фикс **не** создаёт границу «хост = этот человек»: тот же Bearer + `application/json` — полная поверхность, Origin не смотрят, реестр job’ов общий.
