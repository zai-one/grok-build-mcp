# Короткий запуск Round 8

Рабочая папка: `<REPO_PATH>`.

Прочитай целиком `GOAL-ROUND8-ACP-MCP-BRIDGE.md` и выполни его как терминальную
инженерную цель, а не как просьбу написать план. Нужен единый MCP-интерфейс над
тремя отдельно проверяемыми backend: обычный существующий Grok CLI/headless,
ACP stdio и ACP WebSocket. Сначала почини и сохрани обычный режим, затем доведи
ACP stdio до реального execute с непустым diff и тестом, затем добавь loopback
WebSocket с secret и live smoke.

Не останавливайся на архитектуре и не называй `no_changes` успехом. Работай в
изолированной ветке/worktree, не меняй глобальные конфиги, не push/merge и не
выводи secrets. После каждой фазы запускай тесты и сохраняй evidence. Итогом
дай матрицу verdict для `legacy consult`, `legacy execute`, `ACP stdio`,
`ACP WebSocket`; внешний Codex должен суметь независимо повторить проверки.
