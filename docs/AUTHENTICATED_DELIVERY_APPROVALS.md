# Authenticated delivery approvals

Delivery remains locked across intent persistence, local send, and receipt persistence. If cancellation races lock acquisition, the acquisition thread is awaited and its lock is released before `CancelledError` is propagated.

A delivered thumbnail or video is **not** approved by API arguments, a JSON mapping, or a filesystem path. `ApprovalController` accepts only an `AuthenticatedApprovalSource` reply already verified at the transport edge and requires all of:

- configured approval chat ID and configured sender allowlist;
- a positive reply message ID and exact `reply_to_message_id` matching the persisted delivery receipt;
- sender/chat bindings matching trusted configuration;
- exact approval text derived internally from episode ID and artifact kind;
- current artifact bytes matching the delivered SHA-256.

The verified reply document is persisted inside the immutable approval receipt. `TelegramInboundAdapter` implements `AuthenticatedApprovalSource` with a configuration-bound authenticated journal. `TelegramDirectorService` can compose that adapter with a caller-supplied `DirectorAgent` as an in-process aiohttp route, but it neither starts a server nor configures a Telegram webhook and `DirectorAgent` has no default source. Real approval confirmation therefore remains fail-closed until trusted composition and external hosting are explicitly configured. TEST fakes are only dependency-injected at the boundary and do not enable a LIVE path.

The composed route serializes each authenticated delivery gate between processes across journal claim/revalidation, receipt, state and durable outcome. A replay is acknowledged only after all current bindings revalidate and the expected post-gate state exists; it is not treated as a new approval or transition. Interrupted receipt-to-state work is recovered audibly only after the same checks pass.
