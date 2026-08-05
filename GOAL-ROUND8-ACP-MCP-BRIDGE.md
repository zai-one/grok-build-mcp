# GOAL ROUND 8 — рабочий MCP → Grok Agent ACP bridge

> Запускать целиком в отдельной задаче с рабочей папкой
> `D:\ZAI\MCP\Grok CLI`.
>
> Это не архитектурное эссе. Терминальный результат — работающий локальный
> MCP-сервер, реальные ACP smoke-тесты и проверяемый пакет доказательств.

## Роль

Ты — ведущий инженер интеграции агентных рантаймов. Твоя задача — довести
существующий `grok-delegate` до рабочего production-minded локального моста:

```text
Codex / Claude / Cursor / другой MCP-клиент
                    ↓ MCP
         локальный grok-delegate
                    ↓ TransportRouter
      legacy CLI/headless | ACP stdio | ACP WebSocket
                    ↓
       работа, diff, тесты, receipts
```

Основную реализацию и первичную проверку выполняет Grok. Финальное решение о
приёмке остаётся за внешним верификатором, например Codex. Сервер не должен
сам объявлять работу принятой только потому, что исполнитель вернул красивое
резюме.

## Исходное состояние, которое нужно перепроверить

- ОС: Windows, PowerShell.
- Канонический репозиторий: `D:\ZAI\MCP\Grok CLI`.
- Установлен Grok Build CLI `0.2.118` stable.
- Действует OAuth-авторизация `grok.com`; отдельный `XAI_API_KEY` не требуется.
- Локальный CLI заявляет:
  - `grok agent stdio` — ACP через stdin/stdout;
  - `grok agent serve --bind 127.0.0.1:2419` — ACP WebSocket server;
  - `grok agent headless` — работа через Grok WebSocket relay.
- Существующий MCP `grok-delegate 0.3.0` имеет восемь инструментов,
  allowlist корней, worktree-изоляцию, durable jobs и запрет push/merge/
  `--always-approve`.
- На предыдущем baseline прошли `362 tests` и `43 subtests`, self-test и
  консультативный smoke. Эти цифры не считать актуальными без повторного
  запуска.
- Два реальных исполнительских прогона после обновления `0.2.118` завершились
  `EXECUTE_NO_CHANGES`: одноразовый `grok -p/--single` ответил намерением начать,
  но не дошёл до редактирования. Такой результат запрещено считать успехом.
- Глобальные конфиги Codex/Claude и credentials находятся вне scope этой
  задачи. Не редактировать их автоматически.

## Главная цель

Не заменить один режим другим, а сохранить, починить и объединить три
управляемых backend-варианта за одним MCP-интерфейсом:

1. `legacy_cli/headless` — обычный существующий путь через установленный Grok
   CLI. Он должен продолжать давать быстрые consult/review и получить честно
   работающий execute-режим либо точный version-specific NO-GO. Нельзя молча
   удалить, понизить или объявить его consult-only без реального исследования
   доступных CLI/headless команд `0.2.118`.
2. `acp_stdio` — основной рекомендуемый агентный режим для локального запуска.
3. `acp_websocket` — опциональный постоянный daemon-режим для нескольких
   клиентов, автоматизаций и долгих сессий.

Инвариант продукта:

```text
stable MCP tools → TransportRouter
                 → legacy_cli/headless
                 → acp_stdio
                 → acp_websocket
```

Ни один backend не подменяет другой. Transport выбирается явно. Автоматический
fallback не имеет права превращать отказ ACP в непроверенный single-turn success.
Legacy execute допускается только при явном `transport=legacy` или отдельном
opt-in env-флаге и обязан проходить те же receipt/evidence gates.

## Нельзя остановиться на плане

Порядок работы: inspect → protocol spike → implementation → automated tests →
live smoke → skeptic review → fixes → повторные тесты → evidence handoff.

Не заканчивай задачу фразами «я начну», «рекомендуется реализовать» или одним
дизайном. После короткого preflight сразу создавай рабочие изменения. Если
возник настоящий blocker, зафиксируй точную команду, полный безопасный текст
ошибки, пройденные альтернативы и минимальное условие разблокировки.

## Жёсткий порядок поставки

Не пытайся строить всё одновременно. Поставляй и проверяй фазы по порядку:

