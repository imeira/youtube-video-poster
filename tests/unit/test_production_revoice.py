import json
import re
import wave
from pathlib import Path

import pytest

from tests.unit.test_production_harness import approved_source, cli, tone_frames
from tests.unit.test_ep8_offline_adapter import source, snapshot
from src.hybrid.artifacts import atomic_json, sha256
from src.hybrid.production import ProductionHarness, THEME
from src.providers.base import TTSResult


@pytest.fixture
def long_source(approved_source):
    board = json.loads((approved_source / 'story.json').read_text())
    board['frames'][0]['narration_text'] = ' '.join(['Deus fez uma promessa'] * 9) + '.'
    atomic_json(approved_source / 'story.json', board)
    state = json.loads((approved_source / 'state.json').read_text())
    state['checkpoint']['revision_v2']['storyboard_sha256'] = sha256(approved_source / 'story.json')
    atomic_json(approved_source / 'state.json', state)
    (approved_source / 'script.md').write_text('\n'.join(f['narration_text'] for f in board['frames']), encoding='utf-8')
    audio = json.loads((approved_source / 'audio/narration_v1_manifest.json').read_text())
    audio['script_sha256'] = sha256(approved_source / 'script.md')
    atomic_json(approved_source / 'audio/narration_v1_manifest.json', audio)
    return approved_source


class FakeTTS:
    def __init__(self, failure=None):
        self.calls = 0
        self.failure = failure

    async def synthesize(self, text, *, output_path, voice, rate, pitch):
        self.calls += 1
        assert (voice, rate, pitch) == ('pt-BR-ThalitaNeural', '-8%', '+1Hz')
        words = re.findall(r'\b\w+\b', text)
        timings = []
        end = .12
        for i, word in enumerate(words):
            start = end + .01
            end = start + (.07 if i % 2 else .11)
            timings.append(dict(word=word, start=start, end=end))
        duration = end + .2
        with wave.open(str(output_path), 'wb') as wav:
            wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            wav.writeframes(tone_frames(round(duration * 8000)))
        if self.failure == 'network':
            raise OSError('offline')
        if self.failure == 'missing':
            timings = []
        if self.failure == 'mismatch':
            timings[0]['word'] = 'changed'
        if self.failure == 'nan':
            timings[0]['start'] = float('nan')
        return TTSResult(success=True, audio_path=str(output_path), duration_seconds=duration,
                         word_timestamps=timings, cost=0)


def make_plan(h, source):
    return h.plan(source, 'EP8', THEME, 'pt-BR', 'test')


def test_revoice_requires_approval_then_rebinds_exact_words(long_source, tmp_path):
    before = snapshot(long_source)
    fake = FakeTTS()
    root = tmp_path / 'production'
    with ProductionHarness(root, tts_factory=lambda: fake) as h:
        plan = make_plan(h, long_source)
        assert plan['revoice_required'] is True
        with pytest.raises(ValueError, match='approval'):
            h.revoice()
        assert fake.calls == 0
        h.approve(plan['plan_hash'], 'human')
        with pytest.raises(ValueError, match='TTS_REQUIRED'):
            h.run()
        result = h.revoice()
        assert result['status'] == 'REVOICE_COMPLETED'
        assert h.revoice() == result and fake.calls == 1
        _, (episode, manifest, _) = h.validate()
        assert len(episode.frames) == 39
        assert episode.audio.path.is_relative_to(root)
        packet = Path(result['packet'])
        editorial = json.loads((packet / 'editorial.json').read_text())
        from src.agents.script_qa import ScriptQAAgent
        assert ScriptQAAgent().review(editorial['script']).approved
        old = json.loads((Path(plan['packet']) / 'editorial.json').read_text())
        assert re.findall(r'\w+', old['script']['narration']) == re.findall(r'\w+', editorial['script']['narration'])
        timeline = json.loads((packet / 'timeline.json').read_text())
        assert episode.frames[1].start == timeline['words'][36]['start']
        assert episode.frames[-1].end == timeline['duration_s']
        assert h.status()['next_action'] == 'run'
    assert snapshot(long_source) == before
    with ProductionHarness(root) as h:
        assert h.validate()[1][0] == episode
    (packet / 'timeline.json').write_text('{}')
    with ProductionHarness(root) as h:
        with pytest.raises(ValueError):
            h.run()


