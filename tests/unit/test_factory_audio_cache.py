"""Candidate-audio cache tests: no real synthesis, consent, or promotion."""
import asyncio
import wave
from pathlib import Path

import pytest

from src.agents.audio import AudioAgent
from src.providers.base import TTSResult


@pytest.fixture
def transport(tmp_path, monkeypatch):
    source = tmp_path / 'TEST-source.wav'
    with wave.open(str(source), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b'\x00\x00' * 8000)
    calls = []
    constructors = []
    words = [{'word': 'Teste', 'start': 0.0, 'end': 0.5}]
    sentences = [{'text': 'Teste.', 'start': 0.0, 'end': 0.5, 'duration': 0.5}]

    class TestTransport:
        def __init__(self, **kwargs):
            constructors.append(kwargs)

        async def synthesize(self, **kwargs):
            calls.append(kwargs)
            await asyncio.sleep(0)
            return TTSResult(
                success=True, audio_path=str(source), duration_seconds=1.0,
                word_timestamps=words, sentence_timestamps=sentences,
                metadata={'mode': 'TEST', 'rtf': 0.1},
            )

    monkeypatch.setattr('src.agents.audio.EdgeTTSProvider', TestTransport)
    return {'source': source, 'calls': calls, 'constructors': constructors,
            'words': words, 'sentences': sentences}


@pytest.mark.asyncio
async def test_identical_audio_input_reuses_exact_bytes_and_timestamps(tmp_path, transport):
    audio_dir = str(tmp_path / 'a')
    first = await AudioAgent().run('TEST-AUDIO-CACHE', narration='Teste.', audio_dir=audio_dir)
    second = await AudioAgent().run('TEST-AUDIO-CACHE', narration='Teste.', audio_dir=audio_dir)
    assert first.success and second.success
    assert len(transport['calls']) == 1, 'identical persisted TTS candidates must not resubmit'
    assert len(transport['constructors']) == 1
    assert second.data['cache_hit'] is True
    assert Path(second.data['audio_path']).read_bytes() == transport['source'].read_bytes()
    assert second.data['word_timestamps'] == transport['words']
    assert second.data['sentence_timestamps'] == transport['sentences']


@pytest.mark.asyncio
async def test_distinct_candidates_preserve_existing_narration_and_source(tmp_path, transport):
    directory = tmp_path / 'a'
    directory.mkdir()
    canonical = directory / 'narration.mp3'
    original = b'TEST existing narration: never replace'
    canonical.write_bytes(original)
    source_before = transport['source'].read_bytes()

    first = await AudioAgent().run('TEST-AUDIO-CACHE', narration='Teste.', audio_dir=str(directory))
    assert first.success
    assert canonical.read_bytes() == original
    second = await AudioAgent().run('TEST-AUDIO-CACHE', narration='Outra.', audio_dir=str(directory))
    resumed = await AudioAgent().run('TEST-AUDIO-CACHE', narration='Teste.', audio_dir=str(directory))

    assert second.success and resumed.success
    assert len(transport['calls']) == 2
    assert resumed.data['cache_hit'] is True
    assert first.data['audio_path'] != second.data['audio_path']
    assert resumed.data['audio_path'] == first.data['audio_path']
    assert Path(first.data['audio_path']).read_bytes() == source_before
    assert transport['source'].read_bytes() == source_before
    assert canonical.read_bytes() == original


@pytest.mark.asyncio
async def test_concurrent_identical_candidates_submit_once(tmp_path, transport):
    arguments = {'narration': 'Teste.', 'audio_dir': str(tmp_path / 'a')}
    results = await asyncio.gather(
        AudioAgent().run('TEST-AUDIO-CACHE', **arguments),
        AudioAgent().run('TEST-AUDIO-CACHE', **arguments),
        return_exceptions=True,
    )
    assert len(transport['calls']) == 1, 'the lock must span lookup through persistence'
    assert all(not isinstance(result, BaseException) and result.success for result in results)
    assert sorted(result.data['cache_hit'] for result in results) == [False, True]
    assert results[0].data['audio_path'] == results[1].data['audio_path']


