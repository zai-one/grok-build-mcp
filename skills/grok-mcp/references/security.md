# Security

- Unofficial bridge — not xAI
- Auth: `grok login` only (local CLI session)
- Never store OAuth/API keys in MCP JSON, env examples, Issues, or skills
- `GROK_AGENT_SECRET` is for optional daemon modes — **not** MCP client config
- Roots: `GROK_DELEGATE_ALLOWED_ROOTS` must be real project paths, not `/`
- HTTP/VPS bearer = operator token ≠ Grok OAuth
- See `SECURITY.md` at repo root
