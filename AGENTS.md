# AGENTS.md — grok-delegate / unofficial Grok MCP

Неофициальный мост. Не xAI / Grok. Цель: **Grok = дешёвый worker**, хост (Claude / Cursor / Codex) = **оркестратор и verifier**.

Текущий цикл: unpin CLI; валидные карточки `session_next`; bounded evidence pack; правила агентов; multi-version без пина.

## Typed tools vs navigator

| Путь | Когда |
|---|---|
| Navigator: `session_begin` → loop `session_next` → `session_end` | Дешёвый цикл. Карточка `grok_agent_execute` = полный `task`; `grok_agent_poll` = только `{job_id}`. |
| Typed: consult → execute → poll → review | Fallback, если карточка отвергнута / schema error / хост без navigator. |

Хост не скармливает весь репозиторий в контекст. Читает compact receipt: `changed_files`, `diffstat`, bounded `unified_diff`, `tests`, `worktree_path`.

## Экономика и worktree

- Один job за раз. `max_turns` 8–16, `reasoning_effort` low|medium.
- Worktree / ветка только `grok/*`. **Никогда** `git push` / `merge` из моста — человек ревьюит и мержит.
- Секреты не в receipts, не в MCP config, не в issue. Auth = локальный `grok login`.
- Pin версии CLI **выключен**. Любая установленная CLI с ACP v1. Opt-in: `GROK_DELEGATE_EXPECTED_AGENT_VERSION`. Mismatch = warning, typed-путь не блокируется.

## Как вызывать

1. `grok_agent_status` один раз за сессию (версия моста, CLI, unpin, `update_hint`).
2. `grok_agent_session_begin` (`goal`, `host_budget=small`; лучше сразу `project_root`, `expected_artifacts`, `test_commands`).
3. Loop `grok_agent_session_next` — исполнять только `card`.
4. Если execute/poll карточка ломается → typed tools с тем же пакетом.
5. `session_end`. Человек мержит `grok/*`.

Обновление для consuming agents: если `compatibility.bridge_version` / `grok_delegate_version` старше чекаута — `git pull`, `pip install -e .`, **перезапуск MCP**. Фонового updater нет. Контракт = ACP v1, не номер Grok CLI.

## Когда ломается

| Симптом | Что делать |
|---|---|
| CLI missing | Поставить Grok CLI, `grok login`, снова `grok_agent_status`. |
| auth absent | Тот же OS-user: `grok login`. Мост не читает `auth.json`. |
| ACP handshake / timeout | `grok_delegate_doctor` (без `fix`). Stdio по умолчанию. Не пиннить CLI. Live initialize на текущей CLI — `scripts/capture_acp_initialize.py`. |
| tests red | Не пушить. Чинить, `py -3 -m pytest tests -q`. |
| `session_next` schema / `additionalProperties` | Execute должен нести `task` (`objective`, `project_root`, `correlation_id`, `expected_artifacts`, `test_commands`). Poll — только `job_id`, **без** `session_id`. Fallback: typed tools. |
| Cursor vs Claude vs Codex | Один skill `grok-mcp` в `.cursor`, `.claude`, `.codex`, `.agents`. Источник: `skills/grok-mcp`. После правки: `py -3 scripts/sync_skills.py`. |

## Как коммитить

- Только по просьбе оператора **или** когда этот репозиторий явно разрешил commit+push (этот цикл — да, если тесты зелёные).
- Стиль: `fix(session): …` / `feat(receipt): …` / `docs: …` — зачем, не что. 1–2 предложения.
- Не коммитить `.env`, cookies, tokens, raw ACP secrets, `GROK_AGENT_SECRET`.
- Не `git config`. Не `--no-verify`. Не force-push. Не pin Grok CLI.
- Windows: `git commit -m "msg"` (без bash HEREDOC).
- Тесты: `py -3 -m pytest tests -q`. Красное дерево не пушить.

## GitHub Issue (чтобы следующий агент мог сделать)

Поля: symptom; host (Cursor/Claude/Codex); grok CLI `grok --version`; bridge `SERVER_VERSION` / `grok_agent_status`; excerpt `grok_delegate_doctor` (без секретов); expected vs actual; redacted fixture/logs; repro. Шаблоны: `.github/ISSUE_TEMPLATE/`. Не создавать issue с секретами.

## Релиз-тег

Зелёный набор → bump `pyproject.toml` + `grok_delegate/guard.py` `SERVER_VERSION` + CHANGELOG → `git tag vX.Y.Z` → `git push origin vX.Y.Z` → `gh release create vX.Y.Z`. Не тегировать красное дерево. Semver: ломающий MCP schema = minor/major + запись в changelog.

Контракт совместимости: ACP protocol integer `1`. Мост поддерживает несколько CLI тем, что **не пинит** `agentVersion`.
