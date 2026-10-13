# grok-delegate

[English](README.md) · [Русский](README.ru.md)

**Передайте Grok цикл «написать код → запустить тесты → исправить», а своему основному ассистенту оставьте постановку задачи и проверку результата.**

> MCP-сервер, который передаёт задачи по коду из Claude Code, Cursor или Codex локальному Grok CLI в изолированном git worktree и возвращает проверенную квитанцию (diffstat, ограниченный diff, результаты тестов). Неофициальный.

MCP-мост запускает Grok CLI на вашей машине. Для изменений создаётся отдельное рабочее дерево Git; основной ассистент получает список файлов, ограниченный diff и результаты проверок. Подходит для повторяющихся задач разработки, когда хочется делегировать исполнение и сохранить обозримый результат.

## За что отвечает мост

- Передаёт Grok задачу с ожидаемыми файлами, командами тестов и бюджетом работы.
- Сам запускает проверки и отличает их результаты от утверждений исполнителя.
- Сохраняет задания между перезапусками и ограничивает размер отчёта в контексте основного ассистента.
- Работает через stdio MCP с Claude, Cursor, Codex и другими совместимыми клиентами.
- Позволяет отдельно включать работу для каждого проекта и задавать доступные каталоги.

Это неофициальный проект. Он не связан с xAI, Grok, Anthropic или OpenAI. Мост сам не сливает ветки и не публикует код; результат остаётся для ревью. Команды и файлы доступны в пределах настроек проекта и возможностей установленной Grok CLI.

## Почему этот MCP

- Тесты запускает мост, а не агент; пустые изменения, лишние файлы или красные тесты возвращаются как `blocked` с причиной.
- Хост читает ограниченную квитанцию вместо репозитория, контекст остаётся маленьким.
- 24 инструмента, 2 промпта, ресурс, skills и плагин Claude Code; `uvx` и установщики одной командой для Linux, macOS и Windows.
- Другие Grok MCP на GitHub оборачивают xAI API ([djtelicloud/grok-mcp-server](https://github.com/djtelicloud/grok-mcp-server)) или передают промпт в CLI ([BasisSetVentures/grok-cli-mcp](https://github.com/BasisSetVentures/grok-cli-mcp), 3 инструмента); ни один не изолирует работу в worktree и не проверяет тесты.

## Поддержка и обновления

- Проект активно поддерживает ZAI.ONE: текущая версия 0.34.0; изменения в [CHANGELOG.md](CHANGELOG.md) и [Releases](https://github.com/zai-one/grok-build-mcp/releases).
- Встроенная проверка обновлений: попросите ассистента вызвать `grok_agent_check_update` (публичный GitHub API, без учётных данных, с кэшем; ничего не устанавливается автоматически; отключение: `GROK_DELEGATE_DISABLE_UPDATE_CHECK=1`).
- Следует актуальным практикам MCP: аннотации инструментов (read-only / destructive), `instructions` сервера, промпты и ресурсы, структурированные ошибки с подсказками, секреты не попадают в ответы, бюджет токенов описаний проверяется в тестах.
- CI прогоняет тесты на Linux и Windows для каждого pull request.

## Установка

Нужны Python 3.10+, Git и Grok CLI. Выполните `grok login` под тем же пользователем ОС, который будет запускать MCP. Доступы остаются у CLI: в конфигурацию MCP их переносить не нужно.

Укажите конкретный проект при установке. Windows:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/zai-one/grok-build-mcp/main/scripts/install.ps1))) -Project "$env:USERPROFILE\code\my-project"
```

macOS / Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/zai-one/grok-build-mcp/main/scripts/install.sh \
  | bash -s -- --project "$HOME/code/my-project"
```