@pytest.mark.asyncio
async def test_distinct_misses_request_isolated_provider_outputs(tmp_path, transport):
    directory = tmp_path / 'a'
    results = await asyncio.gather(*[
        AudioAgent().run('TEST-AUDIO-CACHE', narration=text, audio_dir=str(directory))
        for text in ('Teste.', 'Outra.')
    ])
    assert all(result.success for result in results)
    assert len(transport['calls']) == 2
    assert all('output_path' in call for call in transport['calls']), 'do not share the provider global temporary filename'
    paths = [Path(call['output_path']) for call in transport['calls']]
    assert paths[0] != paths[1]
    assert all(path.parent == directory / '.audio-cache' for path in paths)


@pytest.mark.asyncio
@pytest.mark.parametrize('identity', ['provider_version', 'algorithm', 'provider_source'])
async def test_provider_or_algorithm_change_invalidates_candidate_identity(tmp_path, transport, monkeypatch, identity):
    import importlib.metadata

    from src.agents import audio as audio_module
    from src.providers.tts import edge_tts_provider

    changed = [False]
    original_version = importlib.metadata.version
    original_sha = audio_module.sha256

    def version(name):
        if name == 'edge-tts':
            return 'TEST-v2' if changed[0] else 'TEST-v1'
        return original_version(name)

    def source_sha(path):
        target = audio_module.__file__ if identity == 'algorithm' else edge_tts_provider.__file__
        if Path(path) == Path(target):
            return ('b' if changed[0] else 'a') * 64
        return original_sha(path)

    if identity == 'provider_version':
        monkeypatch.setattr(importlib.metadata, 'version', version)
    else:
        monkeypatch.setattr(audio_module, 'sha256', source_sha)
    arguments = {'narration': 'Teste.', 'audio_dir': str(tmp_path / 'a')}
    first = await AudioAgent().run('TEST-AUDIO-CACHE', **arguments)
    changed[0] = True
    second = await AudioAgent().run('TEST-AUDIO-CACHE', **arguments)
    assert first.success and second.success
    assert len(transport['calls']) == 2, 'provider/algorithm identity must participate in the cache key'
    assert second.data['cache_hit'] is False
    assert first.data['audio_path'] != second.data['audio_path']


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['approval_field', 'missing_words', 'invalid_duration', 'unordered_timestamps', 'timestamp_exceeds_duration', 'non_finite_timestamp'])
async def test_redigested_cache_data_schema_cannot_inject_authority_or_invalid_timing(tmp_path, transport, change):
    import json

    from src.hybrid.artifacts import digest

    directory = tmp_path / 'a'
    arguments = {'narration': 'Teste.', 'audio_dir': str(directory)}
    first = await AudioAgent().run('TEST-AUDIO-CACHE', **arguments)
    assert first.success
    receipt_path = Path(first.data['audio_path']).with_suffix('.json')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if change == 'approval_field':
        receipt['data']['approved'] = True
    elif change == 'missing_words':
        del receipt['data']['word_timestamps']
    elif change == 'invalid_duration':
        receipt['data']['duration_s'] = -1
    elif change == 'unordered_timestamps':
        receipt['data']['word_timestamps'] = [
            {'start': 0.5, 'end': 0.8, 'word': 'segundo'},
            {'start': 0.1, 'end': 0.4, 'word': 'primeiro'},
        ]
    elif change == 'timestamp_exceeds_duration':
        receipt['data']['word_timestamps'] = [
            {'start': 0.0, 'end': receipt['data']['duration_s'] + 5.0, 'word': 'excesso'},
        ]
    elif change == 'non_finite_timestamp':
        receipt['data']['sentence_timestamps'] = [
            {'start': 'not-a-number', 'end': 1.0, 'text': 'string-time'},
        ]
    receipt['receipt_sha256'] = digest({key: value for key, value in receipt.items() if key != 'receipt_sha256'})
    receipt_path.write_text(json.dumps(receipt), encoding='utf-8')

    result = await AudioAgent().run('TEST-AUDIO-CACHE', **arguments)
    assert not result.success, 'a self-consistent digest does not relax the candidate schema'
    assert 'cache' in result.error.lower()
    assert len(transport['calls']) == len(transport['constructors']) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['audio', 'receipt'])
