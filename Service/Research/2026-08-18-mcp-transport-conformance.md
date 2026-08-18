# Соответствие сетевого MCP-транспорта спецификации

Дата отчёта: 2026-08-18.  
Объект: неофициальный мост `zai-one/grok-mcp` (`grok-delegate`), ветка `main`, пакетная версия `0.12.0` (`grok_delegate/guard.py:22`, `pyproject.toml`).  
Это **не** номер протокола MCP. Протокол на проводе — строка даты `YYYY-MM-DD`.

Метод: первичная спецификация на `modelcontextprotocol.io` и схема в GitHub; код моста прочитан целиком по HTTP-пути; loopback-probe на `127.0.0.1:18765`.  
Не делалось: git checkout/stash/clean/commit; запись в репозиторий кроме этого файла; bind `0.0.0.0`; живой Grok CLI для HTTP (для `initialize` не нужен).

Различие, которое нельзя смешивать:

| Что | Значение здесь |
|---|---|
| `SERVER_VERSION` | `0.12.0` — версия пакета моста |
| `PROTOCOL_VERSION` | `2024-11-05` — версия MCP, которую мы рекламируем |
| ACP | отдельный протокол к Grok CLI (`ACP_PROTOCOL_VERSION = 1` в `grok_delegate/acp.py:35`) |
| FastMCP | сторонняя библиотека/прокси, **не** спецификация MCP |

Автор HTTP-модуля сам называет транспорт «Streamable-ish» (`grok_delegate/http_server.py:1`). Это признание, а не соответствие.

---

## 1. Ревизии протокола

### Какие ревизии существуют