@pytest.mark.parametrize('failure', ['network', 'missing', 'mismatch', 'nan'])
def test_failed_tts_never_activates_packet(long_source, tmp_path, failure):
    root = tmp_path / 'production'
    before = snapshot(long_source)
    with ProductionHarness(root, tts_factory=lambda: FakeTTS(failure)) as h:
        plan = make_plan(h, long_source)
        h.approve(plan['plan_hash'], 'human')
        with pytest.raises(ValueError, match='TTS_REQUIRED'):
            h.revoice()
        assert h.status()['status'] == 'TTS_REQUIRED'
        assert h.latest('revoice', 'REVOICE_COMPLETED') is None
        with pytest.raises(ValueError, match='TTS_REQUIRED'):
            h.run()
        assert not (root / 'renders').exists()
        h.tts_factory = FakeTTS
        assert h.revoice()['status'] == 'REVOICE_COMPLETED'
    assert snapshot(long_source) == before


def test_revoice_cli_is_approval_gated(long_source, tmp_path, monkeypatch, capsys):
    root = tmp_path / 'production'
    assert cli(monkeypatch, capsys, root, 'plan', '--source-root', long_source)[0] == 0
    code, result = cli(monkeypatch, capsys, root, 'revoice')
    assert code == 1 and 'approval' in result['error']


def test_revoice_reaches_local_render_and_delivery(long_source, tmp_path):
    before = snapshot(long_source)
    with ProductionHarness(tmp_path / 'production', tts_factory=FakeTTS) as h:
        plan = make_plan(h, long_source)
        h.approve(plan['plan_hash'], 'human')
        revised = h.revoice()
        render = h.run()
        assert render['status'] == 'TECHNICAL_QA_PASSED'
        assert h.run() == render
        delivery = h.deliver()
        assert delivery['status'] == 'WAITING_THUMBNAIL_APPROVAL'
        assert delivery['active']['plan_receipt'] == revised['receipt']
    assert snapshot(long_source) == before


def test_visual_freeze_change_during_tts_blocks_activation(long_source, tmp_path):
    class MutatingTTS(FakeTTS):
        async def synthesize(self, *args, **kwargs):
            result = await super().synthesize(*args, **kwargs)
            path = long_source / 'compiled/EP8_visual_freeze_v2.json'
            value = json.loads(path.read_text())
            value['visual_freeze_pass'] = False
            atomic_json(path, value)
            return result
    with ProductionHarness(tmp_path / 'production', tts_factory=MutatingTTS) as h:
        plan = make_plan(h, long_source)
        h.approve(plan['plan_hash'], 'human')
        with pytest.raises(ValueError, match='TTS_REQUIRED'):
            h.revoice()
        assert h.latest('revoice', 'REVOICE_COMPLETED') is None


def test_split_only_preserves_short_segments_and_punctuation():
    from src.hybrid.revoice import split_script
    short = dict(id='short', kind='family_reflection', source_refs=[], narration='Uma promessa. Uma alegria!')
    long = dict(id='long', kind='family_reflection', source_refs=[], narration=' '.join(['Uma promessa,'] * 20) + '.')
    packet = dict(audience={'min_age': 6, 'max_age': 10}, closing_duration_s=4,
                  segments=[short, long], narration=short['narration']+'\n\n'+long['narration'])
    revised = split_script(packet)
    assert revised['segments'][0] == short
    assert revised['narration'].split() == packet['narration'].split()