1. **P0 — protocol fixtures:** получить реальные version-pinned ACP frames для
   `0.2.118` и сохранить redacted golden fixtures. До этого ACP wire code писать
   запрещено.
2. **P1 — обычный режим:** исследовать и починить текущий CLI/headless backend,
   подтвердить consult и execute либо выдать точный version-specific NO-GO на
   execute. Существующие инструменты и быстрые консультации не ломать.
3. **P2 — ACP stdio:** handshake, non-git consult, реальное изменение во
   временном git repo, test evidence, cancel и MCP end-to-end.
4. **P3 — ACP WebSocket:** loopback + env secret, handshake, короткая задача,
   completion и graceful shutdown.
5. **P4 — hardening:** durable jobs, aliases, receipts, adversarial regression
   review. Не строить сложный автоматический multi-agent loop до P1/P2.

Каждая фаза имеет собственный `GO|NO-GO`. Общий итог может быть `PARTIAL`, если
обычный режим и stdio работают, а WebSocket имеет доказанный blocker.

## Границы

### Разрешено

- Читать и изменять файлы только внутри изолированного worktree этого repo.
- Запускать локальные тесты, линтеры и bounded smoke-процессы.
- Запускать установленный `grok.exe` и локальные loopback endpoints.
- Создавать временные git-репозитории и каталоги для e2e-тестов.
- Добавлять зависимости только после source/version/license/permissions audit,
  с зафиксированной версией и объяснением необходимости.

### Запрещено

- Push, merge, force-push, публикация пакета или изменение удалённых систем.
- Изменение глобальных Codex/Claude/Grok конфигов.
- Чтение или вывод содержимого `auth.json`, OAuth tokens, API keys и secrets.
- `--always-approve`, `bypassPermissions`, произвольный shell без политики.
- Прослушивание `0.0.0.0` по умолчанию.
- Автоматическое принятие результата исполнителя.
- Замена реального live smoke моками.
- Заявлять WebSocket/ACP совместимость без настоящего handshake.

## Обязательная архитектура

### 1. MCP frontend

Сохранить stdio MCP, совместимый с Codex, Claude Code, Claude Desktop, Cursor и
другими MCP-host. Не ломать существующие имена инструментов; при необходимости
реализовать их как compatibility aliases поверх нового backend.

Новый основной tool surface должен оставаться компактным:

| Инструмент | Назначение |
| --- | --- |
| `grok_agent_status` | Версии, auth-presence без чтения секрета, transport, leader/daemon, roots, jobs |
| `grok_agent_start` | Асинхронно запустить typed task packet |
| `grok_agent_poll` | Статус, progress, события и итоговый receipt |
| `grok_agent_cancel` | Bounded cancel конкретной job без убийства MCP-сервера |
| `grok_agent_consult` | Read-only работа, включая негитовые allowlisted roots |
| `grok_agent_review` | Независимый adversarial review заданного evidence packet/diff |
| `grok_agent_execute` | Изолированное выполнение в git worktree |
| `grok_agent_fix` | Исправление только подтверждённых findings с новым receipt |

Не обязательно делать восемь отдельных внутренних реализаций. Допустим единый
typed dispatcher и тонкие инструменты-обёртки, если схемы и permission profiles
остаются различимыми.

До изменения имён зафиксировать compatibility matrix:

| Старый tool `0.3.0` | Новый tool/alias | Роль | Default transport | Permission profile |
| --- | --- | --- | --- | --- |
| `grok_delegate` | определить после baseline | execute | explicit/configured | workspace |
| `grok_delegate_plan` | определить после baseline | consult/plan | legacy или stdio | read-only |
| `grok_delegate_start` | определить после baseline | async execute | explicit/configured | workspace |
| `grok_delegate_poll` | определить после baseline | status | none | read-only |
| `grok_delegate_status` | определить после baseline | status | all | read-only |
| `grok_delegate_doctor` | compatibility alias | diagnostic | legacy | read-only |
| `grok_delegate_models` | compatibility alias | diagnostic | legacy | read-only |
| `grok_delegate_inspect` | compatibility alias | diagnostic | legacy | read-only |

Итоговая таблица должна содержать фактические mappings и покрываться
`tools/list`/`tools/call` contract tests.

### 2. Backend interface

Реализовать единый backend interface и три адаптера:

- `legacy`: текущий обычный Grok CLI/headless контур, починенный и отдельно
  протестированный;
