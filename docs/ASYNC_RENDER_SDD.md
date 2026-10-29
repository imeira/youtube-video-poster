# Async compiled render boundary

## Scope

`DirectorAgent.finalize_compiled_delivery()` runs the complete synchronous
`render_compiled_video()` boundary with `asyncio.to_thread`. The direct
synchronous API remains supported.

## Immutable flight and recovery contract

A flight is keyed by canonical episode ID **and** an immutable
`compiled-render-contract-v1` digest. The contract binds:

- canonical episode root;
- approved-manifest hash;
- approved audio hash;
- ordered image and optional hero-clip hashes;
- renderer geometry, FPS, filter-thread, scene-worker, and encoder-thread settings;
- closing-hold seconds.

A caller for an in-flight episode with a different contract fails closed rather
than attaching to the first render. The worker recomputes the contract after
admission; changed inputs while waiting also fail closed.

Before any encode, Director exclusively writes canonical
`qa/compiled_render_intent.json`. It binds the full contract and digest. The
renderer publishes the new final file, writes its own output receipt, and then
exclusively fsyncs canonical `qa/renderer_completion_receipt.json`, which binds
the intent digest, contract digest, exact output hash, and canonical hash of
the renderer receipt. Only after that may it return to Director.

Intent, renderer completion, and Director receipt reads use the existing strict
JSON byte-snapshot loader, rooted at the lexical canonical episode directory.
Each target and ancestor is rejected if it is a symlink/reparse point before an
open; each accepted parse and hash comes from the same file-handle bytes. Their
exclusive writes apply the same root/reparse guard before creating a file.

Recovery recomputes current source/manifest/audio/scene/renderer bindings,
verifies the intent, completion, renderer-receipt hash, and final-video bytes
under the episode OS lock, then exclusively creates the Director receipt and
atomically persists `FINAL_QA` state without another encode. A fresh-process
`os._exit` test proves recovery only for the boundary after final publication
and renderer-completion persistence but before the Director receipt: it resumes
with the same output hash and exactly one encode. A crash after final publication
but before renderer completion has no durable proof of completed output; it
remains quarantined, fails closed, and must not be claimed recoverable or
re-encoded automatically. An MP4, ffprobe result, legacy receipt, altered
input, or altered output is not completion evidence: it remains quarantined and
requires a successor; recovery never overwrites the final artifact or resets
state.

## Process and cancellation ownership

A crash-released OS lock at `qa/.compiled-render.lock` spans state load,
recovery validation, encode, receipt write, and state persistence for one
canonical episode. Async acquisition is off the event loop because the lock
lives inside the thread-owned synchronous boundary. Caller cancellation is
shielded: it does not release either the in-process flight or the OS lock until
the thread has returned.

## Admission and FFmpeg bounds

The Director has an explicit, fixed **process-local** one-render pool. Each
render acquires one atomic permit, so there is no partial weighted-permit
acquisition or deadlock. This is not a host-wide CPU claim. `LocalRenderer`
continues to bound its own scene workers and encoder threads; every actual
FFmpeg video/AAC encoding command passes its configured encoder thread count,
including final composition and MP4 mux/derived-master work. No quality or
codec setting changes are introduced.

## Evidence

- `tests/unit/test_director_async_render.py` covers nonblocking offload,
  cancellation/retry ownership, changed manifest/hold conflict rejection,
  exact-contract verified reuse, missing completion quarantine, legacy receipt
  rejection, fresh-process exclusive ownership, and a real FFmpeg
  smoke/`FinalRenderQA` path when FFmpeg is installed.
- `tests/unit/test_hybrid_render.py` covers real local rendering and bounded
  renderer configuration.
