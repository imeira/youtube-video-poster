# Durable worker TEST acceptance report

## Scope and boundary

This report covers only the local TEST worker/status/facade path. It does **not** claim a LIVE provider, billing readback, human approval, production video, publication, TTS, GPU, Telegram, or policy activation.

## Reproducible bounded command

```bash
uv run pytest tests/unit/test_canonical_status_projection.py tests/unit/test_compiled_worker_recovery_integration.py tests/unit/test_hybrid_worker_lifecycle.py -q
```

Observed result on this branch:

```text
........................                                                 [100%]
24 passed in 68.94s (0:01:08)
```

## Verified TEST criteria

| Criterion | Evidence |
|---|---|
| One durable queue owns continuation, not chat | Fresh CLI processes run only `enqueue` then `run --once`; the queue owns `PENDING → WAITING → PENDING → DONE`. |
| Resume after durable provider-ID checkpoint does not resubmit | The compiled fresh-process test records one `*.TEST.png`, resumes after both gates, and asserts the same one output remains. |
| Rolling scene/QA dependency is gated | Existing compiled adapter test path independently runs TEST image QA before Director render; queue resumes only its persisted next action. |
| Tiny local FFmpeg render is real | The test creates a local final MP4 and uses `ffprobe` to require H.264/yuv420p video and AAC audio. It remains TEST fixture media, not a LIVE video. |
| Thumbnail gate exact hash and ordering | A byte-tampered thumbnail is rejected; video approval before thumbnail is rejected; only explicit `--test-approval` wakes the queue. |
| Final-video gate exact hash | A byte-tampered video is rejected before explicit TEST approval; an extra worker pass is `IDLE`. |
| No automatic production approval | The only positive test command carries `--test-approval`; no publication action occurs. |
| Read-only queue status | Status consumes the hash-bound JSON snapshot without opening SQLite and reports only `LOCAL_STATUS_ONLY`. |
| Canonical facade is hash-bound and non-promotable | The facade reopens exact TEST executor receipts, emits only local canonical source records, rejects non-TEST receipts, and labels provider state `TEST_LOCAL_ONLY`. |
| Unknown cost remains unknown | A TEST receipt with `actual_cost=None` and `charged=None` produces `None` ledger/metric cost rather than zero. Tokens and quota are not invented. |
| Actual runner benchmark measures local wall time | `run_actual_runner_benchmark` invokes public runner `start`/`resume` against bounded TEST fake latency, records wall/queue/QA/in-flight/submit evidence, verifies exact-hash resume and fails closed on tamper. The deprecated `benchmark_rolling_vs_barrier` is not scheduler evidence. |

## Remaining not met

- No criterion above is LIVE evidence. In particular, this does not establish real provider delivery/recovery, trusted billing, a production gate mutation, human approval, Telegram delivery, or publication.
- The broader architecture matrix still has independent LIVE policy, Director, budget, provider, and publication work outside this worker/status test scope.
