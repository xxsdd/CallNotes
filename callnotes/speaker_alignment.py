"""Assign timed words to anonymous voices without guessing identities or text."""
from bisect import bisect_left
from collections import defaultdict

try:
    from .text_normalization import simplified
except ImportError:
    from text_normalization import simplified


def speaker_label(speaker_id):
    if speaker_id == 'unknown':
        return '说话人待确认'
    return '说话人 ' + speaker_id.rsplit('-', 1)[-1]


def count_warning(diarization, segments):
    expected = diarization.get('requested_speakers', 0)
    detected = diarization.get('detected_speakers', 0)
    represented = {s.get('speaker_id') for s in segments if s.get('track') == diarization.get('track')
                   and s.get('speaker_id') not in (None, 'unknown')}
    if not expected:
        return (f'自动分出 {detected} 个声音组，但文字稿中仅出现 {len(represented)} 个明确说话人；部分声音尚未对齐文字，请回听核对。'
                if detected > 1 and len(represented) < detected else '')
    if expected < 2:
        return ''
    if detected != expected or len(represented) < expected:
        return f'设置为 {expected} 人，声音模型输出 {detected} 组，但文字稿中仅出现 {len(represented)} 个明确说话人，尚未完整区分；请回听核对。'
    return ''


def align_speakers(segments, diarization, aliases=None):
    turns = sorted(diarization.get('turns', []), key=lambda t: t['start'])
    starts = [t['start'] for t in turns]
    # Prefix maximum end handles overlapping turns without scanning the whole file.
    max_ends, end = [], 0.
    for turn in turns:
        end = max(end, turn['end'])
        max_ends.append(end)
    aliases = aliases or {}

    acoustic = diarization.get('refinement', {}).get('evidence', [])

    def assign(start, end):
        end = max(end, start + .02)
        duration = end - start
        left = bisect_left(max_ends, start)
        right = bisect_left(starts, end)
        scores = defaultdict(float)
        for turn in turns[left:right]:
            overlap = max(0., min(end, turn['end']) - max(start, turn['start']))
            if overlap > 0:
                scores[turn['speaker_id']] += overlap
        ranked = sorted(scores.items(), key=lambda p: p[1], reverse=True)
        ambiguous = len(ranked) > 1 and ranked[1][1] > ranked[0][1] * .35
        if ranked and ranked[0][1] >= duration * .30 and not ambiguous:
            return ranked[0][0], 'direct'
        if ambiguous:
            votes = defaultdict(float)
            for evidence in acoustic:
                overlap = max(0., min(end, evidence['end']) - max(start, evidence['start']))
                if overlap:
                    votes[evidence['speaker_id']] += overlap
            ordered = sorted(votes.items(), key=lambda p: p[1], reverse=True)
            if (ordered and ordered[0][1] >= duration * .5
                    and ordered[0][0] in scores
                    and (len(ordered) == 1 or ordered[1][1] < ordered[0][1] * .25)):
                return ordered[0][0], 'acoustic'
            return 'unknown', 'overlap'
        # A short word just before a detected onset is usually a timestamp
        # mismatch. Check nearby competing voices before allowing this bridge.
        nearby = {t['speaker_id'] for t in turns[max(0, left - 3):right + 3]
                  if max(t['start'] - end, start - t['end'], 0.) <= .32}
        if len(nearby) == 1 and (duration <= .55 or not ranked and duration <= .25):
            identity = next(iter(nearby))
            if not ranked or ranked[0][0] == identity:
                return identity, 'edge'
        return 'unknown', 'gap'

    output = []
    for original in segments:
        if original.get('track') != diarization.get('track', 'imported') or original.get('speaker_manual'):
            output.append(dict(original))
            continue
        words = original.get('words') or []
        # Word alignment is usable only while it still reproduces the saved text.
        if simplified(''.join(w['word'] for w in words)).strip() != original['text'].strip():
            words = []
        units = words or [dict(start=original['start'], end=original['end'], word=original['text'])]
        groups = []
        for unit in units:
            speaker_id, method = assign(unit['start'], unit['end'])
            if groups and groups[-1]['speaker_id'] == speaker_id:
                groups[-1]['end'] = max(groups[-1]['end'], unit['end'])
                groups[-1]['parts'].append(unit)
                groups[-1]['methods'].add(method)
            else:
                groups.append(dict(speaker_id=speaker_id, start=unit['start'], end=unit['end'], parts=[unit], methods={method}))
        for group in groups:
            speaker_id = group['speaker_id']
            value = {**original, 'start': group['start'], 'end': group['end'],
                     'text': simplified(''.join(w['word'] for w in group['parts'])).strip(),
                     'speaker_id': speaker_id, 'speaker': aliases.get(speaker_id, speaker_label(speaker_id)),
                     'speaker_run': diarization['signature'], 'speaker_uncertain': speaker_id == 'unknown',
                     'speaker_inferred': bool(group['methods'] - {'direct', 'gap', 'overlap'}),
                     'speaker_assignment': sorted(group['methods']),
                     'review': bool(original.get('quality', {}).get('review', original.get('review') and not original.get('speaker_uncertain')) or speaker_id == 'unknown')}
            value.pop('raw_text', None)
            if diarization.get('count_recovery'):
                value['speaker_recovery'] = diarization['count_recovery']['policy']
            if words:
                value['words'] = group['parts']
            else:
                value.pop('words', None)
            if value['text']:
                output.append(value)
    return sorted(output, key=lambda s: (s['start'], s['track']))