Установщик готовит конфигурацию подключения. Пошаговый маршрут, ручная установка и расположение файлов — в [руководстве](docs/EASY.md) и [английском README](README.md#install-one-command). После подключения перезапустите MCP и вызовите `grok_agent_status`.

## Установка плагином или через uvx

```bash
# Claude Code: MCP only
claude mcp add grok-mcp -- uvx --from git+https://github.com/zai-one/grok-build-mcp grok-delegate
# Claude Code: plugin = MCP + grok-mcp skill
/plugin marketplace add zai-one/grok-build-mcp
/plugin install grok-mcp@grok-build-mcp
```

Cursor: [установка в один клик](cursor://anysphere.cursor-deeplink/mcp/install?name=grok-mcp&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJnaXQraHR0cHM6Ly9naXRodWIuY29tL3phaS1vbmUvZ3Jvay1idWlsZC1tY3AiLCJncm9rLWRlbGVnYXRlIl19) или `.cursor/mcp.json`:

```json
{"mcpServers": {"grok-mcp": {"command": "uvx", "args": ["--from", "git+https://github.com/zai-one/grok-build-mcp", "grok-delegate"]}}}
```

Codex `~/.codex/config.toml`:

```toml
[mcp_servers.grok-mcp]
command = "uvx"
args = ["--from", "git+https://github.com/zai-one/grok-build-mcp", "grok-delegate"]
```

Claude Desktop: тот же блок `mcpServers` в `claude_desktop_config.json`. Skill лежит в `skills/grok-mcp/` (копии для Claude Code, Cursor, Codex в `.claude/`, `.cursor/`, `.codex/`, `.agents/`). Проекты разрешаются через roots хоста или `GROK_DELEGATE_ALLOWED_ROOTS`. Сначала `grok login`. Prompts: `delegate_task`, `review_diff`; справка: `grok://guide/index`.

Примеры задач: «Передай Grok: добавь ретраи в HTTP-клиент, тесты должны пройти», «Попроси Grok проверить последнюю задачу», «grok-mcp работает?», «Включи grok-mcp для этого проекта с пресетом cheap», «Проверь обновление grok-mcp».

## Первая задача

> Проверь готовность Grok и настройку моего проекта. Затем предложи небольшую задачу с отдельной веткой и конкретной командой тестов. Покажи результат перед слиянием.

Обычный цикл: `grok_agent_session_begin` → выполнение карточек `grok_agent_session_next` → `grok_agent_session_end`. Проект должен разрешить работу через `.grok-mcp.json`; доступ к каталогу сам по себе не включает задания. [Режимы проекта](README.md#turning-the-bridge-on-for-a-project).

## Обновления

`grok_agent_check_update` сравнивает запущенную версию с [последним релизом на GitHub](https://github.com/zai-one/grok-build-mcp/releases) и возвращает ссылку на заметки к релизу и команду, которую стоит предложить (`grok_agent_update` в клоне, иначе `pip install --upgrade "grok-delegate @ git+…@vX"`). Сам ничего не устанавливает. Делает один анонимный запрос к GitHub API с таймаутом 3 с, результат кэшируется на 24 ч; без сети возвращает `status: unavailable` и не повторяет запрос 30 минут, а `force=true` срабатывает не чаще раза в минуту. Если в кэше есть более новый релиз, `initialize` добавляет однострочную подсказку, а статус — блок `update.release`. Отключить (вместе с `git ls-remote` в статусе): `GROK_DELEGATE_DISABLE_UPDATE_CHECK=1`.

## Документация

[Быстрый старт](docs/EASY.md) · [Переменные окружения](docs/ENVIRONMENT.md) · [Примеры](examples/) · [Безопасность](SECURITY.md) · [История изменений](CHANGELOG.md)

Если проект полезен, поставьте ⭐. Для ошибки или предложения [откройте issue](https://github.com/zai-one/grok-build-mcp/issues), приложив версию и обезличенное воспроизведение. Условия использования — [LicenseRef-ZAI-ONE 2.0](LICENSE); правила участия — [CONTRIBUTING.md](CONTRIBUTING.md).

## Кто делает этот MCP

[ZAI.ONE](https://zai.one) — интернет-агентство: сайты, SEO, реклама и аналитика. Мы также создаём инструменты, которые подключают AI-ассистентов к рабочим задачам. [Напишите в Telegram](https://t.me/zai_one), чтобы обсудить настройку, автоматизацию или внедрение для вашей команды.

## Лицензия, использование и участие

- **Использование:** устанавливайте и используйте этот MCP-сервер свободно для себя и своей организации, в том числе в коммерческой работе. Если он полезен, поставьте, пожалуйста, ⭐ репозиторию — это просьба, а не условие.
- **Участие:** [issues](https://github.com/zai-one/grok-build-mcp/issues/new/choose) и pull request приветствуются; см. [CONTRIBUTING.md](CONTRIBUTING.md). Не публикуйте токены, cookie, сессии и данные аккаунтов.
- **Без письменного разрешения ZAI.ONE запрещено:** копировать код в другие проекты, форкать для распространения, встраивать сервер в другие продукты или предлагать его как конкурирующий сервис.
- Лицензия: [LicenseRef-ZAI-ONE 2.0](LICENSE) (исходный код открыт для просмотра, но это не open source). Сторонние компоненты и история лицензии: [NOTICE](NOTICE).