- `stdio`: управляемый дочерний процесс `grok agent stdio`;
- `websocket`: клиент к `grok agent serve`, по умолчанию только
  `127.0.0.1`, с обязательным secret token.

Transport выбирается явно: `legacy|stdio|websocket|auto`. Безопасный default —
`stdio`. На первом этапе `auto` означает только `stdio` либо явную ошибку; не
реализовывать умный каскад fallback до готовности всех трёх адаптеров. Нельзя
молча падать с ACP execution на single-turn executor.

Не угадывать ACP wire format. До написания адаптера получить реальные redacted
frames initialize/session/prompt/cancel/permission от установленного CLI,
зафиксировать версию `0.2.118`, сохранить fixtures под
`evidence/round8/acp-fixtures/` и заставить fake ACP server воспроизводить именно
эти fixtures. Документация без protocol fixtures не закрывает P0.

Поддержать необходимый lifecycle:

- initialize/handshake;
- создание и сохранение session;
- prompt/task submission;
- streaming progress/events;
- tool/permission requests;
- completion receipt;
- cancel/interrupt;
- disconnect/reconnect;
- завершение дочернего процесса без orphan workers.

### 3. Task packet

Ввести версионированную JSON Schema, например `grok-task-packet.v1`:

```json
{
  "objective": "конкретный проверяемый результат",
  "role": "consult|execute|skeptic|fix",
  "project_root": "точный allowlisted root",
  "base_ref": "master",
  "model": "grok-4.5",
  "reasoning_effort": "high",
  "permission_profile": "read-only|workspace",
  "max_turns": 40,
  "timeout_seconds": 1800,
  "inputs": [],
  "constraints": [],
  "acceptance_criteria": [],
  "expected_artifacts": [],
  "correlation_id": "caller-generated id"
}
```

Все строки, массивы, turn/time/output budgets и пути должны иметь bounds.
Неизвестные поля отклонять. Пути нормализовать и проверять после `resolve()`.

### 4. Work receipt

Каждая завершённая работа возвращает версионированный receipt:

```json
{
  "status": "completed|no_changes|blocked|failed|cancelled",
  "job_id": "...",
  "session_id": "...",
  "transport": "stdio|websocket",
  "objective_hash": "...",
  "branch": "grok/...",
  "worktree_path": "...",
  "changed_files": [],
  "commits": [],
  "diffstat": "",
  "tests": [],
  "artifacts": [],
  "findings": [],
  "summary": "...",
  "blocked_reason": null,
  "started_at": "...",
  "finished_at": "..."
}
```

`completed` без требуемых артефактов, diff или acceptance evidence должен
понижаться до `no_changes`/`blocked`/`failed`. Текст модели не является
доказательством выполнения.

### 5. Роли и независимость

Сервер должен предоставлять отдельные операции, из которых внешний orchestrator
может собрать управляемый цикл:

```text
executor → skeptic → fixer → skeptic recheck → external verifier
```

- Executor и skeptic работают в разных ACP sessions.
- Skeptic получает task packet, diff и receipts, но не внутреннее самооправдание
  исполнителя как источник истины.
- Fixer получает только подтверждённые findings и не расширяет scope молча.
- Повторный skeptic проверяет закрытие каждого blocker.
- External verifier, например Codex, получает компактный evidence packet и сам
  выносит GO/NO-GO.
- Автоматический multi-round цикл внутри сервера не входит в обязательный DoD
  Round 8. Достаточно отдельных typed jobs/sessions и receipts. Оркестрацией
  сначала управляет Codex или другой MCP-host.

## Безопасность

Обязательно сохранить или усилить:

- exact allowlist repo/project roots;
- git worktree для write-задач;
- read-only режим без требования git для consult/review;
- разные permission profiles для consult, execute, skeptic и fix;
- запрет push/merge/force/destructive/session credential operations;
- секреты только через env/secret reference, никогда в source/log/result;
- redacted JSON audit;
- timeout на процесс, ACP request и всю job;
- output cap и ограниченный event buffer;
- loopback bind для WebSocket;
- secret authentication для WebSocket;
- защита от path traversal, symlink/junction escape и UNC escape;
- корректная Windows process-tree cancellation;
- отдельные `server_pid`, `worker_pid`, `agent_pid`;
- отсутствие orphan процессов после cancel/restart;
- deny-by-default для неизвестных tools и ACP permission requests.

