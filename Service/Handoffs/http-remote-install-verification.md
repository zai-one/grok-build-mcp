# Верификатор: http-remote-install

| | |
|---|---|
| Задача | `.tasks/2026-08-18-http-remote-install.md` |
| Проект | Grok CLI (`zai-one/grok-mcp`) |
| Агент | Cursor Grok 4.6 |
| Baseline SHA | `faab02ec430dd8f9c127741132be986b278a9232` |
| Итоговый SHA | незакоммичено; HEAD всё ещё `faab02ec`; ветка `grok/http-remote-install` |
| Дата | 2026-08-18 |
| **Вердикт** | **GREEN С ОГОВОРКАМИ** |

Оговорки: Linux systemd/Caddy/домен на этой машине не запускались (Windows, нет Docker и WSL). Живой хост (Claude/Cursor) к публичному URL не подключался. В том же рабочем дереве параллельный агент правил handshake HTTP (`ONE_CLIENT_PER_PROCESS`, эхо `protocolVersion`). Коммита нет.

## 1. Что просили и что сделано

Сделать один путь установки моста на удалённой машине без угадывания. Гейт I1 **не** остановил работу: сетевой MCP «не нужен, пока Grok и хост на одной машине», но VPS-сценарий как раз тот случай, где сеть нужна; «сетевой транспорт не делаем» в I1 нет.

Документы больше не продают FastMCP-прокси как установку сервера. Путь: `python -m grok_delegate.server --transport http` на loopback, Bearer обязателен, TLS только на обратном прокси, systemd-юнит для этого процесса. Код отказывается подниматься без токена, отказывается биндить не-loopback без флага, `GET /` больше не открытый health, токен регистрируется в редакторе receipts.

## 2. Утверждение → доказательство

| Утверждение | Доказательство | Прогонов | Размах |
|---|---|---:|---|
| I1 не сказал «сетевой транспорт не делаем» | `Service/Research/2026-08-18-mcp-transport-conformance.md:433`: «не нужен, пока оператор не гоняет Grok на другой машине»; `:435` «Если VPS-сценарий всё же живой» | — | — |
| Без токена процесс не стартует (и на loopback) | `tests/test_http_transport_auth.py::test_starting_without_a_token_is_refused_even_on_loopback`; смоук: сообщение содержит `GROK_DELEGATE_HTTP_TOKEN` | 1 смоук + pytest | — |
| `GET /healthz` открыт, без tools/token/roots | Смоук `Service/Handoffs/http-remote-install-curl-capture.txt`: 200, тело `ok/service/transport/mcp_binding` | 1 | — |
| `GET /mcp` = 405 `Allow: POST`, не 404 | тот же capture | 1 | — |
| `GET /readyz` без Bearer = 401 + `WWW-Authenticate: Bearer` | тот же capture | 1 | — |
| `POST /` больше не JSON-RPC alias | capture: 404 `not_found` | 1 | — |
| Токен не попадает в access-log (query stripped) | pytest `test_access_logs_drop_query_strings_and_never_echo_the_token`; смоук stderr без значения токена | 1 | — |
| Bearer hex редактируется в receipt-тексте | `tests/test_redaction_coverage.py::test_a_registered_http_bearer_is_stripped_even_without_a_key_prefix` | 1 | — |
| Не-loopback без флага — отказ | `test_nonloopback_bind_is_refused_without_the_explicit_flag` | 1 | — |
| Пустой `Content-Type` на POST `/mcp` — 415 | `test_a_post_without_content_type_cannot_reach_the_tools` | 1 | — |
| Полный pytest зелёный | `py -3 -m pytest tests -q`: **753 passed, 1 skipped, 79 subtests**, 286.05 с, exit 0 | 1 полного набора | шумное время, вердикта по секундам нет |

## 3. Что НЕ запускалось и почему

