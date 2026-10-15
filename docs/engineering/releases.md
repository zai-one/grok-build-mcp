# MCP releases and integration

Read for publication, upstream changes, platform integration or live verification.

- Run the applicable existing verification and installation checks on the exact revision. Keep versions, CHANGELOG, plugin/server metadata, install pins, tags and licenses consistent. Build artifacts from the tagged commit and preserve provenance/checksums.
- Publishing a release does not update the central platform. Record upstream repository/tag/full SHA, reviewed capabilities and exclusions, platform commit and regression evidence separately from deployed image/revision.
- An independently maintained platform adapter needs an explicit integration decision. Do not blindly overwrite its authorization, account bindings, budgets, idempotency or PostgreSQL state with public adapter code.
- A candidate is pending until review and applicable offline tests pass. Mark integrated only for an immutable platform revision with checked evidence. Keep deliberately excluded features visible with a reason and owner.
- Before rollout record exact image/digest, target, existing approval scope, rollback and canary checks. After rollout collect real container image/revision and health evidence; a Git tag or successful build is not a runtime receipt.
- Scan configured official GitHub releases on events or a periodic schedule, generate/update one candidate per release, and retain unresolved items until a decision. Failed metadata retrieval is unknown, not up to date. Keep tokens in the existing credential store; never create credentials just to make a monitor pass.
- Standard updates have a separate lifecycle: update the shared canon, bump VERSION, test, run its synchronizer, then review/commit target diffs. The generated block is owned by the synchronizer; local rules outside it survive. `--check` detects local drift without fetching the network.

The public checkout contains its own guides and checker; it does not require a private repository or network access at startup. Follow the downstream service's own integration documentation when applicable. Keep private workspace notes, machine paths and operational receipts out of release commits; ignoring a tracked file alone does not untrack it or remove it from earlier commits.