Windows sandbox-флаг не считать доказательством OS-level isolation. Основные
гарантии должны опираться на allowlist, worktree, permission policy, process
boundaries и post-readback.

## Durable jobs и быстродействие

- Сохранить durable job store; миграция формата должна быть версионирована.
- После рестарта сервер различает running/unknown/orphaned/recoverable.
- Poll не должен быть необходим для продвижения фоновой работы.
- Долгий Grok-процесс живёт независимо от частоты MCP poll.
- Ограничить concurrency конфигом, безопасный default — 1 или 2.
- Поддержать очередь, bounded backpressure и idempotency/correlation key.
- Не реализовывать multiplex/leader reuse в Round 8. Безопасный default:
  concurrency `1`, отдельная session на job, без смешивания проектов.

## Автоматические проверки

### Unit/contract

- task/receipt schema validation;
- MCP tools/list и tools/call;
- ACP initialize/session/prompt/cancel на fake agent;
- exact-root allowlist;
- non-git consult;
- worktree execute;
- permission request allow/deny;
- timeout, malformed JSON-RPC, oversized output;
- audit redaction;
- Windows path, UNC, junction/symlink escape;
- no `always-approve`/bypass/push/merge in every generated argv/policy;
- backwards compatibility старых tool names.

### Integration

- fake ACP stdio process;
- локальный fake ACP WebSocket server с secret;
- server restart + durable job readback;
- cancellation без убийства MCP server;
- две queued jobs и concurrency limit;
- executor/skeptic sessions действительно различны.

### Live acceptance с установленным Grok

Live-тесты должны быть opt-in и не читать secrets. Выполнить их в безопасном
временном окружении:

1. OAuth/auth-presence probe через штатную команду, без чтения `auth.json`.
2. Реальный ACP stdio handshake.
3. Реальный read-only consult в негитовом временном каталоге.
4. Реальный execute в маленьком временном git repo:
   - создать конкретный файл;
   - получить непустой diff;
   - выполнить тест;
   - получить receipt со changed file и test evidence.
5. Реальный cancel долгой bounded job.
6. Реальный `grok agent serve` на свободном loopback-порту с secret:
   handshake → короткий запрос → completion → graceful shutdown.
7. MCP initialize → tools/list → status → start → poll через итоговый server,
   то есть тот же путь, которым воспользуется Codex.

Для обычного режима отдельно выполнить:

1. legacy consult/review через текущий CLI;
2. legacy execute в маленьком временном git repo;
3. если execute снова возвращает только намерение/`no_changes`, сохранить точный
   receipt и пометить только `legacy execute` как NO-GO, не ломая consult и ACP;
4. запретить `completed` без непустого ожидаемого diff и acceptance evidence.

Если WebSocket live smoke нельзя безопасно выполнить, stdio может быть принят
как основной готовый transport, но WebSocket нельзя объявлять готовым.

## Обязательный skeptic review

После первой реализации запусти отдельного независимого reviewer с этим
заданием:

```text
Ты — враждебный reviewer MCP→ACP bridge. Не исправляй код. Проверь protocol
correctness, auth boundary, path confinement, permission escalation, process
lifecycle, job durability, cancellation, Windows encoding/process behavior,
MCP compatibility, fake-vs-live evidence и ложные success states. Для каждого
finding дай severity, точный файл/место, воспроизведение, impact и минимальный
fix. BLOCK, если execute можно объявить completed без реального diff/evidence,
если WS доступен не только на loopback без явного разрешения, если secret может
попасть в лог, либо если после cancel остаётся worker.
```

Передай BLOCK/HIGH findings отдельному fixer:

```text
Исправь только подтверждённые findings из skeptic receipt. Не расширяй scope.
На каждый finding укажи изменение, regression test и readback. После исправлений
запусти целевые тесты и полный suite. Не закрывай finding одним объяснением.
```

Затем другой reviewer повторно проверяет каждый BLOCK/HIGH finding. Это внешний
acceptance gate, а не требование строить автоматический multi-agent workflow
внутри MCP-сервера.

## Definition of Done

Работа завершена только когда одновременно выполнено следующее:

- [ ] Старые инструменты MCP не сломаны либо имеют документированные aliases.
- [ ] Обычный legacy CLI/headless consult реально работает.
- [ ] Legacy execute либо реально работает, либо имеет отдельный честный NO-GO
      без деградации других transport.
