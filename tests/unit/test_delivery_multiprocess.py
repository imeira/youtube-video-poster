"""An interprocess delivery lock prevents duplicate local transport sends."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
from dataclasses import asdict
from pathlib import Path

from src.approval.receipts import ApprovalReceipt
from src.delivery.controller import DeliveryController


class _LocalTransport:
    def __init__(self, send_log: str):
        self.send_log = send_log

    async def send_photo(self, chat_id, photo_path, caption=""):
        # O_APPEND produces an observable local fake transport receipt across processes.
        fd = os.open(self.send_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, b"send\n")
        finally:
            os.close(fd)
        return 77


def _deliver_in_process(root: str, approval: dict[str, object], result_queue) -> None:
    async def run() -> None:
        receipt = await DeliveryController(_LocalTransport(str(Path(root) / "sends.log"))).deliver_for_approval(
            chat_id="authorized-chat", episode_id="EP8", thumbnail=ApprovalReceipt(**approval),
            receipt_dir=Path(root) / "delivery",
        )
        result_queue.put(receipt["message_id"])

    asyncio.run(run())


def test_two_processes_send_once_with_local_fake_transport(tmp_path: Path):
    artifact = tmp_path / "thumbnail.png"
    artifact.write_bytes(b"thumbnail")
    receipt = ApprovalReceipt.approve("thumbnail", artifact, "delivery-audit")
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    workers = [
        context.Process(target=_deliver_in_process, args=(str(tmp_path), asdict(receipt), results))
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0

    assert sorted(results.get(timeout=2) for _ in workers) == [77, 77]
    assert (tmp_path / "sends.log").read_text(encoding="utf-8").splitlines() == ["send"]
