# PR44 text-to-image admission SDD

## Scope

Add one reference-free `TEXT_TO_IMAGE` admission path to the existing durable
`Executor` and `FinalAdmissionKernel`. It is TEST-only mechanical coverage for
the approved FLUX 3 canary (`blackforestlabs/flux-3/text-to-image`, `0.048`)
and does not activate the production CLI.

## Contract

- `Job.input_kind` is closed: `TEXT_TO_IMAGE` or `IMAGE_EDIT`.
- `TEXT_TO_IMAGE` has no manifest/reference inputs. Its canonical input hash is
  the SHA-256 of the canonical actual payload bytes.
- `IMAGE_EDIT` retains the required verified `Manifest` reference path.
- The prepared executor receipt records input kind and canonical payload hash;
  the durable FAL binding/marker must bind request ID, endpoint, scope,
  `0.048`, input kind, and that hash.
- Recovery after a durable remote ID calls only `recover` (GET-only provider
  contract), never `submit` or a second ledger reservation. No remote ID stays
  quarantined (`SUBMITTING`) and is not retried.

## Non-goals

No LIVE authority, credentials, paid requests, provider-contract research, or
CLI readiness changes. Existing production execution remains fail-closed until
its independent prerequisites are met.
