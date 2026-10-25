# Director Telegram Delivery Session SDD

## Purpose

`src.approval.delivery_session.DeliverySessionRunner` closes the operational link
between the existing Director thumbnail and video human gates without promoting
the TEST runner or introducing an autonomous loop.

The real path remains:

```text
Director WAITING_THUMBNAIL_APPROVAL
  -> existing DeliveryController sends thumbnail and persists receipt
  -> TelegramDirectorService authenticates exact human reply and transitions Director
  -> hash-bound `video_delivery_outbox` eligibility
  -> explicit `studio telegram-delivery-session drain` consumes at most one row
  -> existing Director.deliver_for_approval / DeliveryController sends video
  -> authenticated video reply transitions to WAITING_FINAL_APPROVAL
```

## Durable boundary

After a valid thumbnail approval has been persisted and the Director transition
has completed, `TelegramDirectorService` writes the authenticated outcome and
one idempotent outbox row in the **same SQLite transaction**. The row is bound
to the episode, canonical thumbnail path, and thumbnail SHA-256. A process
crash cannot leave a committed outcome without its video eligibility. Startup
also repairs legacy rows only after validating their outcome MAC, fixed delivery
binding, current state, and the live authenticated inbound reply. This is local
SQLite state only; it is not a POST callback and no messenger is called from
that journal/transition work.

`DeliverySessionRunner.drain_once()` takes a crash-released local lock, reads
one `PENDING` row, confirms its canonical path/hash, and revalidates the
consumed thumbnail reply through the configured `inbound_source` immediately
before the Director's video-delivery path. A tampered journal, configured
binding, receipt, approval, or hash blocks before a video POST. It marks
`DELIVERED` only after the returned receipt is for the video gate. A crash after
the existing delivery receipt but before the outbox update safely reruns the
Director path; `DeliveryController` recovers the same receipt and never resends
media. Altered/missing thumbnail bytes block the row before any send.

## Operator surfaces

- `studio telegram-delivery-session preflight --registry <absolute.sqlite3>` is
  configuration-only and does **not open or query SQLite**. It reports
  `queue: NOT_INSPECTED` and does not create/alter the database, WAL, or SHM,
  construct transport, read secrets, bind a socket, send, poll, or mutate state.
- `studio telegram-delivery-session drain ...` is the sole explicit delivery
  operation. It consumes one already authenticated, hash-bound outbox row; it
  exposes no caller-selected thumbnail argument, so the video path comes only
  from the trusted thumbnail delivery record and revalidated approval.

## Non-goals

No fake `LIVE` label is added to autonomous TEST code. There is no approve,
review-bypass, budget, provider, audio, or publication command. Human decisions
remain authenticated, artifact-hash-bound, and independent for thumbnail and
video.

## Offline verification

Focused tests use concrete local SQLite and fake HTTP-shaped transport boundaries
to prove: durable second-gate eligibility, read-only preflight, fresh-process
outbox resume, hash mutation block, duplicate/replay idempotence, authenticated
wrong sender/chat/reply/hash rejection, crash recovery without resend, and no
autopublish. The tests do not activate a network service or Telegram account.
