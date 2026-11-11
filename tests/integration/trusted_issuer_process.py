"""Fresh-process runner used only by the trusted-issuer integration test."""
from __future__ import annotations

import argparse
import asyncio
import os
import time
from decimal import Decimal
from pathlib import Path


class PersistentFakeProvider:
    mode = "TEST"

    def __init__(self, effects: Path, output: Path, behavior: str, *,
                 control: Path | None, provider_release: Path | None) -> None:
        self.effects = effects
        self.output = output
        self.behavior = behavior
        self.control = control
        self.provider_release = provider_release

    def _effect(self, value: str) -> None:
        with self.effects.open("a", encoding="ascii") as stream:
            stream.write(value + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _signal(self, value: str) -> None:
        if self.control is not None:
            _append(self.control, value)

    async def submit(self, job, request_id, checkpoint):
        self._effect("POST")
        checkpoint(provider_id="telegram-fixture-provider-1")
        self._signal("DISPATCHED")
        if self.provider_release is not None:
            _wait_for(self.provider_release, "parent did not release blocked provider")
        if self.behavior == "cancel":
            self._effect("CANCEL")
            raise asyncio.CancelledError()
        raise TimeoutError("lost response after durable provider ID")

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        from src.hybrid.execution import ProviderResult

        assert provider_id == "telegram-fixture-provider-1"
        self._effect("GET")
        self.output.write_bytes(b"recovered fixture image")
        return ProviderResult(self.output, Decimal("0.125"))


def _append(path: Path, value: str) -> None:
    with path.open("a", encoding="ascii") as stream:
        stream.write(value + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _signal_ready(directory: Path, value: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    acknowledgement = directory / value
    try:
        descriptor = os.open(acknowledgement, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"duplicate readiness acknowledgement: {value}") from exc
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(value + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _wait_for(path: Path, message: str) -> None:
    deadline = time.monotonic() + 20
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(message)
        time.sleep(0.01)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("endpoint_file", type=Path)
    parser.add_argument("token_file", type=Path)
    parser.add_argument("effects", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("now", type=int)
    parser.add_argument("--behavior", choices=("timeout", "cancel", "recover"), default="timeout")
    parser.add_argument("--control", type=Path)
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--launch", type=Path)
    parser.add_argument("--start", type=Path)
    parser.add_argument("--role", choices=("first", "second"))
    parser.add_argument("--provider-release", type=Path)
    args = parser.parse_args()
    if args.ready is not None:
        if args.launch is None or args.start is None or args.role is None:
            raise ValueError("ready rendezvous requires launch, start and role")
        _signal_ready(args.ready, "LAUNCHED:" + args.role)
        _wait_for(args.launch, "parent did not release worker initialization")
    from src.hybrid.hermes_issuer_client import HermesIssuerClient
    from src.hybrid.trusted_issuer_admission import JoseTrustedIssuerAdmission

    client = HermesIssuerClient.for_test(
        endpoint_file=args.endpoint_file, token_file=args.token_file
    )
    admission = JoseTrustedIssuerAdmission.for_test_ipc(
        root=args.root,
        client=client,
        provider_factory=lambda: PersistentFakeProvider(
            args.effects, args.output, args.behavior,
            control=args.control, provider_release=args.provider_release,
        ),
        now=lambda: args.now,
    )
    if args.ready is not None:
        _signal_ready(args.ready, "READY:" + args.role)
        _wait_for(args.start, "parent did not release synchronized workers")
        _signal_ready(args.ready, "ATTEMPT:" + args.role)
    try:
        result = asyncio.run(admission.admit("JOSE_GEN37_IDENTITY_FRAME_001"))
    except asyncio.CancelledError:
        return 76
    except TimeoutError:
        return 75
    assert result["status"] == "COMPLETE"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
