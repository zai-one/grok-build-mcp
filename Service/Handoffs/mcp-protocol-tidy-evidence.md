# Evidence: отказ от Streamable HTTP, честный handshake stdio

| | |
|---|---|
| Задача | tidy MCP transport после R1/R2/R3 (2026-08-18) |
| Проект | Grok CLI (`zai-one/grok-mcp`) |
| Агент | Cursor Grok 4.6 (subagent) |
| Baseline SHA | `faab02ec430dd8f9c127741132be986b278a9232` |
| Ветка | `grok/mcp-protocol-tidy` |
| Дата | 2026-08-18 |
| **Вердикт** | **GREEN С ОГОВОРКАМИ** — сетевой spec-транспорт **не** реализован по рекомендации R1; stdio handshake и честные имена сделаны |

## Решение

R1 (`Service/Research/2026-08-18-mcp-transport-conformance.md`, раздел «Нужен ли сетевой транспорт вообще», рекомендация одним абзацем) сказал **не** строить Streamable HTTP / OAuth / dual-era `2026-07-28` для этого моста: stdio достаточен для заявленных хостов; HTTP в коде — Bearer JSON-RPC, не spec.

Цитата:

> Сетевой MCP-транспорт этому мосту как продукту не нужен, пока оператор не гоняет Grok на другой машине, чем хост. … Не вкладываться в полную Streamable HTTP / OAuth / dual-era реализацию «на всякий случай».

R3 (`2026-08-18-multi-client.md`, «Вердикт»): не делать in-process multi-client; один процесс на клиента.

R2 запреты инструментов по сети остаются **документацией**: продукт — stdio. `--transport http` как частный JSON-RPC всё ещё отдаёт тот же `tools/list` держателю Bearer (остаточный риск, не ACL).

## Утверждение → доказательство

| Утверждение | Доказательство | Прогонов | Размах |
|---|---|---:|---|
| `initialize` без версии отдаёт `2025-06-18` | живой клиент: `Service/Handoffs/mcp-protocol-tidy-live-stdio.json` и `mcp-protocol-tidy-live-initialize.json`; pytest `tests/test_mcp_protocol.py` | 1 live + pytest | — |
| Клиент `2024-11-05` получает эхо `2024-11-05` | `test_initialize_echoes_a_supported_handshake_version[2024-11-05]` | pytest | — |
| `2026-07-28` не заявлен; ответ — наша latest `2025-06-18` | `test_modern_era_that_dropped_initialize_is_not_claimed`; parametrize `2026-07-28` | pytest | — |
| Stdio допускает повторный `initialize` (тот же клиент) | `ServerTests.test_handle_jsonrpc_initialize_twice_on_stdio_is_the_same_client` | pytest | — |
| HTTP второй `initialize` — `ONE_CLIENT_PER_PROCESS` | `test_second_http_initialize_is_refused_as_one_process_per_client` | pytest | — |
| HTTP не называется Streamable: `mcp_binding=private-jsonrpc`, GET `/mcp` = 405 | `test_healthz_stays_open_because_it_answers_nothing`; `test_mcp_get_is_method_not_allowed_not_not_found`; curl-capture соседнего прохода | pytest + 1 curl | — |
| Живой MCP-клиент (Inspector, не свой скрипт) сделал handshake и вызвал инструмент | `@modelcontextprotocol/inspector --cli` → `initialize` `protocolVersion=2025-06-18`; `tools/call grok_agent_economy` `ok: true`, `isError: false` | 1 | — |
| Полный набор зелёный и больше, чем до правки | до: collect-only **728**; после: `py -3 -m pytest tests -q` → **753 passed, 1 skipped, 79 subtests**, exit 0, 146.04 с | 1 полного набора | время шумное, вердикта по секундам нет |

## Что НЕ запускалось и почему

| Проверка | Почему не запускалась | Кто может запустить |
|---|---|---|
| Streamable HTTP (SSE GET, session, OAuth, `2026-07-28` discover) | R1 запретил реализовывать | только если сменят продуктовую цель |
| Запреты R2 на отдельные MCP-инструменты по HTTP | нет продуктовой сетевой поверхности spec MCP; HTTP остаётся частным JSON-RPC с тем же `tools/list` | отдельная задача, если HTTP станет поддерживаемым продуктом |
| Живой Claude/Cursor/Codex к HTTP URL | хосты ждут Streamable HTTP; R1 как раз об этом | оператор с осознанным private JSON-RPC |
| Linux systemd / Caddy / публичный DNS | эта машина Windows, без WSL/Docker | Linux VPS |
| Bind `0.0.0.0` с `GROK_DELEGATE_HTTP_ALLOW_NONLOOPBACK=1` | намеренно не слушали публичный plaintext | pytest проверяет отказ без флага |
| Скептик (`grok --prompt-file`, без инструментов) | в этом репозитории нет `scripts/skeptic_prompt.py` | хост/оператор |
| Три полных прогона pytest | один полный прогон 146 с; счётчик тестов не шумит | CI |

## Риски

- Хост, который выкинул handshake-эпоху целиком (`2026-07-28` only), по-прежнему не заговорит: мы не делаем `server/discover`.
- Хост, который шлёт `2025-11-25` и требует именно её, получит `2025-06-18` и **SHOULD** отключиться, если не умеет откат. R1 назвал честными именно `2025-03-26`/`2025-06-18`.
- `--transport http` всё ещё поднимается. Это не spec MCP. Bearer + полный набор инструментов = модель R2 T-1, если токен утёк.
- В том же дереве параллельный проход писал VPS-install docs (`docs/install/vps.md`). Откатывать handshake, не глядя на HTTP 405/loopback, нельзя.

## Вопросы скептику

- Правильно ли latest = `2025-06-18`, а не `2025-11-25` (R1 сказал, что `2025-11-25` мы «не умеем» из‑за HTTP MUST Origin/405)?
- Нужно ли гасить `--transport http` полностью, чтобы «нет сетевой поверхности» было буквальным, а не «не spec»?
- `ONE_CLIENT_PER_PROCESS` ломает HTTP-клиент, который делает initialize дважды на reconnect без рестарта процесса — приемлемо?

Критерий:

> Мост не притворяется Streamable HTTP; `initialize` на stdio согласует handshake-эпоху; живой MCP-клиент подключился по stdio и вызвал инструмент; набор тестов зелёный и больше прежнего.
