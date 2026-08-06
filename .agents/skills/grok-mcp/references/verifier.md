# Verifier mode

**Goal:** confirm work without another full coding loop.

## Checks

1. `grok_agent_status` / poll existing job_id
2. Summarize: outcome, changed_files, tests
3. Optionally `grok_agent_review` on a narrow question
4. Local: run the project's tests if appropriate (host or shell)

## Pass / fail

| Pass | Fail |
|---|---|
| Tests green or accepted gaps listed | Broken tests, empty diff, auth/root errors |
| Receipt matches user request | Scope creep without ask |

On fail: either one more **tight** execute, or escalate to human — do not thrash.
