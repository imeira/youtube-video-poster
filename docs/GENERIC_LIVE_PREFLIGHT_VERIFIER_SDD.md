# SDD — bounded generic preflight verifier

## Current boundary

`GenericLivePreflightVerifier.production()` consults the repository-owned generic binding registry before any protected-root access. The registry is intentionally empty. Therefore every generic production request fails as `generic canonical binding is not provisioned` before opening a production file, SQLite database, credential, provider, reservation, or network client.

`GenericLivePreflightVerifier.for_test()` is a hermetic TEST fixture reader. Its successful result is structural TEST evidence only: `authority: NONE` and `dispatch_permitted: false`. It cannot make TEST evidence LIVE authority.

## Offline canonical fixture verifier

`canonical_operation_binding.verify_canonical_operation_binding()` is explicitly **TEST-only**. It accepts only `TEST_OFFLINE_FROZEN` bindings that pin SHA-256 hashes for the transcript and ledger fixture byte payloads. It rejects every other mode before any source path inspection or open. It uses a byte payload to deserialize SQLite in memory and never lets SQLite open the fixture path.

This is not a live-source capture, canonical issuer, producer, snapshot contract, or authorization verifier. A hash establishes fixture byte integrity only. It provides no live consistency guarantee: transient or persistent SQLite WAL state and an unauthorized writer are outside this boundary. A future LIVE design requires a separately reviewed, source-owned atomic snapshot producer and independently authenticated issuer receipt; neither is implemented.

For frozen fixture file reads, the verifier fails closed on unsupported platforms. POSIX uses an `O_DIRECTORY|O_NOFOLLOW` dirfd chain and relative final open. Windows opens each ancestor with `FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_BACKUP_SEMANTICS`, verifies no reparse point, and holds handles without `FILE_SHARE_DELETE` through final safe-handle validation and read. `OPEN_REPARSE_POINT` on the final file alone is not treated as ancestor protection.

## Production status

No generic LIVE authority, canonical issuer/producer, database capture, dispatch, provider call, credential access, reservation, or claim exists in this change. Production remains blocked.

## Acceptance evidence

Offline tests cover an unbound fixture rejection before `os.open`, TEST evidence labeling, present and committed WAL rejection, fixture hash pins, and a native Windows `mklink /J` ancestor rejection. The latter permits a sharing violation as a safe denial; it must never read redirected target bytes.
