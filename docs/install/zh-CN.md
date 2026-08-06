# Install (简体中文)

> Unofficial community project — not xAI / Grok.

只保留简单安装。完整说明见 **[EASY.md](../EASY.md)**。

```bash
curl -fsSL https://raw.githubusercontent.com/zai-one/grok-mcp/main/scripts/install.sh \
  | bash -s -- --project "$HOME/code/my-project"
grok login
```

然后 self-test，并把 `~/.config/grok-mcp/mcp/` 里的 JSON 接到 Claude/Cursor。非官方项目。
