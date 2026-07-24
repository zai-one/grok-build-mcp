# MCP / Grok CLI

Рабочая папка для интеграции **Grok Build CLI** через MCP.

## Что здесь подключено

| Компонент | Как подключён | Файл |
|---|---|---|
| `grok` (MCP-сервер) | project-scope, `npx -y grok-cli-mcp` | `.mcp.json` |
| Infra Ops (плагин `infra-ops@zai` v0.3.2) | project-scope, скиллы + агенты | `.claude/settings.json` |
| `grok_delegate` (свой MCP-сервер) | **код есть, в MCP не подключён** | `grok_delegate/` |

## `grok_delegate` — собственный делегирующий сервер

Python-пакет `grok_delegate/` — dev-only MCP-сервер: принимает кодинг-цель, заводит изолированный
git worktree на ветке `grok/*`, запускает локальный `grok` headless и возвращает ветку + diffstat.
Без auto-merge, без push, без `--always-approve`.

Приёмка (2026-07-24): R1–R5 закрыты, live-smoke подтвердил реальный headless-запуск, тесты 56 зелёных.
Границы и **честный список не-гарантий** — в [EVIDENCE.md](EVIDENCE.md); задание — в
[GOAL-ROUND3.md](GOAL-ROUND3.md).

> В `.mcp.json` он намеренно **не прописан**. Подключение = дать агенту живой инструмент, у которого
> confinement по cwd best-effort (относительный `../` traversal — задокументированная не-гарантия).
> Это отдельное осознанное решение владельца, а не побочный эффект приёмки.

## Авторизация

Ключ **не нужен**. `grok-cli-mcp` переиспользует OAuth-сессию установленного Grok CLI из
`C:\Users\codex\.grok\auth.json` (вход по SuperGrok / X Premium+). Поэтому в `.mcp.json`
нет блока `env` — секретов в репозитории не хранится.

Если сервер перестанет видеть сессию — перелогиниться в обычном терминале (`grok login`),
это обновит `auth.json`.

## Инструменты

От `grok`: `grok_chat` (one-shot), `grok_consult` (многоходовой), `grok_review` (ревью git-диффа,
`format=json` для CI-гейтинга), `grok_challenge` (adversarial-разбор кода).

Известное ограничение: короткие запросы проходят стабильно, объёмные payload-ы
(`grok_challenge` по большому файлу) упирались в таймаут MCP.

От Infra Ops: скиллы/агенты для Proxmox, Windows, SSH, ADB-туннелей, Passbolt.
Транспорт — центральный `zai-mcp` (`https://mcp.zai.one/mcp`), он подключён на уровне
пользователя, поэтому отдельной настройки в проекте не требует.

## Границы

`grok-cli-mcp` — **консультативно-ревьюшный** канал: он не заводит ветки и не правит файлы сам.
Делегирование кодинг-целей с worktree-изоляцией — это `grok_delegate/` (см. выше), и оно
подключается отдельным решением.

Grok также остаётся подключённым глобально в `claude_desktop_config.json` — здешний
project-scope его дублирует, а не заменяет.
