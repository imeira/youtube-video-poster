"""The convenience API cannot bypass the pre-production human gate."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.agents.director import DirectorAgent


@pytest.mark.asyncio
async def test_run_full_pipeline_stops_at_preproduction_approval_gate():
    director = DirectorAgent.__new__(DirectorAgent)
    director.start_episode = AsyncMock(
        return_value={"episode_id": "EP8", "state": "WAITING_PLAN_APPROVAL"}
    )
    director.continue_after_approval = AsyncMock()

    result = await director.run_full_pipeline("Abraão — Gênesis 15–18", episode_id="EP8")

    assert result["state"] == "WAITING_PLAN_APPROVAL"
    director.continue_after_approval.assert_not_awaited()
