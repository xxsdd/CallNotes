"""Local incremental captions. Runs in the existing ASR Python environment.

Capture always writes original PCM independently. This process tails both tracks,
updates a growing utterance, and commits at a natural pause. Computational windows
are hidden from the reading UI, which groups them into speaker turns.
"""
from __future__ import annotations

import audioop
import json
import os
import sys
import time
import queue
import threading
from pathlib import Path

# Shared offline CUDA DLL setup, no model download or change to VoiceStudio.
from asr_worker import emit
from text_normalization import simplified
from asr_quality import decode_speech, uncertain_segment

SHUTDOWN = threading.Event()


def save_checkpoint(folder, segments, complete=False):
    path = folder / 'live-transcript.json'
    tmp = folder / 'live-transcript.json.tmp'
    with tmp.open('w', encoding='utf-8') as handle:
        json.dump({'segments': sorted(segments, key=lambda s: (s['start'], s['track'])),
                   'complete': complete, 'engine': 'faster-whisper-large-v3',
                   'recognition_policy': 'zh-en-quality-v2', 'saved_at': time.time()}, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_clip(folder, track, cursor, window=26.0):
    import numpy as np
    path = folder / (track['key'] + '.pcm')
    if not path.exists():
        return np.zeros(0, dtype=np.float32)
    rate, channels = track['rate'], track['channels']
    frame_size = channels * 2
    with path.open('rb') as source:
        source.seek(int(cursor * rate) * frame_size)
        raw = source.read(int(rate * window) * frame_size)
    raw = raw[:len(raw) // frame_size * frame_size]
    if not raw:
        return np.zeros(0, dtype=np.float32)
    if channels == 2:
        raw = audioop.tomono(raw, 2, .5, .5)
    if rate != 16000:
        raw, _ = audioop.ratecv(raw, 2, 1, rate, 16000, None)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768


def load_model(model_path):
    import numpy as np
    from faster_whisper import WhisperModel
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    model_path = Path(model_path)
    if not (model_path / 'model.bin').exists():
        raise RuntimeError('本地模型缺失，录音会继续保存，请设置已下载的模型目录。')
    emit({'type': 'progress', 'text': '正在准备本地字幕引擎，首次打开需要几秒…'})
    model = WhisperModel(str(model_path), device='cuda', compute_type='float16', local_files_only=True)
    silence = np.zeros(16000, dtype=np.float32)
    get_speech_timestamps(silence, VadOptions())
    chunks, _ = model.transcribe(silence, language='zh', beam_size=1,
                                condition_on_previous_text=False, vad_filter=False)
    list(chunks)  # Warm CUDA inference too; none of this is user audio or saved text.
    return model


def process_recording(folder, model, language_mode='zh_en'):
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    folder = Path(folder)
    meta = json.loads((folder / 'audio.json').read_text(encoding='utf-8'))
    states = {t['key']: {'cursor': 0., 'last_partial': 0., 'last_end': 0., 'serial': 0}
              for t in meta['tracks']}
    finals = []
    emit({'type': 'ready'})
    vad = VadOptions(min_silence_duration_ms=700, min_speech_duration_ms=250,
                     speech_pad_ms=250, max_speech_duration_s=24)
    finishing = False
    while not SHUTDOWN.is_set():
        finishing = finishing or (folder / 'live-stop.json').exists()
        capture_state_path = folder / 'record-state.json'
        capture_state = json.loads(capture_state_path.read_text(encoding='utf-8')) if capture_state_path.exists() else {}
        did_work, drained = False, True
        for track in meta['tracks']:
            state = states[track['key']]
            audio = read_clip(folder, track, state['cursor'])
            seconds = len(audio) / 16000
            if seconds < .15:
                continue
            spans = get_speech_timestamps(audio, vad)
            if not spans:
                # Retain a little context so speech at the next poll isn't cut off.
                state['cursor'] += max(0., seconds - .4) if not finishing else seconds
                raw = folder / (track['key'] + '.pcm')
                available = raw.stat().st_size / (track['rate'] * track['channels'] * 2)
                if finishing and state['cursor'] + .15 < available:
                    drained = False
                continue
            drained = False
            span = spans[0]
            start, end = span['start'] / 16000, span['end'] / 16000
            # If output loops stop delivering frames during silence, use wall time
            # since the captured tail as evidence that the utterance has ended.
            wall_now = capture_state.get('pause_wall') if capture_state.get('paused') else time.time()
            capture_elapsed = max(0., wall_now - meta.get('wall_started', time.time()) - capture_state.get('paused_seconds', 0))
            tail_age = capture_elapsed - state['cursor'] - seconds - track.get('offset', 0)
            final = finishing or capture_state.get('paused', False) or len(spans) > 1 or seconds - end >= .9 or end - start >= 23.5 or tail_age > 1.3
            now = time.monotonic()
            if not final and (now - state['last_partial'] < .9 or end - state['last_end'] < .45):
                continue
            if end - start < 1.2 and not final:
                continue
            clip = audio[span['start']:span['end']]
            chunks, language = decode_speech(model, clip, state, final, language_mode)
            values = []
            base = state['cursor'] + start + track.get('offset', 0)
            for chunk, quality in chunks:
                if not quality['accepted']:
                    if final and quality['reason'] not in ('empty', 'likely_noise', 'unsupported_outro', 'implausible_timing'):
                        values.append(uncertain_segment(base + chunk.start,
                            base + min(chunk.end, len(clip) / 16000), track, [quality['reason']]))
                    continue
                raw_text = chunk.text.strip()
                text = simplified(raw_text)
                if not text:
                    continue
                values.append({'start': round(base + chunk.start, 3),
                               'end': round(base + min(chunk.end, len(clip) / 16000), 3),
                               'speaker': track['speaker'], 'track': track['key'], 'text': text,
                               **({'raw_text': raw_text} if raw_text != text else {}),
                               'review': quality['review'], 'language': language, 'quality': quality})
            utterance = track['key'] + '-' + str(state['serial'])
            emit({'type': 'caption', 'track': track['key'], 'utterance': utterance,
                  'final': final, 'segments': values})
            state['last_partial'], state['last_end'] = now, end
            if final:
                finals.extend(values)
                state['cursor'] += end
                state['serial'] += 1
                state['last_end'] = 0
                save_checkpoint(folder, finals)
                did_work = True
        if finishing and drained:
            save_checkpoint(folder, finals, complete=True)
            emit({'type': 'result', 'segments': sorted(finals, key=lambda s: (s['start'], s['track'])),
                  'device': 'cuda', 'mode': 'incremental'})
            return
        time.sleep(.06 if did_work else .20)


def main():
    if sys.argv[1] != '--server':
        model = load_model(sys.argv[2])
        return process_recording(sys.argv[1], model)
    commands = queue.Queue()

    def receive():
        for line in sys.stdin:
            try:
                commands.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        SHUTDOWN.set()
        commands.put({'type': 'shutdown'})

    # Initialize native NumPy/CUDA modules before a thread blocks on Windows
    # stdin; starting that reader first hung in NumPy initialization in testing.
    model = load_model(sys.argv[2])
    threading.Thread(target=receive, daemon=True).start()
    emit({'type': 'engine_ready'})
    while not SHUTDOWN.is_set():
        command = commands.get()
        if command.get('type') == 'shutdown':
            return
        if command.get('type') == 'record':
            try:
                process_recording(command['folder'], model, command.get('language_mode', 'zh_en'))
            except Exception as exc:
                emit({'type': 'error', 'text': str(exc), 'scope': 'recording'})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        emit({'type': 'error', 'text': str(exc), 'scope': 'engine'})
        sys.exit(1)
