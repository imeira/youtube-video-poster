# Paired Hermes PR6 check

Public Studio CI validates Studio in isolation. It does not and cannot check out
the private Hermes repository, and it does not claim cross-repository coverage.

The paired trusted-issuer check belongs in the private Hermes PR6 environment.
From this Studio worktree, with the reviewed private PR6 checkout supplied as an
absolute fixture path, run:

```powershell
$env:HERMES_PAIRED_ROOT = 'C:\absolute\path\to\reviewed\hermes-pr6'
python scripts/run_offline_tests.py tests/integration/test_trusted_issuer_admission.py --tb=short -q
```

The integration test verifies the exact reviewed Hermes commit before importing
the private fixture code. The offline runner blocks external network access and
any access under `C:\HermesStudio\production`; all generated state stays under
pytest fixture roots. Unset `HERMES_PAIRED_ROOT` after the manual check.
