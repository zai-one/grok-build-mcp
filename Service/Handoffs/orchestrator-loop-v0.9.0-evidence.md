# Верификатор / evidence: orchestrator-loop-v0.9.0

| | |
|---|---|
| Задача | независимый скептик + durable handoff по v0.9.0 (`fix/orchestrator-loop`) |
| Проект | Grok MCP / `D:\ZAI\MCP\Grok CLI` (`zai-one/grok-mcp`) |
| Агент | Cursor Grok 4.6 (скептик; цикл реализации скептика не вызывал) |
| Baseline SHA | `723abf6c822d58a5bfaefb363fd448ca9f812074` (`main`) |
| Тег `v0.9.0` | `ed019dff999c325ab1a2f7e657290dee43ee0ac1` |
| Итоговый SHA | `5278a8824d22d31bd32bbd9fdef0b6d380fff4f9` (follow-up после тега; тег не двигать) |
| Ветка | `fix/orchestrator-loop` → https://github.com/zai-one/grok-mcp/tree/fix/orchestrator-loop |
| Дата | 2026-08-16 |
| **Вердикт** | **GREEN С ОГОВОРКАМИ** |

Pin CLI **остаётся снят** (`expected_agent_version: any`). Retag `v0.9.0` не делать: тег уже на `ed019df`. Фикс bind/cancel — коммит *после* тега.

Grok MCP в этой сессии Cursor **не был подключён**. Typed `grok_agent_review` / navigator не вызывались. Разбор — in-repo, defect-first. grok.com / xAI API WS не вызывались.

---

## 1. Что просили и что сделано

Оператор спросил: скептик своей работы был? ошибок нет? рекомендации лежат в handoff, чтобы Claude в следующий раз не перечитывал репозиторий?

Факт до этого прохода: pytest заявляли 460/1 и пушили; **независимого скептика не было**; **Service/Handoffs не было**.

Сделано сейчас: adversarial review коммитов v0.9.0, три своих прогона pytest до фикса, точечный фикс cancel/bind, pytest после фикса, этот файл, ссылка из `docs/orchestrator-verdict.html`.

---

## 2. Что приземлилось (v0.9.0)

| SHA короткий | SHA полный | Сообщение |
|---|---|---|
| `a758a8b` | `a758a8bafe9a2661d49f2d387c90d50754d2b1cf` | `fix(acp): unpin CLI version; mismatch warns, does not block` |
| `07f13c4` | `07f13c4b49dfdb95fd953a0a1ff14cdae82c01c5` | `fix(session): compile typed execute and poll cards for session_next` |
| `0f2c559` | `0f2c559a5ea7d53cb592e57782e02f3cfa599a42` | `feat(receipt): add bounded unified diff to compact evidence pack` |
| `646b85c` | `646b85cf6dcae02006c459c0742c8d3414e0b019` | `docs: agent rules, multi-host skills, issue templates, and v0.9.0` |
| `ed019df` | `ed019dff999c325ab1a2f7e657290dee43ee0ac1` | `docs: update orchestrator verdict with v0.9.0 results` |

Тег `v0.9.0` на origin указывает на `ed019df` (`git ls-remote --tags origin v0.9.0`).

После тега (этот цикл скептика): bind/cancel + этот handoff. **Не** `v0.9.1`, **не** GitHub Release.

---

## 3. Утверждение → доказательство

