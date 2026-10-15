# MCP contracts

Read for changes to tools, schemas, transport, output, discovery or packaging.

- Compare public names, input schemas, annotations and default toolsets against the current contract. New optional behavior must not silently enable new write/paid permissions for existing configurations, including `all` presets.
- Exercise changes through the MCP boundary, not only helper calls. Cover permitted and denied principals/accounts/targets, aliases and batch items. Tool annotations and hidden tools are not enforcement.
- Test each supported stdio/HTTP mode. Per-session discovery must remain isolated; stateless HTTP needs a tested strategy or eager fallback. Do not advertise client support without evidence.
- Preserve date range, timezone, units, filters, returned/total counts and continuation in compact/matrix output. Test more than one page and explicit truncation. Keep responses and timeouts bounded.
- Measure the UTF-8 bytes of actual `tools/list` plus server instructions per preset and principal. Track token estimates separately with the tokenizer named; characters/4 is only an estimate.
- Keep startup files short. A Claude `@path` import is eager; ordinary links are read by the agent when relevant. Do not import every guide or print them from startup hooks. Preserve indispensable safety rules in AGENTS.md.
- Build and install outside the checkout; confirm entrypoints, packaged guides and skills remain available. Tests must not use live accounts, credentials or paid APIs.
- Keep static router reachability tests. For behavioral claims, use separate held-out agent scenarios with a named model, budget and observed outcomes; do not call keyword overlap a model evaluation.

Run `python scripts/check_engineering.py --check` and the repository's applicable existing checks. Intentional behavior changes need regression coverage and a migration note; documentation-only changes do not need unrelated live tests.
