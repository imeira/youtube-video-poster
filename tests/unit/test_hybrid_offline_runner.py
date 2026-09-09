from scripts import run_offline_tests


def test_protected_episode_path_normalizes_relative_parent_segments(
    tmp_path, monkeypatch
):
    protected = tmp_path / "HermesStudio" / "episodes"
    worktree = tmp_path / "HermesStudio" / "worktrees" / "hybrid-fast-ep8-approval"
    protected.mkdir(parents=True)
    worktree.mkdir(parents=True)
    monkeypatch.setattr(run_offline_tests, "PROTECTED_EPISODES", protected.resolve())
    monkeypatch.chdir(worktree)

    assert run_offline_tests.is_protected_path("../../episodes/EP8/state.json")
    assert not run_offline_tests.is_protected_path(tmp_path / "safe.json")