| Утверждение | Доказательство | Прогонов | Размах |
|---|---|---:|---|
| Pytest на `ed019df` (до фикса скептика): 460 passed, 1 skipped | этот чат, run1 48.53 с; `Service/Handoffs/pytest-orchestrator-loop-v0.9.0-run2.txt` 49.74 с; `…-run3.txt` 47.63 с; exit 0 | 3 | 47.63 / 48.53 / 49.74 с |
| Pytest после фикса bind/cancel: 463 passed, 1 skipped | `Service/Handoffs/pytest-orchestrator-loop-v0.9.0-after-fix.txt`, exit 0 | 1 | 49.02 с |
| Skip — не jsonschema, а symlink-escape на этой Windows-машине | `tests/test_round8_bridge.py` `test_permission_and_artifact_readback_reject_symlink_escape` → `pytest.skip("directory symlinks are unavailable on this Windows host")` | 3+1 | всегда 1 skipped |
| Default navigator-карточки (brainstorm/execute/verify/install/update/triage/feedback) валидны против `list_tools()` схем, если есть `project_root` и execute bind состоялся | in-process audit 2026-08-16: consult/execute/poll/status/doctor/session_end; poll после `bind_session_job` = `{job_id}` без `session_id` | 1 | — |
| Pin по умолчанию не блокирует typed stdio | `tests/test_agent_version_unpin.py` (`alien_agent_version_does_not_block`, `opt_in_pin_mismatch_warns_and_continues`); `grok_delegate/acp.py` `DEFAULT_EXPECTED_AGENT_VERSION is None` | suite | — |
| `compile_card_args("grok_agent_cancel")` на теге отдавал `{}` при живом `job_id` (схема требует `job_id`) | факт: audit на `ed019df`; исправлено после тега | 1 | — |
| consult/review `bind_session_job` мог занять poll-слот execute-сессии | факт: `server.py` биндил любой `AGENT_ROLE_TOOLS`; второй цикл bind перезаписывал любую живую сессию; тест `test_consult_does_not_bind_job_into_execute_session` | 1 | — |

Число 460/463 — детерминированный счётчик тестов, не шумная метрика. Три прогона до фикса совпали по счёту.

> **Поправка от 2026-08-16 (проход Claude, §11).** Для *пост-фиксного* прогона это утверждение не подтвердилось: записан был один прогон. Независимый повтор дал 462 passed / 1 **failed** / 1 skipped — `test_cancel_has_independent_grace_deadline` нестабилен под нагрузкой. Подробности и доказательства — §11.

---

## 4. Что НЕ запускалось и почему

| Проверка | Почему не запускалась | Кто может запустить |
|---|---|---|
| Live ACP `session/new`, permission/`rawInput`, `session/cancel`, WS serve/reconnect | в цикле v0.9.0 снят только `initialize`; повторный live capture здесь не делался | оператор с установленным `grok`, `scripts/capture_acp_initialize.py` + ручной session/new |
| Typed `grok_agent_review` / navigator через MCP | grok-mcp в этой сессии Cursor не подключён | хост с рабочим MCP после `pip install -e .` + restart |
| `grok --prompt-file` скептик без tools | тот же gap: нет гарантии, что CLI доступен этой сессии; разбор сделан in-repo | оператор: `grok --prompt-file … --tools "" --no-memory --max-turns 3 --verbatim` |
| Symlink-escape (`outside-link` → reject) | skip: directory symlinks unavailable on this Windows host | машина, где `symlink_to(..., target_is_directory=True)` проходит |
| Реальный execute Grok CLI в worktree | не scope скептика; фейковый ACP только в unit | live job на allowlisted root |
| GitHub Release / bump 0.9.1 | запрещено в задаче (не плодить fake release; не retag) | человек после merge |
| Docker / CI remote | в репо нет обязательного container gate для этого цикла | GitHub Actions если появится |

---

## 5. Скептик — findings (defect-first)

Вызван: 2026-08-16, in-repo review-agent pass (не grok CLI). Вердикт: нашёл 2 must-adjacent на теге, оба починены на ветке; остальное should/residual.

Формат: `[P#] title — path:line` как в review-agent skill.

```
[P2] Cancel card compiles to {} even when session has job_id — grok_delegate/session.py:compile_card_args
Default execute plan does not emit cancel, but compile_card_args is the typed-card compiler this cycle added. Poll was special-cased; cancel (required job_id, additionalProperties false) fell through to empty args_hint. A host that followed recommended_tools / a future plan step would get a schema-invalid card. Fixed after tag: poll and cancel share {job_id} or skip if unbound.

[P2] bind_session_job lets consult steal the execute poll slot — grok_delegate/server.py + session.py
handle_tool_call bound every AGENT_ROLE_TOOLS job (consult/review/execute/fix) onto the newest session. Fallback loop overwrote any non-ended session. One-job-at-a-time hides this until a typed consult runs beside an open execute navigator. Fixed after tag: bind only execute/fix/start; match correlation_id; do not overwrite unrelated sessions.

[P2] Doctor compatibility.detected_cli_version is always null — grok_delegate/server.py:handle_status_tool TOOL_DOCTOR
compatibility_report() is called without detected_cli_version. Status passes grok --version. Opt-in pin mismatch will not show on doctor. update_hint text itself is correct. Not fixed (should).

[P2] unified_diff is not the same slice as changed_files — grok_delegate/agent_runtime.py
changed_files is filtered by _changes_since (this run). unified_diff is collect_diff vs HEAD/base (reused worktree dirt included), then 16KiB cap + redact. Legacy execute path sets unified_diff to "" even after collect_diff. Host must not treat unified_diff as a 1:1 of changed_files. Not fixed (should).

[P3] intent=auto matches substring "test" inside "tests/" — grok_delegate/session.py:_auto_mode
Goal "change tests/sample.py" routed to verify, not execute. Pre-existing. Not introduced by v0.9.0. Residual.

[P3] Install/update navigator cards are bash/curl — grok_delegate/session.py session_next
On this Windows operator machine the cheap install card is not install.ps1. Pre-existing. Residual.

[P3] --no-subagents is hard-on for ACP stdio/WS — grok_delegate/acp.py:build_argv
Listed as should in the HTML verdict. Residual, this block.

No P0/P1 remaining after the bind/cancel follow-up.
Unpin does not block the typed path (tested).
Poll cards do not include session_id (tested).
Evidence cap is 16KiB at collect_diff; compact recaps; redact_text applied before clip.
Skill SKILL.md matches tools: navigator cheap loop, typed fallback, unpin default.
```

