"""Fresh-process runner used only by the trusted-issuer integration test."""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
from pathlib import Path

from src.hybrid.execution import ProviderResult
from src.hybrid.hermes_issuer_client import HermesIssuerClient
from src.hybrid.trusted_issuer_admission import JoseTrustedIssuerAdmission


class PersistentFakeProvider:
    mode = "TEST"

    def __init__(self, effects: Path, output: Path) -> None:
        self.effects = effects
        self.output = output

    def _effect(self, value: str) -> None:
        with self.effects.open("a", encoding="ascii") as stream:
            stream.write(value + "\n")

    async def submit(self, job, request_id, checkpoint):
        self._effect("POST")
        checkpoint(provider_id="telegram-fixture-provider-1")
        raise TimeoutError("lost response after durable provider ID")

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        assert provider_id == "telegram-fixture-provider-1"
        self._effect("GET")
        self.output.write_bytes(b"recovered fixture image")
        return ProviderResult(self.output, Decimal("0.125"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("endpoint_file", type=Path)
    parser.add_argument("token_file", type=Path)
    parser.add_argument("effects", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("now", type=int)
    args = parser.parse_args()
    client = HermesIssuerClient.for_test(
        endpoint_file=args.endpoint_file, token_file=args.token_file
    )
    admission = JoseTrustedIssuerAdmission.for_test_ipc(
        root=args.root,
        client=client,
        provider_factory=lambda: PersistentFakeProvider(args.effects, args.output),
        now=lambda: args.now,
    )
    try:
        result = asyncio.run(admission.admit("JOSE_GEN37_IDENTITY_FRAME_001"))
    except TimeoutError:
        return 75
    assert result["status"] == "COMPLETE"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
