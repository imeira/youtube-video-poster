import json
import sqlite3

import pytest


@pytest.mark.asyncio
async def test_codex_transport_persists_usage_without_prompt_or_fake_money(
    tmp_path, monkeypatch
):
    from src.providers.llm.codex_provider import CodexLLMProvider
    from src.providers.llm.usage import usage_scope

    path = tmp_path / "usage.sqlite3"
    provider = CodexLLMProvider(usage_path=path, task="script")
    monkeypatch.setattr(provider, "available", lambda: True)
    monkeypatch.setattr(provider, "execution_command", lambda: ["codex", "-m", "test"])

    class Process:
        returncode = 0

        async def communicate(self):
            events = [
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 3,
                        "output_tokens": 5,
                    },
                },
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "safe-result"},
                },
            ]
            return "\n".join(json.dumps(e) for e in events).encode(), b""

    async def spawn(*args, **kwargs):
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT status FROM calls").fetchone()[0] == "STARTED"
        return Process()

    monkeypatch.setattr(
        "src.providers.llm.codex_provider.asyncio.create_subprocess_exec", spawn
    )
    assert not path.exists()
    with usage_scope("episode-test"):
        assert await provider.complete("PRIVATE PROMPT DO NOT PERSIST") == "safe-result"
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM calls")]
    assert len(rows) == 1
    row = rows[0]
    assert row["episode_id"] == "episode-test" and row["task"] == "script"
    assert row["status"] == "COMPLETE" and row["billing_kind"] == "subscription_quota"
    assert row["money_usd"] is None and row["quota_units"] is None
    assert json.loads(row["usage_json"])["input_tokens"] == 10
    assert "PRIVATE PROMPT" not in str(rows)


def test_director_default_routes_wire_lazy_shared_usage_store():
    from src.agents.director import DirectorAgent

    director = DirectorAgent()
    provider = director.script._llm
    assert provider.usage_store is not None
    assert (
        provider.usage_store.path
        == director.config.episodes_dir.parent / "usage.sqlite3"
    )
    assert provider.task == "script"


@pytest.mark.asyncio
async def test_usage_context_isolated_between_concurrent_episodes(tmp_path):
    import asyncio

    from src.providers.llm.usage import UsageStore, track_episode_usage

    store = UsageStore(tmp_path / "calls.sqlite3")

    class Producer:
        @track_episode_usage
        async def run(self, episode_id):
            await asyncio.sleep(0)
            return store.begin(model="test-model", task="test", prompt="not-persisted")

    await asyncio.gather(Producer().run("ep1"), Producer().run("ep2"))
    with sqlite3.connect(store.path) as db:
        assert {row[0] for row in db.execute("SELECT episode_id FROM calls")} == {
            "ep1",
            "ep2",
        }