**Что сделано по находкам**

| Finding | Реакция |
|---|---|
| Cancel `{}` | исправлено после тега (этот коммит) |
| consult bind → execute poll | исправлено после тега (этот коммит) |
| doctor CLI version | не чинили; issue-shape ниже |
| unified_diff vs changed_files / legacy empty | не чинили; issue-shape ниже |
| auto `test` substring, Windows host_cmd, `--no-subagents` | не чинили; should этого блока |

Не утверждать «ошибок нет». На теге ошибки компилятора cancel и bind были. После follow-up — нет must-fix, которые я бы блокировал как schema/pin/leak/false-green.

---

## 6. Cheap verify recipe для Claude (не перечитывать дерево)

Цель: подтвердить, что ветка всё ещё то, что описано здесь, **без** полного аудита ACP/worktree/skills.

### Делать

1. `git fetch origin` && `git log --oneline origin/main..HEAD` — ожидай пять коммитов v0.9.0 плюс follow-up скептика. Тег: `git rev-parse v0.9.0` = `ed019df…`.
2. `py -3 -m pytest tests -q` — ожидай ≥463 passed, 1 skipped. Не сравнивай 460 с HEAD после follow-up.
3. Открыть **только**:
   - этот файл;
   - `AGENTS.md` (таблица typed vs navigator, unpin);
   - `skills/grok-mcp/SKILL.md` (не `references/*`, пока карточка не отвергнута);
   - `grok_delegate/session.py` — `bind_session_job`, `compile_card_args`, skip poll/cancel без `job_id`;
   - `grok_delegate/server.py` — bind только execute/fix/start + `correlation_id`;
   - `grok_delegate/acp.py` — `expected_agent_version()` / `DEFAULT_EXPECTED_AGENT_VERSION is None`;
   - `tests/test_session_protocol.py`, `tests/test_agent_version_unpin.py`;
   - `docs/orchestrator-verdict.html` (короткий HTML, не источник истины).
4. Один раз `grok_agent_status` (если MCP жив): `compatibility.expected_agent_version == "any"`, `pin_enabled false`, `mismatch_blocks_typed_path false`, в `update_hint` есть `git pull` / editable / restart MCP.
5. Receipt job: доверять `changed_files`, `diffstat`, `tests` (bridge-verifier), `worktree_path`. `unified_diff` — опциональное bounded evidence, не 1:1 с `changed_files`, cap 16KiB, после `redact_text`.

### Не делать

- Не ходить по всему дереву, `evidence/round8/`, install docs, skill `references/*`.
- Не аудитить заново unpin handshake, если `test_agent_version_unpin.py` зелёный и `DEFAULT_EXPECTED_AGENT_VERSION` всё ещё `None`.
- Не считать `summary` / agent prose приёмкой.
- Не слать `session_id` в `grok_agent_poll`.
- Не вызывать `session_next` второй раз, не исполнив execute-карточку: poll без `job_id` **скипается**, сессия заканчивается (это контракт, не баг).
- Не хардкодить `0.2.118` / `1.0.0` / `1.0.4`. Observed CLI в `evidence/live-acp/NOTES.md` — не pin.
- Не `git push` / merge из моста; человек мержит `grok/*` worktree jobs, не эту docs-ветку вслепую.
- Не retag `v0.9.0`.

