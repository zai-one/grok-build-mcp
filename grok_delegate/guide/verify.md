# Verify

`session_begin` intent=verify → tick/poll only → `session_end`.  
Fail → one tight re-execute or human.

## Routines — checking the bridge itself

Not for verifying a job. For verifying grok-mcp: one routine, one promise, one
verdict read off a receipt.

```
py -3 scripts/routines.py --list           # the catalogue
py -3 scripts/routines.py --harness-only   # seconds, no worker
py -3 scripts/routines.py --dimension security
py -3 scripts/routines.py                  # everything, tens of minutes
```

Findings land in `Service/Audits/routines-<stamp>.json`; each row carries the
receipt slice that decided it and a `repro` command. Grok finds, the host
reproduces and fixes. An agent's report is not evidence.