| Проверка | Почему не запускалась | Кто может запустить |
|---|---|---|
| Docker «с нуля» Linux-контейнер | `docker` нет в PATH | оператор на машине с Docker |
| WSL / systemd / `journalctl` | WSL не установлен; хост Windows | Linux VPS |
| Caddy/nginx + Let's Encrypt + DNS | нет домена и Linux-прокси | оператор с hostname |
| `grok login` + живой Claude/Cursor/Codex на этот HTTP URL | жжёт сессию Grok; хост может требовать Streamable HTTP | оператор |
| Bind `0.0.0.0` с флагом | намеренно не биндили публичный plaintext | pytest проверяет только отказ без флага |
| FastMCP `create_proxy` | `examples/fastmcp_proxy.py` — заглушка | тот, кто ставит FastMCP своей версии |
| Скептик (`grok --prompt-file`, без инструментов) | этот проход писал верификатор; вызов скептика — следующий шаг протокола | хост/оператор |

## 4. Что может сломаться

- Хост, который ждал Streamable HTTP GET/SSE на `/mcp`, теперь получает 405 — это честнее, чем 404, но «URL MCP» в Claude/Cursor по-прежнему может не заговорить.
- Второй `initialize` на том же HTTP-процессе — JSON-RPC `ONE_CLIENT_PER_PROCESS` (соседний срез). Рестарт службы обязателен для второго клиента.
- `POST /` больше не принимает JSON-RPC. Старые пробы на корень сломаются; путь только `/mcp`.
- `GROK_DELEGATE_HTTP_ALLOW_NONLOOPBACK=1` — ломающий для тех, кто уже биндил `0.0.0.0` без флага.
- Редактор теперь вырезает точное значение HTTP-токена из receipts. Короткий токен (<8 символов) не регистрируется.
- В рабочем дереве смешаны правки этого прохода и `grok/mcp-protocol-tidy` (handshake). Откатывать HTTP-установку, не глядя на `server.py` `negotiate_protocol_version`, нельзя.

## 5. Вопросы скептику

- Достаточно ли `/healthz` с `mcp_binding`, или «answers nothing» требовало тела ровно `{"ok": true}`?
- Нужно ли запретить не-loopback совсем, а не флагом, учитывая Docker-сеть без Docker на этой машине?
- Документ честно говорит, что Claude remote / Cursor URL могут не подключиться. Это приемлемый VPS-путь, или оператор ждал рабочий Streamable HTTP?
- systemd-юнит не гонялся. Есть ли в нём ложный путь (`LANES_PARENT` убран, TOKEN_FILE `/etc/grok-delegate/token`) относительно 0.12.0 lanes-inside-project?
- Смешение двух веток в одном worktree: можно ли принимать docs/install, не отделяя handshake-коммит?

Критерий приёмки для скептика (одной строкой, идёт в `--criterion`):

> Документы `docs/install/vps.md` и `fastmcp.md` описывают тот HTTP, который есть в коде (Bearer JSON-RPC, не Streamable HTTP), каждый проверяемый на Windows факт подтверждён командой, Linux/TLS помечены как не проверенные, токен не светится в логах и receipts.

## 6. Скептик

| | |
|---|---|
| Вызван | нет |
| Вердикт скептика | не вызывался |

```
не вызывался
```

**Что сделано по находкам:** раздел пуст, пока нет ответа скептика.

## 7. Следы

- строка на доске: `.tasks/BOARD.md`, слаг `http-remote-install`, состояние `в работе` (коммита нет, поэтому не `на проверке`);
- файл промпта удалён: нет;
- временные ветки и worktree: создана `grok/http-remote-install`; сосед переключил дерево на `grok/mcp-protocol-tidy` (тот же SHA `faab02ec`); возвращено на `grok/http-remote-install`; незакоммиченные файлы общие. Push/merge не делались.
- смоук: `Service/Handoffs/http-remote-install-curl-capture.txt` (токен не содержится).