### Как читать карточку vs typed

| Ситуация | Что делать |
|---|---|
| `session_next` execute card | вызвать `grok_agent_execute` с `card.args` как есть (полный `task`) |
| после execute | следующий `session_next` → poll `{job_id}` |
| schema / unknown tool | typed: consult → execute → poll → review; poll только `{job_id}` |
| MCP restart | navigator `_sessions` в памяти **пусто**. Job record durable. Poll typed с `job_id` из ответа execute |

---

## 7. Рекомендации только этого блока (orchestrator / MCP worker)

Каждый пункт: зачем, как проверить, файлы, форма issue. Pin не предлагать.

### 7.1 Live ACP досъём (must относительно CLI, не относительно pytest)

- **Зачем:** initialize на observed 1.0.4 не доказывает `session/new` cwd-stall, two-frame `rawInput`, cancel, WS reconnect. Код join/`rawInput` всё ещё от наблюдения 0.2.118.
- **Как:** на той же OS-учётки `grok login`; `py -3 scripts/capture_acp_initialize.py`; затем bounded capture session/new + permission + cancel. Фикстуры — redacted, как `evidence/live-acp/`.
- **Файлы:** `scripts/capture_acp_initialize.py`, `grok_delegate/acp.py` (`permission_decision`, session/new cwd), `evidence/live-acp/`, `docs/ACP-TRANSPORTS.md`.
- **Issue:** template `bug.md` если live расходится с кодом; иначе `improvement.md`. Поля: `grok --version`, bridge, redacted frames. Не писать expected agentVersion.

### 7.2 Двухфазный execute

- **Зачем:** navigator сам подставляет `expected_artifacts` (anchors или `"src"`) и `python -m pytest -q`. Хост может запустить write job не в те файлы.
- **Как:** `session_begin` intent=execute без artifacts → карточка всё равно валидна. Нужен шаг consult «предложи artifacts» → хост подтверждает → execute.
- **Файлы:** `grok_delegate/session.py` `_write_task_packet`, `compile_plan`, skill `references/execute.md`.
- **Issue:** `improvement.md`, acceptance: без confirmed artifacts execute-карточка не эмитится (или эмитится consult). Unpin не трогать.

### 7.3 `--no-subagents` opt-out

- **Зачем:** ACP argv всегда `--no-subagents`. Для части задач CLI-субагенты нужны; сейчас нельзя выключить на typed path.
- **Как:** unit на `StdioACPTransport.build_argv` / WS argv; флаг в task packet, default on (текущее поведение), opt-out явный.
- **Файлы:** `grok_delegate/acp.py`, `agent_runtime.py` (`no_subagents=True` на legacy), `guard.py`.
- **Issue:** `improvement.md`. Не делать вечный off.

### 7.4 Durable navigator

- **Зачем:** `_sessions` процесс-локальный. Restart MCP → `SESSION_UNKNOWN`; poll typed ещё работает, если хост сохранил `job_id`.
- **Как:** тест: begin → kill store → next → unknown; execute job_id всё ещё в `jobs_store`.
- **Файлы:** `grok_delegate/session.py`, `jobs_store.py`.
- **Issue:** `improvement.md`. Не класть секреты в session dump.

### 7.5 Doctor должен видеть CLI version

- **Зачем:** `compatibility_report()` на doctor без `detected_cli_version` → mismatch opt-in pin невидим, хотя status его показывает.
- **Как:** `test_status_and_doctor_report_unpin` расширить: doctor с pin env + mocked version → `mismatch true`, `mismatch_blocks_typed_path false`.
- **Файлы:** `grok_delegate/server.py` TOOL_DOCTOR, `status.py` `run_doctor_json` / `probe_grok_version`.
- **Issue:** `bug.md` (несогласованность status vs doctor), не pin.

### 7.6 Evidence: фильтр unified_diff + legacy

- **Зачем:** хост иначе ревьюит чужой dirty worktree или пустой diff на legacy transport.
- **Как:** тест reused worktree: pre-dirty file не в `changed_files` и не в `unified_diff`; legacy receipt `unified_diff` из `collect_diff`.
- **Файлы:** `agent_runtime.py`, `runner.py` `collect_diff`.
- **Issue:** `improvement.md`.