На 2026-08-18 в репозитории схемы [modelcontextprotocol/modelcontextprotocol `schema/`](https://github.com/modelcontextprotocol/modelcontextprotocol/tree/main/schema) лежат датированные каталоги:

| Ревизия | Каталог схемы | Статус на сайте |
|---|---|---|
| `2024-11-05` | `schema/2024-11-05` | первая публичная |
| `2025-03-26` | `schema/2025-03-26` | датированный снимок |
| `2025-06-18` | `schema/2025-06-18` | датированный снимок |
| `2025-11-25` | `schema/2025-11-25` | годовая; предыдущая «большая» стабильная |
| `2026-07-28` | `schema/2026-07-28` | то, на что указывает `latest` |
| `draft` | `schema/draft` | черновик после `2026-07-28` |

Страница <https://modelcontextprotocol.io/specification/latest> (прочитана 2026-08-18) явно опирается на TypeScript-схему `schema/2026-07-28/schema.ts`. Индекс `llms.txt` тоже ведёт спецификацию в дерево `/specification/2026-07-28/`.  
URL <https://modelcontextprotocol.io/specification/2025-11-25> **существует** (прочитан 2026-08-18).

Источники changelog (первичные):

- `2025-03-26`: <https://modelcontextprotocol.io/specification/2025-03-26/changelog> — OAuth 2.1; Streamable HTTP **заменяет** HTTP+SSE; batching; аннотации инструментов.
- `2025-06-18`: <https://modelcontextprotocol.io/specification/2025-06-18/changelog> — снят batching; structured tool output; MCP-сервер как OAuth Resource Server + RFC 9728; RFC 8707; elicitation; заголовок `MCP-Protocol-Version`.
- `2025-11-25`: <https://modelcontextprotocol.io/specification/2025-11-25/changelog> — OIDC discovery; CIMD; tasks (тогда experimental); уточнение HTTP 403 на невалидный `Origin`.
- `2026-07-28`: <https://modelcontextprotocol.io/specification/2026-07-28/changelog> — сняты сессии и `initialize`; обязателен `server/discover`; заголовки `Mcp-Method` / `Mcp-Name`; HTTP+SSE формально в реестре Deprecated.

Блог проекта описывает `2026-07-28` как RC с датой публикации 28 июля 2026 ([The 2026-07-28 MCP Specification Release Candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)). Это вторичный источник; датированный снимок на сайте уже живой.

### Правила согласования версии

**Эпоха handshake (`2025-11-25` и раньше)** — Lifecycle, раздел «Version Negotiation»:

> If the server supports the requested protocol version, it **MUST** respond with the same version. Otherwise, the server **MUST** respond with another protocol version it supports. This **SHOULD** be the *latest* version supported by the server. If the client does not support the version in the server's response, it **SHOULD** disconnect.

Цитата: <https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle> (прочитано 2026-08-18). Та же норма уже была в `2024-11-05`: [lifecycle.mdx в GitHub](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/docs/specification/2024-11-05/basic/lifecycle.mdx).

**Эпоха `2026-07-28` («modern»)** — handshake нет. Каждые запрос несёт версию в `_meta` и (на HTTP) в заголовке `MCP-Protocol-Version`. Несовпадение → `UnsupportedProtocolVersionError` (код `-32022`). Клиент **SHOULD** взять пересечение из `supported` и повторить. Источник: <https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning>, разделы «Terminology» и «Protocol Version Negotiation».

Там же матрица совместимости: **legacy-сервер + modern-клиент = отказ**; **dual-era клиент + legacy-сервер = работает** через откат на `initialize`.

### Что рекламируем мы

```152:152:grok_delegate/server.py
PROTOCOL_VERSION = "2024-11-05"
```

`handle_jsonrpc` на `initialize` **не читает** `params.protocolVersion`. Всегда отдаёт константу:

```1707:1713:grok_delegate/server.py
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
        return None if is_notification else _jsonrpc_result(req_id, result)
```

По правилу handshake это допустимый ответ *только если* единственная поддерживаемая нами ревизия — `2024-11-05`. Тогда клиент, запросивший `2025-11-25`, получает более старую версию и **SHOULD** отключиться, если её уже не умеет. Мы не отдаём `error.data.supported`, не выбираем «последнюю из своих» (у нас в коде ровно одна строка), не ведём dual-era.

Тест `tests/test_grok_delegate.py:832-842` проверяет только `serverInfo.name`, не `protocolVersion`. Self-test в `__main__.py:92-103` тоже.

### Хосты: что принимают

Эмпирически (перехват трафика Claude Code / Cursor / Codex) **не проверялось**. Ниже — только документы хостов на 2026-08-18 и явно помеченные вторичные источники.

| Хост | Что сказано официально | Версия протокола |
|---|---|---|
| **Claude Code** | stdio; HTTP (`type: http`, в JSON допустим alias `streamable-http`); устаревший SSE; **отдельный** host-тип `ws` (WebSocket). Bearer-заголовок и OAuth 2.0. Документ прямо говорит, что спецификация называет транспорт Streamable HTTP. | Дата `YYYY-MM-DD` в документации **не названа**. Источник: <https://code.claude.com/docs/en/mcp> |
| **Cursor** | Три транспорта: `stdio`, `SSE`, `Streamable HTTP`. Для remote — `url` + `headers` (в примере `Authorization: Bearer …`) и отдельный объект `auth` для static OAuth. | Дата в официальных docs **не названа**. Форум Cursor (вторично) показывал `protocolVersion: "2025-06-18"` в `initialize` от `cursor-vscode`. Это нельзя считать текущей гарантией. Источник docs: <https://cursor.com/docs/context/mcp> |
| **Codex** | STDIO и Streamable HTTP; Bearer через `bearer_token_env_var`; OAuth (CIMD и DCR). Документ описывает поле `instructions` **во время initialization**. | Дата в официальных docs **не названа**. Наличие `initialize` в тексте на 2026-08-18 говорит, что handshake ещё в контракте. Статья DEV.to (вторично) утверждает, что Codex уже шлёт `2026-07-28`. Противоречие не разрешено эмпирикой. Источник: <https://developers.openai.com/codex/mcp.md> |

Вывод по хостам (честный): все три умеют **stdio** (наш основной путь) и умеют **remote Streamable HTTP**. Ни один официальный документ не сказал «отклоняем `2024-11-05`». Ни один не гарантирует, что ответ `protocolVersion: 2024-11-05` на запрос `2025-06-18` будет принят. По спецификации клиент, который выкинул первую ревизию, **SHOULD** отключиться.

### Что теряем, рекламируя самую раннюю ревизию

Это не «хосты нас сразу режут». Это набор возможностей и **HTTP-обязанностей**, которых нет в `2024-11-05`:

- нет Streamable HTTP (в `2024-11-05` сетевой транспорт — HTTP+SSE, два endpoint’а);
- нет OAuth-фреймворка (`2025-03-26`);
- нет обязательного заголовка `MCP-Protocol-Version` на последующих HTTP-запросах (`2025-06-18`);
- нет elicitation, resource links, structured output как нормы схемы (`2025-06-18`) — при этом наше тело `tools/call` уже кладёт `structuredContent` (`server.py:1729-1737`);
- нет иконок, CIMD, experimental tasks (`2025-11-25`);
- нет stateless `_meta` / `server/discover` / `Mcp-Method` (`2026-07-28`).

Для **stdio** и tools-only сервера большинство пунктов — опциональные capabilities. Для **сетевого** клиента, который говорит Streamable HTTP ревизии `2025-03-26`+, проблема не в «старой JSON-RPC сессии», а в том, что наша HTTP-обёртка не является тем транспортом.

---

## 2. Нормативные транспорты

### Что входит в спецификацию

Во всех прочитанных ревизиях нормативных транспорта **два**, плюс право на custom.

**`2024-11-05`**, Transports: stdio и **HTTP with SSE**. Клиенты **SHOULD** поддерживать stdio.  
<https://modelcontextprotocol.io/specification/2024-11-05/basic/transports>

**`2025-03-26` и `2025-06-18` и `2025-11-25`**, Transports:

> The protocol currently defines two standard transport mechanisms … 1. stdio … 2. Streamable HTTP. Clients **SHOULD** support stdio whenever possible. … custom transports.

`2025-11-25`: <https://modelcontextprotocol.io/specification/2025-11-25/basic/transports>  
`2025-03-26`: <https://modelcontextprotocol.io/specification/2025-03-26/basic/transports>  
`2025-06-18`: <https://modelcontextprotocol.io/specification/2025-06-18/basic/transports>

**`2026-07-28`**, Overview транспортов: те же два binding — stdio и Streamable HTTP. Custom **MAY**.  
<https://modelcontextprotocol.io/specification/2026-07-28/basic/transports>

### WebSocket — да или нет?

**Нет. WebSocket не является стандартным транспортом MCP ни в одной прочитанной ревизии.** На страницах transports перечислены только stdio, HTTP+SSE (до замены) / Streamable HTTP, и custom. Слова «WebSocket» там нет.

Custom **MAY**, если сохраняются JSON-RPC и lifecycle (`2025-11-25` Custom Transports; `2026-07-28` Custom Transports **MUST** сохранить JSON-RPC, message patterns и per-request metadata).

Сторонние SDK это подтверждают (не спецификация, но снимают двусмысленность): MCP Python SDK v2 удалил WebSocket — «It was never part of the MCP specification» (<https://py.sdk.modelcontextprotocol.io/whats-new/>). TypeScript SDK v2: `WebSocketClientTransport` removed, «WebSocket is not a spec-defined MCP transport».

Оговорка по хостам: Claude Code документирует `type: ws` как **свой** remote-транспорт. Это расширение хоста, не норма MCP. Наш ACP WebSocket (`docs/ACP-TRANSPORTS.md`) — канал к Grok CLI, не MCP.

### Что на практике значит «MCP по сети»

1. **Streamable HTTP** — единственный актуальный стандартный сетевой binding с `2025-03-26`.
2. **HTTP+SSE (`2024-11-05`)** — deprecated с `2025-03-26`; в `2026-07-28` внесён в реестр Deprecated (SEP-2596), окно не меньше 12 месяцев, затем eligible for removal. Changelog `2026-07-28`, раздел Deprecated, пункт 2; Streamable HTTP, раздел «HTTP+SSE Transport (2024-11-05)».
3. **Custom HTTP JSON-RPC** — разрешён спецификацией как custom, но хосты Claude/Cursor/Codex его не конфигурируют: они ждут Streamable HTTP (или устаревший SSE).
4. **FastMCP proxy** — адаптер третьей стороны: локальный stdio к хосту, сзади HTTPS. Не замена спецификации. Наш пример `examples/fastmcp_proxy.py` так и подписан: conceptual.

Наш `http_server.py` ближе к пункту 3, а документация продаёт его как пункт 1 хостам пункта 4 (`docs/install/vps.md:64-70`: «Claude remote MCP», «Cursor (URL MCP)»).

---

## 3. Streamable HTTP — обязанности сервера

Ниже две колонки: **эпоха handshake** (`2025-03-26` … `2025-11-25`, цитаты в основном по `2025-11-25`) и **эпоха `2026-07-28`**. Нормы первой эпохи — то, чего ждут сегодняшние хосты по их docs. Нормы второй — то, что `latest` требует от новой реализации.

### Общее с `2025-03-26`

| Обязанность | Уровень | Ревизия / раздел |
|---|---|---|
| Один MCP endpoint, поддерживающий **POST и GET** | **MUST** | `2025-11-25` Streamable HTTP: «The server **MUST** provide a single HTTP endpoint path … that supports both POST and GET methods.» |
| Сообщения клиента — новый HTTP POST | **MUST** (клиент) | тот же раздел, «Sending Messages to the Server» |
| `Accept: application/json, text/event-stream` | **MUST** (клиент) | там же, п. 2 |
| Тело POST — один JSON-RPC request / notification / response (`2025-06-18+`; batching снят) | **MUST** | `2025-06-18` / `2025-11-25` п. 3 |
| Notification или JSON-RPC response → HTTP **202** без тела | **MUST** | п. 4 |
| Request → `Content-Type: application/json` **или** `text/event-stream` | **MUST**; клиент **MUST** уметь оба | п. 5 |
| GET: `text/event-stream` **или HTTP 405** | **MUST** | «Listening for Messages from the Server» п. 3 |
| Не слать JSON-RPC response на GET-stream, кроме resume | **MUST NOT** | п. 4 |
| Не broadcast одно сообщение на несколько SSE | **MUST NOT** | Multiple Connections п. 2 |
| `MCP-Protocol-Version` на **последующих** запросах после initialize | **MUST** (клиент); сервер при невалидном/неподдерживаемом **MUST 400** | `2025-06-18` и `2025-11-25`, «Protocol Version Header». Появилось в changelog `2025-06-18` п. 8. Если заголовка нет — сервер **SHOULD** считать `2025-03-26` |
| `MCP-Session-Id` в ответе на initialize | **MAY** | Session Management п. 1 |
| Если сессия выдана — клиент **MUST** слать её дальше; сервер без неё **SHOULD** 400 | MUST / SHOULD | п. 2 |
| Терминация сессии → **MUST 404** на этот id | **MUST** | п. 3 |
| Клиент **SHOULD** DELETE для закрытия; сервер **MAY 405** | SHOULD / MAY | п. 5 |
| SSE event `id` / resume через `Last-Event-ID` | **MAY** | Resumability |
| Origin: см. §4 | **MUST** | Security Warning |
| Bind localhost при локальном запуске | **SHOULD** | Security Warning п. 2 |
| Аутентификация | **SHOULD** | Security Warning п. 3 |

`2025-11-25` уточняет 403 на невалидный Origin (changelog, minor п. 3).

### Что изменил `2026-07-28`

Источник: <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http> и changelog.

| Обязанность | Уровень | Комментарий |
|---|---|---|
| Endpoint поддерживает **POST** (GET больше не часть binding) | **MUST** | «The server **MUST** provide a single HTTP endpoint path … that supports POST.» |
| GET/DELETE от старого клиента | **SHOULD 405** | Backward Compatibility, «Earlier Streamable HTTP Revisions» |
| `MCP-Protocol-Version` на **каждом** POST | **MUST** | Request Metadata → Protocol Version Header |
| Заголовок совпадает с `_meta.io.modelcontextprotocol/protocolVersion` | **MUST**; иначе 400 + `HeaderMismatch` `-32020` | там же |
| `Mcp-Method` на всех request; `Mcp-Name` для `tools/call` / `resources/read` / `prompts/get` | **REQUIRED** | Standard Request Headers |
| Неизвестный RPC → **HTTP 404** и JSON-RPC `-32601` | **MUST** | Protocol Version Header |
| Notification → 202 | **MUST** | Sending Messages п. 5 |
| Request → JSON или SSE | **MUST**; клиент **MUST** оба | п. 6 |
| На SSE: **MUST NOT** слать JSON-RPC *request* (это ушло в MRTR) | **MUST NOT** | Receiving Messages |
| Закрытие SSE = cancel | **MUST** трактовать как отмену | Cancellation |
| `Mcp-Session-Id` / GET stream / `Last-Event-ID` | **сняты** | changelog major 1, 4, 9 |
| `initialize` / `notifications/initialized` / `ping` | **сняты** | changelog major 2, 5 |
| `server/discover` | **MUST** | changelog major 3; Versioning |
| Origin / localhost / auth | те же MUST/SHOULD | Security & Endpoint |

Старый HTTP+SSE: new implementations **SHOULD NOT** adopt; existing **SHOULD** migrate.

### Что из этого делает наш сервер (факт кода, не спеки)

Файл `grok_delegate/http_server.py` — 200 строк.

| Маршрут | Код | Probe 2026-08-18 |
|---|---|---|
| `GET /healthz` | `http_server.py:117-118`, без auth | 200 `{"ok":true,"service":"grok-delegate","transport":"http"}` |
| `GET /` | тот же код, **тоже без auth** | 200, то же тело. Docstring (`http_server.py:8`) говорит, что без auth только `/healthz` — **это неверно** |
| `GET /readyz` | `:120-124`, Bearer | 401 без токена (`WWW-Authenticate: Bearer`); 200 с токеном |
| `POST /mcp` и `POST /` | `:128-168` | JSON-RPC |
| `GET /mcp` | падает в 404 `:126` | 404 `{"error":"not_found"}`, не 405 и не SSE |
| `DELETE /mcp`, `OPTIONS /mcp` | нет handler | 501 HTML от `BaseHTTPRequestHandler` |
| SSE | нет | все успешные POST — `Content-Type: application/json; charset=utf-8` |
| `MCP-Session-Id` | нет | в ответе initialize заголовка нет |
| `MCP-Protocol-Version` / `Mcp-Method` | не читаются | probe №8 и №25: заголовки проигнорированы, `ping` при `Mcp-Method: tools/call` → 200 |
| Origin | не проверяется | probe №8: `Origin: https://evil.example` → 200 |

`handle_jsonrpc` (`server.py:1696-1746`) понимает ровно пять имён: `initialize`, `notifications/initialized`, `tools/list`, `tools/call`, `ping`. Нет `resources/*`, `prompts/*`, `logging/*`, `completion/*`, `notifications/cancelled` как метода с семантикой, `server/discover`. Неизвестный request → JSON-RPC `-32601` при **HTTP 200**. Неизвестный notification (нет `id`) → `None` → HTTP 202 (`http_server.py:163-167`). Probe: `resources/list`, `prompts/list`, `logging/setLevel`, `completion/complete`, `server/discover` — `-32601`; `notifications/cancelled` — 202 (глотается как любой notification).

Старт: `python -m grok_delegate.server --transport http --host 127.0.0.1 --port 8765` (`server.py:1944-1980`). Токен: `GROK_DELEGATE_HTTP_TOKEN` или `GROK_DELEGATE_HTTP_TOKEN_FILE`, не оба (`http_server.py:32-37`). Без токена bind не поднимается, включая loopback (`:183-190`). Дефолт хоста — `127.0.0.1` (`server.py:1959`). `0.0.0.0` кодом **не запрещён**, если токен задан. Константа `LOOPBACK_HOSTS` (`http_server.py:24`) нигде не используется.

---

## 4. Безопасность HTTP-транспорта в спецификации

Норма одна и та же с `2024-11-05` (HTTP+SSE, «Security Warning») по `2026-07-28` (Streamable HTTP, «Security & Endpoint»):

1. **MUST** проверять `Origin` на всех входящих соединениях (DNS rebinding).  
   С `2025-11-25`: если заголовок **есть и невалиден** → **MUST 403**. Тело **MAY** быть JSON-RPC error без `id`.  
   Источники: `2024-11-05` Transports HTTP with SSE; `2025-03-26` / `2025-06-18` / `2025-11-25` Streamable HTTP Security Warning; `2026-07-28` Security & Endpoint п. 1.
2. Локально **SHOULD** bind `127.0.0.1`, не `0.0.0.0`.
3. **SHOULD** «proper authentication for all connections».

Это нормы **транспорта**, не OAuth-главы. Authorization (OAuth) — отдельный, опциональный слой (§5).

У нас:

| Норма | Есть? | Где |
|---|---|---|
| Origin → 403 | нет | `http_server.py` не читает `Origin`. Probe №8: 200 |
| Bind localhost | дефолт да; запрета на `0.0.0.0` нет | `server.py:1959`; `create_http_server` принимает любой host |
| Authentication | да, жёстче SHOULD: Bearer **везде** кроме healthz/`/` | `http_server.py:82-96, 133-135, 183-190`; CHANGELOG 0.12.0 |
| `Content-Type: application/json` | да (защита от simple CORS POST) | `:139-142`; 415 на `text/plain` (probe №21) |
| CORS / OPTIONS | нет | OPTIONS → 501 |

Токен — операторский секрет, не OAuth access token. Docstring это прямо запрещает (`http_server.py:9`).

Угроза DNS-rebinding / browser CSRF на loopback — соседний агент (threat model). Здесь только факт: спецификация считает Origin **MUST**, у нас его нет. Частичная компенсация: обязательный Bearer (браузерный simple request не ставит `Authorization`) и отказ `text/plain`. Cross-origin **preflight** с украденным токеном Origin всё равно не режет.

---

## 5. Authorization для удалённых серверов

### Что говорит спецификация

`2025-03-26` вводит OAuth 2.1. `2025-06-18` классифицирует MCP-сервер как Resource Server и требует RFC 9728. `2025-11-25` / `2026-07-28` расширяют discovery (OIDC, CIMD).

Ключ — **OPTIONAL**, затем условные MUST. Цитата `2026-07-28` Authorization, «Protocol Requirements» (<https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization>, прочитано 2026-08-18; то же в `2025-06-18`):

> Authorization is **OPTIONAL** for MCP implementations. When supported:  
> * Implementations using an HTTP-based transport **SHOULD** conform to this specification.  
> * Implementations using an STDIO transport **SHOULD NOT** follow this specification, and instead retrieve credentials from the environment.

Если сервер *является* protected resource server, дальше идут MUST:

- MCP servers **MUST** implement OAuth 2.0 Protected Resource Metadata (RFC 9728).
- Клиенты **MUST** использовать PRM для discovery.
- Authorization servers **MUST** реализовать OAuth 2.1.
- Клиенты **MUST** RFC 8707 `resource` (с `2025-06-18`).
- Access token **MUST** в `Authorization: Bearer`, **MUST NOT** в query.

401 без токена в примерах несёт `WWW-Authenticate: Bearer resource_metadata="https://…/.well-known/oauth-protected-resource"`.

stdio: OAuth **SHOULD NOT**. Наш основной путь — stdio, и это совпадает со спецификацией.

### Что это значит для *этого* моста

Инструменты создают worktree и запускают команды (job execute). Это не «каталог документов», это remote code execution от имени OS-пользователя процесса. Спека Authorization рассчитана на *пользовательский* OAuth к *ресурсу*. Статический операторский Bearer — shared secret на весь процесс.

Следствия:

1. Реализовать полный Resource Server + AS + PRM для моста = поднять IdP ради одного оператора, который и так владеет VPS. Цена высокая, модель не подходит.
2. Оставить Bearer и назвать это «authorization» — формально не соответствие главе Authorization (SHOULD conform, если authorization supported). Мы *поддерживаем* защиту HTTP, но это не OAuth 2.1.
3. Хосты (Claude Code, Codex, Cursor) умеют **статический** `Authorization: Bearer` *и* OAuth-discovery. Наш 401 даёт `WWW-Authenticate: Bearer` **без** `resource_metadata` (probe №7). Claude Code: если задан `headers.Authorization` и сервер его отвергает — **не** падает в OAuth, а считает соединение failed. Если заголовок *не* задан, 401 может запустить OAuth-flow, который у нас не с чем завершить (нет `/.well-known/oauth-protected-resource`).
4. Документация моста это знает: `docs/install/vps.md:11` — «HTTPS + bearer — not OAuth». FastMCP — не спецификация.

Рекомендация по auth: не строить OAuth AS в этом репозитории. Если HTTP останется — требовать, чтобы хост слал заранее заданный Bearer, и не имитировать PRM. Если когда-нибудь понадобится «войти через браузер» — это отдельный продукт с threat model, не conformance-правка.

---

## 6. Таблица разрывов

Оценка стоимости — порядок величины для tools-only сервера на stdlib, не полный SDK. «Не делать» — цена для оператора и хостов.

| Требование | У нас | Цена сделать | Цена не делать |
|---|---|---|---|
| Рекламировать честную handshake-ревизию (`2025-03-26` или `2025-06-18`) и читать `params.protocolVersion` | нет: всегда `2024-11-05` (`server.py:152,1707-1713`) | низкая (часы) | клиент **SHOULD** отключиться, если выкинул `2024-11-05`; потеря поздних опциональных полей |
| Dual-era / `2026-07-28`: `_meta`, `server/discover`, отказ от обязательного initialize | нет; `server/discover` → `-32601` (probe №19) | средняя–высокая (дни) | modern-only клиент не заговорит ни по HTTP, ни по stdio |
| Streamable HTTP POST JSON (один endpoint `/mcp`) | частично: POST `/mcp` и ещё POST `/` | — | alias `/` путает пробы и прокси |
| GET MCP endpoint → SSE или **405** (`2025-03-26`–`2025-11-25`) | **404** JSON (`http_server.py:126`; probe №3–4) | низкая | Claude Code трактует GET 404/405 как «сервер жив, только POST»; спецификация хочет 405; HTTP+SSE-проба не отличит нас от «нет endpoint» |
| DELETE сессии → 405, если сессий нет | **501** HTML (probe №22) | низкая | шум у клиентов, которые закрывают сессию |
| SSE на POST (progress) | нет; только JSON | средняя, если нужен progress; иначе JSON **разрешён** | нет live progress/log на HTTP; для tools-only терпимо |
| `MCP-Session-Id` | нет (для stateless **MAY** не выдавать) | не нужна | нет |
| Resume `Last-Event-ID` | нет (**MAY**; в `2026-07-28` снято) | не нужна | нет |
| `MCP-Protocol-Version` → 400 если чужой | игнорируется | низкая | с `2025-06-18` это MUST на HTTP; dual-era/modern клиент может отвалиться |
| `Mcp-Method` / `Mcp-Name` + HeaderMismatch | игнорируется (probe №25) | низкая для 2026; не нужна до перехода | блокер только для modern HTTP |
| Origin MUST → 403 | нет (probe №8) | низкая | DNS-rebinding; MUST-нарушение на любом HTTP-транспорте с `2024-11-05` |
| Bind только loopback локально | дефолт 127.0.0.1; `0.0.0.0` разрешён с токеном | низкая (запретить без явного флага) | публичный bind = RCE-поверхность |
| Bearer на MCP | есть, включая loopback | сделано в 0.12.0 | — |
| OAuth 2.1 + RFC 9728 PRM | нет; `WWW-Authenticate: Bearer` без metadata | высокая; модель не та | хост без заранее заданного Bearer уйдёт в OAuth и не подключится |
| `resources` / `prompts` / `logging` / `completion` / cancel semantics | нет (`-32601` или 202-глоток) | не нужна для tools-only: capabilities их не обещают | клиент, который *всё равно* зовёт `resources/subscribe` (баг Cursor на форуме) — шум |
| `notifications/cancelled` | глотается как 202 | низкая, если появится SSE | на JSON-only HTTP cancel = обрыв HTTP; наш `tools/call` синхронный |
| Неизвестный method → HTTP 404 (`2026-07-28`) | HTTP 200 + `-32601` | низкая | только modern |
| Документировать HTTP как Streamable HTTP | docs говорят «HTTP MCP» / Claude remote | нужен либо conformance, либо честный ярлык «private JSON-RPC» | хосты ожидают spec transport и получают custom |
| FastMCP как адаптер | пример-заглушка `examples/fastmcp_proxy.py` | средняя (довести до рабочего proxy на stdio хоста) | единственный документированный обход неработоспособен «из коробки» |

---

## 7. Эмпирический probe

### Как поднимали

- Только `127.0.0.1`, порт `18765`.
- Рабочий каталог логов: `%TEMP%\mcp-conf` = `C:\Users\codex\AppData\Local\Temp\mcp-conf`.
- Токен: `mcp-conf-probe-token-not-a-secret` (выдуманный, не секрет продукта).
- Команда: `py -3 -m grok_delegate.server --transport http --host 127.0.0.1 --port 18765` с `GROK_DELEGATE_HTTP_TOKEN`.
- Grok CLI / `grok login` **не требовались**: `initialize` не вызывает ACP. Сервер стартовал.
- PID 8852; после прогона `Stop-Process -Force`; повторный GET `/healthz` — порт закрыт.
- Интерпретатор на машине: Python 3.14.5, `Server: BaseHTTP/0.6 Python/3.14.5`.

Полный дамп (токен уже redact): `%TEMP%\mcp-conf\probe-results.json`. Ниже — то, что сравнивается со спецификацией.

### `initialize`

Запрос (probe №8; токен скрыт):

```http
POST /mcp HTTP/1.1
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2025-11-25
Mcp-Method: initialize
Origin: https://evil.example
Authorization: Bearer <redacted>
Content-Type: application/json

{
  "jsonrpc": "2.0",
  "id": 8,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-11-25",
    "capabilities": {"roots": {"listChanged": true}},
    "clientInfo": {"name": "mcp-conf-probe", "version": "0"}
  }
}
```

Ответ:

```http
HTTP/1.1 200 OK
Content-Type: application/json; charset=utf-8
Content-Length: 165
Cache-Control: no-store

{
  "jsonrpc": "2.0",
  "id": 8,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "grok-delegate", "version": "0.12.0"}
  }
}
```

Тот же `result.protocolVersion` на запросы `2024-11-05` (№9) и `2026-07-28` (№10).

Сравнение с тем, что ждёт клиент:

| Ожидание | Факт |
|---|---|
| Handshake-клиент (`2025-11-25` Lifecycle): если сервер *умеет* запрошенную версию — **MUST** вернуть её | Мы *не* умеем `2025-11-25`, поэтому возврат другой своей версии формально допустим. Мы всегда возвращаем `2024-11-05`, не глядя на запрос |
| Форма `InitializeResult`: `protocolVersion`, `capabilities`, `serverInfo` | совпадает по ключам; нет `instructions`, `title`, иконок (все опциональны) |
| `capabilities.tools` | `{}` — tools есть, `listChanged` не обещан. Честно |
| `Content-Type: application/json` | допустимо (вместо SSE) |
| Клиентский `Accept` с обоими типами | проигнорирован; JSON всё равно валиден |
| `MCP-Session-Id` | нет — для stateless **MAY** |
| Origin invalid → 403 | **не выполнено** |
| `MCP-Protocol-Version` на initialize | в `2025-06-18` заголовок обязателен на *последующих* запросах; на самом initialize клиенты часто уже шлют. Мы не смотрим |
| Modern-клиент `2026-07-28` | `initialize` не должен быть точкой входа; `server/discover` обязателен. Мы отвечаем на initialize как legacy и не знаем discover |

`notifications/initialized` без `id` → **202** с пустым телом (probe №11). Это совпадает с MUST для notification.

Без токена initialize → **401** `{"error":"unauthorized"}` + `WWW-Authenticate: Bearer` (probe №7). Это не JSON-RPC error. Хост с заранее заданным Bearer — ок; хост, который по 401 начинает OAuth, — тупик.

`ping` → `{"jsonrpc":"2.0","id":12,"result":{}}` (probe №12). В `2024-11-05`–`2025-11-25` `ping` есть; в `2026-07-28` снят.

`tools/list` → 23 инструмента, HTTP 200 JSON (probe №13). Имена начинаются с `grok_agent_*` / совместимость `grok_delegate*`. Это MCP tools, не ACP.

### Что реальный MCP-клиент делает дальше

По `2025-11-25` sequence diagram: POST initialize → POST initialized (202) → POST tools/list с `MCP-Protocol-Version` и опционально `MCP-Session-Id` → при необходимости GET SSE.  
Наш сервер переживёт JSON-only ветку этого сценария, **если** клиент примет downgrade до `2024-11-05` и не потребует Origin/version-header/GET-405. Это не доказано на живом Claude/Cursor/Codex.

По `2026-07-28`: клиент шлёт `server/discover` или сразу `tools/list` с `_meta` и `Mcp-Method`. Discover у нас `-32601` при HTTP 200, не 404. Header mismatch не ловится.

---

## Нужен ли сетевой транспорт вообще

Это решение, от которого зависят остальные вложения.

### За сеть

- Задокументированный сценарий: Grok CLI залогинен на VPS, хост (Claude/Cursor) на ноутбуке (`docs/install/vps.md`, `docs/install/fastmcp.md`, `docs/economy.md`). Stdio не пересекает машину без SSH-трюка.
- Несколько хостов к одному `grok login` — соблазнительно (гонки и «один job» — чужая тема; факт продукта: HTTP уже `ThreadingHTTPServer` + семафор `GROK_DELEGATE_HTTP_MAX_INFLIGHT`).
- Контейнер/systemd: процесс слушает порт, TLS снимает Caddy. Так написан `examples/vps.systemd.service` (на файл ссылается vps.md; содержимое unit в этом проходе не перечитывалось построчно).

### Против сети

- Спека: клиенты **SHOULD** поддерживать stdio; это наш реальный потребитель. README: «Host | Claude, Cursor, Codex, … (stdio MCP)». `docs/CODEX-MCP-SETUP.md` — только stdio. Skill `grok-mcp` рассчитан на локальный navigator.
- Инструменты = worktree + команды. Сеть превращает мост в remote execution. 0.12.0 уже заплатил за Bearer на loopback именно поэтому.
- Текущий HTTP **не** Streamable HTTP. Документы зовут его так, код — «Streamable-ish». Довести до `2025-11-25` MUST — небольшая работа (Origin, 405, version header). Довести до OAuth + `2026-07-28` — другая система.
- FastMCP в доках — признание, что хостам нужен *нормальный* MCP-клиентский транспорт, а не наш POST.
- Альтернатива VPS без MCP-HTTP: SSH и stdio на той же машине, где `grok login`. Для одного оператора это дешевле conformance.

### Рекомендация

**Сетевой MCP-транспорт этому мосту как продукту не нужен, пока оператор не гоняет Grok на другой машине, чем хост.** Stdio покрывает Claude Code, Cursor и Codex на рабочей станции. Не вкладываться в полную Streamable HTTP / OAuth / dual-era реализацию «на всякий случай».

Если VPS-сценарий всё же живой: не притворяться Streamable HTTP. Либо (дешевле и ближе к спеке хоста) оставить наш HTTP **частным** JSON-RPC за TLS и поставить перед ним настоящий Streamable HTTP (официальный SDK или доведённый FastMCP), который говорит с хостом, а внутрь ходит с Bearer; либо (если хост должен бить в нас напрямую) закрыть **MUST-дыры handshake-эпохи** за дни — Origin 403, GET/DELETE 405, обработка `MCP-Protocol-Version`, честный `protocolVersion` не ниже `2025-03-26` — и явно написать «Bearer only, не OAuth». Не строить Authorization Server. Не гнаться за `2026-07-28`, пока живой целевой хост не потребует это на *этом* endpoint (официальный Codex всё ещё описывает initialize).

Стоимость рекомендации: ноль строк кода, если VPS не используется (заморозить HTTP в docs как experimental/private). Стоимость «хост напрямую»: порядка нескольких рабочих дней на MUST `2025-11-25` без SSE и без OAuth. Стоимость «как у SaaS MCP»: недели и чужой threat model.

Смежные темы, намеренно не разобранные: сетевой threat model, гонки нескольких клиентов на одном job/lane.

---

## Рекомендация одним абзацем

Stdio — нормативный и достаточный транспорт для заявленных хостов; сетевой MCP нужен только в схеме «Grok на VPS, хост на ноутбуке», и даже там правильнее настоящий Streamable HTTP-прокси, а не доращивание 200-строчного stdlib-сервера до OAuth и `2026-07-28`. Наш HTTP — Bearer JSON-RPC, который честно отвечает `initialize` ревизией `2024-11-05` и ломает MUST Streamable HTTP (Origin, GET 405, version header). Это можно чинить точечно, если прямой URL-MCP кому-то нужен; полный Resource Server этому мосту не подходит, потому что за endpoint стоят worktree и выполнение команд.

## Таблица разрывов (повтор для навигации)

| Требование | У нас | Цена сделать | Цена не делать |
|---|---|---|---|
| Честный handshake (`2025-03-26`/`2025-06-18`) | всегда `2024-11-05` | часы | SHOULD-disconnect у клиента, потеря поздних полей |
| `2026-07-28` / `server/discover` | нет | дни | отказ modern-only клиента |
| GET `/mcp` → 405 или SSE | 404 JSON | часы | расхождение со спекой; хосты часто терпят |
| DELETE → 405 | 501 HTML | часы | шум |
| SSE | нет (JSON можно) | дни, если нужен progress | нет стрима |
| Session / resume | нет (MAY) | не надо | нет |
| `MCP-Protocol-Version` | игнор | часы | MUST с `2025-06-18` на HTTP |
| `Mcp-Method`/`Mcp-Name` | игнор | часы (для 2026) | блокер modern HTTP |
| Origin → 403 | нет | часы | MUST с первой HTTP-ревизии |
| Запрет `0.0.0.0` по умолчанию | нет | часы | публичный RCE-bind |
| Bearer | есть | сделано | — |
| OAuth + PRM | нет | недели, не та модель | OAuth-only хост не подключится |
| resources/prompts/logging/completion | нет, capabilities не врут | не надо | шум у баговых клиентов |
| Документ vs код («HTTP MCP») | расхождение | честный ярлык или proxy | ложные ожидания хостов |
