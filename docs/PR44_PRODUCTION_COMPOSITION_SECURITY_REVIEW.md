# PR44 production composition independent security review

- Reviewed: 2026-11-06T15:39:23Z
- Verdict: **BLOCKED — not ready for production execution**
- Scope: `src/hybrid/jaco_flux3_production.py`, its focused test, and the six
  pre-existing dirty files left unmodified by this review.

## Corrected critical findings

1. The CLI accepted a caller-selected `--root`; that directory chose the
   trust-root pathname/hash and therefore could self-authorize a mandate.
2. The same configuration supplied submit/result URLs, enabling SSRF and FAL
   credential exfiltration to a loopback or attacker host.
3. The fixture used `license_sha256 = 'b' * 64` and a localhost POST as LIVE
   authority.
4. A text-only canary was forced through an unrelated `reference_image` field.

The corrected implementation removes CLI root selection and all configured
provider URLs/image inputs. It blocks execution before side effects until the
text-only admission contract exists.

## Remaining blockers

- Parent-provisioned root protection and authorization provenance are not yet
  implemented as an immutable authenticated artifact.
- License evidence has no credible terms-verification implementation.
- The fixed endpoint constants and response parser have not been verified
  against current official FAL documentation or a non-fixture response.
- Shared episode-ledger reuse, remote-ID recovery, and `UNKNOWN` timeout
  reconciliation cannot be exercised while the text-only executor path is
  absent.

## Evidence

- Aggregate SHA-256 of the reviewed source and focused test (ordered
  `sha256sum` records):
  `1d2fc0e5292901ae17783520a929a8fc2217703d21efc77300bfaf48b98db9b1`
- Focused tests: `16 passed` (pytest command recorded in acceptance document).
- Ruff and `git diff --check`: passed.
- SHA-256 before documentation rewrite:
  - `src/hybrid/jaco_flux3_production.py`:
    `54bd9e494adc3522bdc2912e8b2f4842ccb64da22bebd2b4ef7c4250c50b09f5`
  - `tests/unit/test_jaco_flux3_production_composition.py`:
    `40bafdf169bdbdb652a1a851ea47df3ce29d2810f47beedb4bd87ebe4d8afa56`

No commit was made: the acceptance criteria for a production composition are
not met. No push was attempted.