### 7.7 Не в этом блоке

Не делать: grok.com web client; xAI `wss://api.x.ai/v1/responses`; background updater; hardcoded CLI pin; force-push; `--no-verify`.

---

## 8. Как завести GitHub Issue / как продолжить

Шаблоны: `.github/ISSUE_TEMPLATE/bug.md`, `improvement.md`. Skill draft: `skills/grok-mcp/scripts/draft_issue.py` + `templates/issue.md`.

Обязательные поля: symptom; host (Cursor/Claude/Codex); `grok --version`; `compatibility.bridge_version`; unpin (`any` unless issue is about opt-in pin); redacted doctor/status; expected vs actual; repro. Никаких tokens / `auth.json` / `GROK_AGENT_SECRET`.

Продолжить работу **без** повторной верификации всего:

- если pytest зелёный и файлы из §6 не диффятся относительно этого handoff — не ревьюить acp handshake, skills mirrors, install.ps1, round8 fixtures;
- новый код только в listed файлах пункта 7 + тест рядом;
- после правки: `py -3 -m pytest tests -q` (и узкий файл сначала);
- skill править в `skills/grok-mcp`, затем `py -3 scripts/sync_skills.py`.

---

## 9. Что может сломаться

- Хост, который зовёт `session_next` вхолостую после execute-карточки, никогда не получит poll (skip). Это сейчас тест, не регрессия.
- Две execute-сессии без `correlation_id` в bind: fallback берёт newest execute/verify/operate без `job_id`. Контракт: один job.
- Restart MCP теряет navigator, не jobs.
- `unified_diff` может содержать leftover dirty (redacted, capped).
- Symlink-escape тест на этой машине не бежал.
- Тег `v0.9.0` **не содержит** bind/cancel follow-up. Merge ветки ≠ checkout тега.

Откат: `git revert` follow-up, либо checkout `v0.9.0` если нужен именно тег (вернутся дыры cancel/bind).

---

## 10. Следы

- handoff: `Service/Handoffs/orchestrator-loop-v0.9.0-evidence.md`
- pytest logs рядом
- HTML: `docs/orchestrator-verdict.html` ссылается сюда
- строка доски рабочей области: `.tasks/BOARD.md` (слаг `orchestrator-loop-v0.9.0`)
- файл промпта `.tasks/*`: не создавался этим проходом
- worktree: не создавался; работа на `fix/orchestrator-loop`
- GitHub Release: не создавался


---

## 11. Проход-верификация 2026-08-16 (Claude, после скептика)

Что делалось: рецепт §6 без полного аудита дерева, затем консолидация веток по просьбе оператора.
Grok MCP отвечал, `grok --version` = **1.0.4**. Итоговый SHA этого прохода: см. `git log --oneline -1`.

### 11.1 Что подтвердилось

| Проверка §6 | Результат |
|---|---|
| 5 коммитов v0.9.0 + 2 follow-up над `origin/main` | подтверждено |
| `git rev-parse v0.9.0` = `ed019df…`, тег не двигали | подтверждено |
| `DEFAULT_EXPECTED_AGENT_VERSION is None` (`acp.py:56`) | подтверждено |
| `compile_card_args`: poll и cancel в одной ветке `{job_id}` (`session.py:273`), skip без `job_id` (`session.py:914`) | подтверждено |
| bind по `correlation_id` только для execute/fix/start (`server.py:995`) | подтверждено |
| `docs/orchestrator-verdict.html` ссылается на этот файл | подтверждено |
| Логи pytest рядом соответствуют заявленным 460 / 463 | подтверждено |
| [P2] doctor без `detected_cli_version` — всё ещё открыт | подтверждено, `server.py:759` |

### 11.2 Опровергнуто: пост-фиксный suite нестабилен

`tests/test_round8_bridge.py::test_cancel_has_independent_grace_deadline`

| Прогон | Результат | Время |
|---|---|---|
| full suite #1 | **462 passed, 1 failed, 1 skipped** | 62.64 с |
| full suite #2 | 463 passed, 1 skipped | 77.73 с |
| изолированно ×3 | 3 × passed | ~0.8 с |

Падение: `assert holder["result"]["blocked_reason"] == "ACP_CANCEL_TIMEOUT"` → фактически `ACP_CANCELLED`.

