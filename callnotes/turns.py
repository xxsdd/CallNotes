"""Reading paragraphs are speaker turns, never the ASR engine's chunks."""
from __future__ import annotations
import re
from .text_normalization import simplified


def join_text(left, right):
    left, right = left.rstrip(), right.lstrip()
    if not left:
        return right
    if not right:
        return left
    # Keep English words separate, without inserting unnatural spaces into Chinese.
    space = bool(re.match(r'[A-Za-z0-9]', right) and re.search(r'[A-Za-z0-9.,!?;:]$', left))
    return left + (' ' if space else '') + right


def speaker_turns(segments, pause=3.0):
    turns = []
    # Stable original indices retain existing S citations and edits.
    indexed = sorted(enumerate(segments), key=lambda pair: (pair[1]['start'], pair[0]))
    for position, (index, segment) in enumerate(indexed):
        segment = {**segment, 'text': simplified(segment.get('text', ''))}
        if not segment.get('text', '').strip():
            continue
        same = (turns and turns[-1]['speaker'] == segment['speaker'] and turns[-1]['track'] == segment['track']
                and turns[-1].get('speaker_id') == segment.get('speaker_id'))
        unknown = segment.get('speaker_id') == 'unknown'
        previous_unknown = bool(turns and turns[-1].get('speaker_id') == 'unknown')
        # Reading-only bridges retain original unknown segments and their source
        # indices. Their text is underlined; analysis never receives this grouping.
        bridge = False
        if turns and turns[-1]['track'] == segment['track']:
            previous = turns[-1]
            if previous['speaker'] == segment['speaker'] and (unknown or previous_unknown):
                bridge = True
            elif unknown and segment['end'] - segment['start'] <= 3 and position + 1 < len(indexed):
                following = indexed[position + 1][1]
                bridge = (bool(previous.get('speaker_id')) and previous['speaker_id'] != 'unknown'
                    and following.get('speaker_id') == previous['speaker_id']
                    and following['track'] == previous['track']
                    and segment['start'] - previous['end'] <= .7
                    and following['start'] - segment['end'] <= .7)
            elif previous.get('contains_unresolved') and previous.get('speaker_id') == segment.get('speaker_id'):
                bridge = True
        # A change of speaker takes precedence over minor pauses. No fixed maximum
        # turn duration: a 90-second uninterrupted answer remains one reading turn.
        merge = (same or bridge) and segment['start'] - turns[-1]['end'] <= pause
        if merge:
            current = turns[-1]
            joined = join_text(current['text'], segment['text'])
            if unknown:
                current['uncertain_ranges'].append((len(joined) - len(segment['text'].lstrip()), len(joined)))
            if previous_unknown and not unknown:
                current['speaker_id'], current['speaker'] = segment.get('speaker_id'), segment['speaker']
            current['text'] = joined
            current['end'] = max(current['end'], segment['end'])
            current['indices'].append(index)
            current['review'] |= bool(segment.get('review'))
            current['echo_suspected'] |= bool(segment.get('echo_suspected'))
            current['partial'] |= bool(segment.get('partial'))
            current['speaker_uncertain'] = bool(current.get('speaker_uncertain') or segment.get('speaker_uncertain'))
            current['speaker_inferred'] = bool(current.get('speaker_inferred') or segment.get('speaker_inferred'))
            current['contains_unresolved'] = bool(current.get('contains_unresolved') or unknown)
        else:
            turns.append({**segment, 'indices': [index], 'review': bool(segment.get('review')),
                          'echo_suspected': bool(segment.get('echo_suspected')),
                          'partial': bool(segment.get('partial')),
                          'contains_unresolved': unknown,
                          'uncertain_ranges': [(0, len(segment['text']))] if unknown else []})
    return turns
