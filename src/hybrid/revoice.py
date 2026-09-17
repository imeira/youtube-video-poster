"""Lossless segmentation and exact WordBoundary allocation; no model or network."""
import math
import re
import unicodedata
from dataclasses import replace

from src.agents.script_qa import ScriptQAAgent


def words(text):
    return re.findall(r'\b\w+\b', unicodedata.normalize('NFC', text).casefold())


def split_script(script):
    segments = []
    for segment in script['segments']:
        text = segment['narration']
        sentences = re.split(r'(?<=[.!?])\s+', text)
        if all(len(words(sentence)) <= 30 for sentence in sentences):
            segments.append(dict(segment))
            continue
        parts = []
        for sentence in sentences:
            tokens = list(re.finditer(r'\b\w+\b', sentence))
            start = 0
            for index in range(30, len(tokens), 30):
                cut = tokens[index].start()
                parts.append(sentence[start:cut].strip())
                start = cut
            if sentence[start:].strip():
                parts.append(sentence[start:].strip())
        segments.extend({**segment, 'id': f"{segment['id']}.{i+1}",
                         'parent_frame': segment['id'], 'narration': part}
                        for i, part in enumerate(parts))
    result = {**script, 'segments': segments,
              'narration': '\n\n'.join(s['narration'] for s in segments)}
    if words(result['narration']) != words(script['narration']):
        raise ValueError('source word order changed')
    qa = ScriptQAAgent().review(result)
    if not qa.approved:
        raise ValueError('script QA failed: ' + ', '.join(qa.findings))
    return result


def allocate(frames, cues, timings, duration):
    if len(frames) != 39 or len(cues) != 39 or not timings:
        raise ValueError('39 frames and real WordBoundary timings required')
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('invalid audio duration')
    tokens, previous = [], 0
    for event in timings:
        start, end = event['start'], event['end']
        if not all(math.isfinite(v) for v in (start, end)) or start < previous or end <= start or end > duration:
            raise ValueError('invalid WordBoundary window')
        normalized = words(event['word'])
        if not normalized:
            raise ValueError('empty WordBoundary')
        tokens.extend((word, start, end) for word in normalized)
        previous = end
    cursor, allocated, speech = 0, [], []
    for frame, cue in zip(frames, cues):
        expected = words(cue['text'])
        selected = tokens[cursor:cursor + len(expected)]
        if not expected or [t[0] for t in selected] != expected:
            raise ValueError('WordBoundary words differ from original frame narration')
        if cursor and tokens[cursor-1][2] > selected[0][1]:
            raise ValueError('frame boundary inside a WordBoundary event')
        speech.append(dict(start=selected[0][1], end=selected[-1][2], text=cue['text']))
        cursor += len(expected)
    if cursor != len(tokens):
        raise ValueError('unallocated WordBoundary words')
    for i, frame in enumerate(frames):
        start = speech[i]['start'] if i else 0
        end = speech[i+1]['start'] if i+1 < len(frames) else duration
        if end - start <= .25:
            raise ValueError('visual window too short')
        allocated.append(replace(frame, start=start, end=end))
    return tuple(allocated), speech