Механизм: тест патчит `CANCEL_GRACE_SECONDS` в **0.2 с**, спит 0.3 с и ждёт, что фикстура
`CANCEL_IGNORED_FIXTURE` проигнорирует cancel и мост упрётся в grace-дедлайн. Окно в 200 мс сравнимо
с джиттером планировщика Windows под нагрузкой полного suite: процесс успевает закрыться раньше
дедлайна, и мост честно рапортует чистый `ACP_CANCELLED`.

Это **не регрессия ветки**: `git diff 723abf6 df833c2 -- grok_delegate/acp.py` не содержит ни одного
изменения в cancel-пути, а единственная правка `tests/fake_acp_agent.py` — инъекция
`GROK_FAKE_AGENT_VERSION` для unpin-тестов. Дефект преэкзистентный, не заводился как finding.

**Следствие для следующего хоста:** один зелёный прогон suite ≠ зелёный suite. Счётчик тестов
детерминирован, счётчик *проходов* — нет. Ориентир §6 «≥463 passed, 1 skipped» держать, но
единичный fail этого теста трактовать как flake, а не как регрессию, пока не тронут cancel-путь.

### 11.3 Опровергнуто: §6 п.4 нечем проверить

`grok_agent_status` вернул `server.version` = **0.8.0** и **не содержит блока `compatibility` вообще**.
Блок появляется только в v0.9.0 (`status.py: compatibility_report`). То есть запущенный в хосте MCP —
сборка *до* этой ветки, и проверить `expected_agent_version == "any"` / `pin_enabled false` через неё
нельзя. Проверка §6 п.4 остаётся **невыполненной** до `pip install -e .` + рестарт MCP.
Код на ветке при этом верный — подтверждено чтением, а не вызовом.

### 11.4 Найдено и починено: ACP output burst

При консолидации веток в worktree `grok/safe-consult-acp-bridge` обнаружена **незакоммиченная** правка,
которой нет ни в одной ветке. `_line_reader` клал кадры с `timeout=0.1` и на `queue.Full` ставил
overflow и **выбрасывал весь остаток вывода агента**, помечая прогон как `ACP_OUTPUT_LIMIT`. Обычный
backpressure потребителя дольше 100 мс — не превышение лимита. Правка снимает таймаут и добавляет
явный stop-event ридерам.

Патч лёг на `main` чисто, в комплекте регрессионный тест
`test_stdio_reader_applies_backpressure_for_bounded_bursts`. Suite: **464 passed, 1 skipped**.
Закоммичено в `main` по решению оператора. Исходный патч сохранён:
`Service/Handoffs/archive/safe-consult-acp-bridge-uncommitted.patch`.

### 11.5 Консолидация веток

Продакшен-ветка ровно одна: **`main`**. `origin` содержит только `main` и тег `v0.9.0` (не двигали).

| Ветка | Была | Судьба |
|---|---|---|
| `fix/orchestrator-loop` | df833c2 | fast-forward в `main`, удалена локально и на origin |
| `fix/mcp-json-wiring-and-schema-guard` | f31ea63 | уже содержалась в `main`, удалена локально и на origin |
| `github-main-v0.8.0` | 37bacb1 | предок `main`, worktree снят, удалена |
| `grok/b2b-cro-plan-third-pass` | 37bacb1 | предок `main`, worktree снят, удалена |
| `grok/safe-consult-acp-bridge` | a1ae467 | несвязанная история, worktree снят, удалена → тег `archive/safe-consult-acp-bridge` |
| `master` | 6b02884 | несвязанная история, удалена → тег `archive/master-round8` |
| `main-local-v0.4.0-backup` | ba5134e | несвязанная история, удалена → тег `archive/local-v0.4.0-backup` |

Три последние **не мержились намеренно**: `git merge-base main master` пуст — общего предка нет, это
до-GitHub'овская локальная линия. Ни одного файла, которого нет в `main`, они не содержат
(`comm -13` по `ls-tree` пуст для всех трёх); их различия — старые версии `README.md`, `server.py`
и прочего. Мерж не добавил бы работы, а вернул бы регрессию. Вместо мержа — локальные архивные теги,
так что история достижима и удаление обратимо в этом клоне.

`D:\ZAI\MCP\grok-lanes` пуст. Архивные теги локальные, на origin не пушились.

### 11.6 Что по-прежнему не проверено

Всё из §4 остаётся в силе. Дополнительно: §6 п.4 (см. 11.3) и symlink-escape (skip на этой машине).
