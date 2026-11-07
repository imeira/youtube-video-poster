# PR53 durable-resume acceptance record

This committed-repository artifact records the hermetic acceptance evidence for
the protected TEST composition.  It is not a claim that full CI ran or passed.

## Command

```powershell
python -m pytest tests/unit/test_jaco_style_remediation_operation.py tests/unit/test_bounded_operation_admission.py -q
```

The command was run on this worktree and completed with `28 passed` (the test
environment also emitted its existing pytest deprecation warning).

## Durable counter artifact

The test writes its process-shared counter at this fixture-relative path:

```text
<pytest tmp_path>/fixture-counts.json
```

Schema (`pr53-durable-resume-counter/v1` by this document):

```json
{"factories": "integer", "posts": "integer", "gets": "integer"}
```

`factories` counts fresh quote/OpenAPI input-factory calls, `posts` counts
provider submits, and `gets` counts provider recovery/readback calls.  The
same test queries `<pytest tmp_path>/ledger/standing-budget.sqlite3` for the
durable claim count.

## Observed initial and resume counters

Initial crash-after-checkpoint process:

```json
{"factories": 1, "posts": 1, "gets": 0}
```

The durable ledger claim count is `1`.

Two concurrent fresh-process resumes from that checkpoint:

```json
{"factories": 1, "posts": 1, "gets": 1}
```

The durable ledger claim count remains `1`.  Thus resume added zero input
factory calls, zero reservations, zero POSTs, and exactly one provider GET.
The companion unknown-pre-checkpoint case remains quarantined with
`{"factories": 1, "posts": 1, "gets": 0}` rather than retrying dispatch.