- [ ] Новый MCP использует ACP stdio для execute/fix по умолчанию.
- [ ] ACP stdio live handshake и реальное редактирование прошли.
- [ ] Non-git read-only consult прошёл.
- [ ] `no_changes` не маскируется под success.
- [ ] Durable jobs не зависят от частоты poll.
- [ ] Cancel не убивает MCP server и не оставляет worker.
- [ ] Full test suite зелёный.
- [ ] Отдельный skeptic не оставил BLOCK/HIGH findings.
- [ ] Есть Windows copy-ready конфигурации для Codex и generic MCP host.
- [ ] Конфиги не содержат секретов и используют exact roots.
- [ ] Нет push/merge/global-config mutation.
- [ ] Evidence позволяет внешнему Codex независимо повторить acceptance.

WebSocket считается готовым только при отдельном live smoke. Итог содержит
матрицу `legacy consult | legacy execute | ACP stdio | ACP WebSocket`, и каждый
режим получает собственный verdict. Общий `GO` возможен только при четырёх GO;
иначе `PARTIAL` или `NO-GO` с точным blocker.

## Выходные артефакты

Создай или обнови:

- исходники bridge, TransportRouter и трёх transport adapters;
- JSON schemas task/receipt/events;
- unit, contract, integration и opt-in live tests;
- `README.md` с архитектурой и quick start;
- `docs/CODEX-MCP-SETUP.md`;
- `docs/ACP-TRANSPORTS.md`;
- `docs/SECURITY.md`;
- `evidence/round8/baseline.json`;
- `evidence/round8/test-results.txt`;
- `evidence/round8/live-smoke.json`;
- `evidence/round8/skeptic-receipt.json`;
- `evidence/round8/final-verification.json`;
- `ROUND8-HANDOFF.md`.

Не записывай OAuth tokens, API keys, WebSocket secret или содержимое auth-файла
в evidence.

## Формат финального handoff

Финальный ответ должен содержать:

1. `VERDICT: GO | PARTIAL | NO-GO`.
2. Что реально работает: MCP, legacy CLI/headless consult и execute, ACP stdio,
   ACP WebSocket, consult, execute, skeptic, fix, cancel, restart recovery.
3. Какие команды и live smoke действительно запускались.
4. Точные counts тестов из сохранённого evidence.
5. Изменённые файлы, branch, commits, diffstat.
6. Остаточные риски и неподтверждённые утверждения.
7. Copy-ready блок подключения к Codex без secrets.
8. Rollback: как отключить новый backend и вернуть предыдущий сервер.
9. Что должен независимо проверить Codex перед merge.

Не использовать слова «готово» и `GO`, если заявленный execute transport не
создал непустое проверенное изменение в временном git repo. Не переносить успех
одного transport на другой.

---

## Короткие role-prompts для повторного использования

### A. Архитектор

```text
Сними version-specific baseline Grok CLI и существующего grok-delegate.
Определи минимальный MCP→ACP контракт для stdio и WebSocket. Не редактируй код.
Верни protocol methods, lifecycle, threat model, compatibility constraints,
dependency choices и проверяемый implementation plan. Не угадывай wire format.
```

### B. Исполнитель

```text
Реализуй утверждённый MCP→ACP slice в изолированном worktree. Сначала сделай
минимальный работающий diff, затем тесты и документацию. Не push/merge, не меняй
глобальные конфиги, не читай secrets. Success требует live ACP receipt, а не
текстового обещания модели.
```

### C. Скептик

```text
Независимо атакуй bridge: protocol, auth, roots, permissions, process tree,
timeouts, cancellation, durability, Windows, evidence и false-success. Не
исправляй код. Верни severity-ranked findings с reproducer и минимальным fix.
```

### D. Исправлятор

```text
Исправь только подтверждённые BLOCK/HIGH findings. Для каждого добавь regression
test и readback. Не расширяй scope и не закрывай finding объяснением.
```

### E. Внешний verifier Codex

```text
Не доверяй summary исполнителя. Прочитай diff, receipts и raw test evidence;
повтори MCP handshake, ACP stdio live execute, non-git consult, cancel и, если
заявлен готовым, WebSocket smoke. GO только если факты воспроизводятся и нет
BLOCK/HIGH findings. Не merge/push без отдельного подтверждения человека.
```