async def test_cache_reparse_file_is_rejected_before_content_read(tmp_path, transport, monkeypatch, role):
    directory = tmp_path / 'a'
    arguments = {'narration': 'Teste.', 'audio_dir': str(directory)}
    first = await AudioAgent().run('TEST-AUDIO-CACHE', **arguments)
    assert first.success
    target = Path(first.data['audio_path'])
    if role == 'receipt':
        target = target.with_suffix('.json')
    foreign = tmp_path / ('inert-foreign' + target.suffix)
    foreign.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(foreign)
    except OSError as error:
        if getattr(error, 'winerror', None) == 1314:
            pytest.skip('native file symlink privilege is unavailable')
        raise
    seen = []
    original_open = Path.open

    def watched(path, *args, **kwargs):
        if path == target:
            seen.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', watched)
    result = await AudioAgent().run('TEST-AUDIO-CACHE', **arguments)
    assert not result.success, 'cache files cannot redirect to another inode by symlink'
    assert seen == []
    assert len(transport['calls']) == len(transport['constructors']) == 1


@pytest.mark.asyncio
async def test_corruption_during_candidate_copy_never_gets_a_cache_receipt(tmp_path, transport, monkeypatch):
    import shutil

    original_copy = shutil.copyfileobj

    def corrupt(incoming, outgoing, *args, **kwargs):
        original_copy(incoming, outgoing, *args, **kwargs)
        outgoing.write(b'TEST injected copy fault')

    monkeypatch.setattr(shutil, 'copyfileobj', corrupt)
    directory = tmp_path / 'a'
    source_before = transport['source'].read_bytes()
    result = await AudioAgent().run('TEST-AUDIO-CACHE', narration='Teste.', audio_dir=str(directory))
    assert not result.success, 'the copied candidate must match the source hash captured before copying'
    assert list((directory / '.audio-cache').glob('*.json')) == []
    assert not (directory / 'narration.mp3').exists()
    assert transport['source'].read_bytes() == source_before


@pytest.mark.asyncio
async def test_incomplete_candidate_does_not_trigger_blind_resynthesis(tmp_path, transport):
    arguments = {'narration': 'Teste.', 'audio_dir': str(tmp_path / 'a')}
    first = await AudioAgent().run('TEST-AUDIO-CACHE', **arguments)
    assert first.success
    candidate = Path(first.data['audio_path'])
    saved = candidate.read_bytes()
    candidate.with_suffix('.json').unlink()
    try:
        result = await AudioAgent().run('TEST-AUDIO-CACHE', **arguments)
    except FileExistsError:
        result = None
    assert len(transport['calls']) == len(transport['constructors']) == 1
    assert result is not None and not result.success
    assert 'reconciliation' in result.error.lower()
    assert candidate.read_bytes() == saved


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['bytes', 'timestamps', 'foreign_path'])
async def test_cached_audio_tamper_fails_before_new_transport(tmp_path, transport, change):
    import json

    from src.hybrid.artifacts import digest

    arguments = {'narration': 'Teste.', 'audio_dir': str(tmp_path / 'a')}
    first = await AudioAgent().run('TEST-AUDIO-CACHE', **arguments)
    assert first.success
    audio = Path(first.data['audio_path'])
    receipt_path = audio.with_suffix('.json')
    if change == 'bytes':
        audio.write_bytes(b'TEST corrupt audio')
    else:
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if change == 'timestamps':
            receipt['data']['word_timestamps'][0]['end'] = 0.9
        else:
            receipt['data']['audio_path'] = str(transport['source'])
            receipt['receipt_sha256'] = digest({key: value for key, value in receipt.items() if key != 'receipt_sha256'})
        receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    resumed = await AudioAgent().run('TEST-AUDIO-CACHE', **arguments)
    assert not resumed.success
    assert len(transport['calls']) == len(transport['constructors']) == 1


@pytest.mark.asyncio
async def test_alignment_programming_error_is_not_silently_cached(tmp_path, transport, monkeypatch):
    from src.agents import audio as audio_module

    transport['words'].clear()

    async def broken_alignment(*args, **kwargs):
        raise TypeError('TEST aligner contract error')

    monkeypatch.setattr(audio_module.EdgeTTSProvider, 'word_align', broken_alignment, raising=False)
    with pytest.raises(TypeError, match='TEST aligner contract error'):
        await AudioAgent().run('TEST-AUDIO-CACHE', narration='Teste.', audio_dir=str(tmp_path / 'a'))
    assert list((tmp_path / 'a' / '.audio-cache').glob('*.json')) == []
