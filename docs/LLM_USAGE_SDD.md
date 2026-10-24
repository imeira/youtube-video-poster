# SDD — LLM usage attribution

Persist invocation intent before spawning Codex, then settle status, actual exported tokens and latency in a shared SQLite database. Use generated invocation IDs (not prompt hashes as billing IDs); repeated identical prompts can be separately billed. Store only model, task, episode, status, time, usage and SHA-256 of input. Never store prompt text, CLI output, credentials or raw exceptions.

Codex subscription invocations have `billing_kind=subscription_quota`; monetary USD and quota-unit conversion remain null because tokens alone do not prove either. Usage unavailable on failure remains null, not zero. Preserve input/cached/output/reasoning fields as reported without adding reasoning again to output totals.

Director propagates episode attribution by ContextVar during asynchronous preproduction/production. Context is restored on exit and isolated across concurrent tasks. Router assigns task and metrics path; constructing a provider remains lazy and causes no file/network side effects. A successful fake CLI transport tests wiring without consuming quota. No pricing/paid authority is inferred from telemetry.

Acceptance: intent before transport; successful recorded tokens/model/task/episode; failure persisted without stderr; concurrent calls isolated and stored; no files before first completion; no provider invocation if intent persistence fails. Failure after an invocation remains in STARTED for audit rather than triggering automatic repetition.
