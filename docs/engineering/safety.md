# MCP safety

Read for authorization, secrets, money, writes, retries or concurrent/durable state.

- Derive principal/account/environment policy on the server. Caller arguments, `confirm=true`, annotations, visibility and an agent's approval command are not proof of human authority. Preserve the provider's established policy and existing explicit user authorization.
- Validate target identity before execution, including numeric/name aliases, batches and account-wide metadata. Lazy enablement cannot restore forbidden tools. New operations must pass the same controls as existing ones.
- Bind approvals and idempotency to normalized request, principal, provider account and sandbox/production environment. Consume once under cross-process-safe locking or a transaction. Reserve paid budget atomically before dispatch; an unknown result retains its reservation.
- Classify effects by behavior, not GET/POST or a read-only label. Use exact money representations. Respect provider/global quotas, bounded concurrency, timeout and retry limits.
- Distinguish failed-before-send, submitted, applied and unknown. Cancellation/restart must preserve durable state. Never blindly retry a possibly completed payment/write; reconcile it. A failed readback must not report an applied write as unsent.
- Keep secrets in their approved custody. Redact logs, URLs, exceptions and untrusted response echoes. Never commit sessions, credentials, live message/contact data or private operational evidence. Do not load arbitrary parent-directory `.env` files or expose an unrestricted URL/method/path proxy.
- Add applicable negative and concurrency regression tests: wrong account/target, expired or replayed approval, simultaneous consume, spent budget, read-only bypass, interrupted operation and restart.
- Browser operations require the correct authorized account/profile and owned tabs. Keep machine-specific browser settings in ignored workspace notes; preserve stricter local restrictions.

Work within the user's task and existing permissions. Clarify the exact target/effect for ambiguous or destructive actions; do not introduce duplicate approval questions for already authorized routine work. Infrastructure deployment and credential changes keep their existing operator gates.
