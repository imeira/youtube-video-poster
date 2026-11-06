# PR44 production composition acceptance

## Security verdict: BLOCKED — no production authorization

The previous localhost-fixture acceptance was not evidence of a production
composition. It allowed `--root` to select a caller-controlled directory whose
configuration supplied the trust-root hash, FAL submit/result URLs, and a
reference image. The fixture also self-attested a fake `license_sha256`.

The corrected CLI has no `--root` option. It derives its root only from the
deployment-provisioned location, rejects the legacy caller URL/root/image
configuration shape, and permits credentials only on fixed HTTPS FAL hosts.
Result URLs are parsed as a documented queue response followed by a single
image URL on an allowlisted FAL media host; arbitrary callback URLs cannot
receive the FAL key.

The requested canary is text-only (`4:3`, `1k`, PNG, no prompt expansion, no
reference image). The existing `Executor` requires an image manifest, so
`--execute` now fails before creating a fence, reservation, provider, or POST.
It does not fabricate a reference image to satisfy that legacy contract.

## Verification

```text
.venv\Scripts\python.exe -m pytest -q \
  tests/unit/test_jaco_flux3_production_composition.py \
  tests/unit/test_jaco_flux3_final_admission.py \
  tests/unit/test_local_process_review.py
16 passed

.venv\Scripts\python.exe -m ruff check \
  src/hybrid/jaco_flux3_production.py \
  tests/unit/test_jaco_flux3_production_composition.py
All checks passed

git diff --check
passed
```

Negative coverage proves `--root` rejection, rejection of legacy
caller-controlled URL fields, and rejection of HTTP, loopback, userinfo,
non-default-port, fragment, and lookalike-host URLs.

## Required before production execution

1. Add a reference-free text-only manifest/admission path, including durable
   intent, shared episode-budget reservation, remote-ID checkpoint, and GET-only
   recovery of `UNKNOWN` submissions.
2. Verify a real parent-provisioned protected policy and immutable user
   authorization provenance. A local JSON file or its self-computed hash is not
   sufficient authority.
3. Implement credible license terms verification from the parent-provisioned
   evidence source; a 64-character placeholder hash is rejected.
4. Verify the current official FAL queue/catalog/OpenAPI response contracts and
   bind a fresh authenticated price/authorization before any paid POST.

No FAL credential, paid call, ledger, or external network request was used in
this review.