"""Run with the existing VoiceStudio interpreter; never modify its environment."""
from __future__ import annotations

import json
import os
import re
import sys
import wave
import io
from bisect import bisect_left, bisect_right
from difflib import SequenceMatcher
from pathlib import Path
from text_normalization import simplified
from asr_quality import decode_speech, uncertain_segment

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['HF_HOME'] = r'D:\Apps\VoiceStudio-Environment\models'
os.environ['TEMP'] = r'D:\Apps\CallNotes-Cache\tmp'
os.environ['TMP'] = os.environ['TEMP']
os.environ['NUMBA_CACHE_DIR'] = r'D:\Apps\CallNotes-Cache\numba'

DLL_HANDLES = []
runtime = Path(r'D:\Apps\CallNotes\runtime\cudnn8')
if runtime.exists():
    os.environ['PATH'] = str(runtime) + os.pathsep + os.environ['PATH']
    if hasattr(os, 'add_dll_directory'):
        DLL_HANDLES.append(os.add_dll_directory(str(runtime)))
site_packages = Path(sys.prefix) / 'Lib' / 'site-packages'
for parent in (site_packages / 'nvidia', site_packages / 'torch'):
    if parent.exists():
        for path in parent.rglob('*.dll'):
            folder = str(path.parent)
            if folder not in os.environ['PATH'].split(os.pathsep):
                os.environ['PATH'] = folder + os.pathsep + os.environ['PATH']
                if hasattr(os, 'add_dll_directory'):
                    DLL_HANDLES.append(os.add_dll_directory(folder))


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def main():
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    folder = Path(sys.argv[1])
    model_path = Path(sys.argv[2])
    if not (model_path / 'model.bin').exists():
        raise RuntimeError('本地 large-v3 模型文件缺失；未启动在线下载。')
    emit({'type': 'progress', 'text': '正在加载本地 large-v3（CUDA）…'})
    model = WhisperModel(str(model_path), device='cuda', compute_type='float16', local_files_only=True)
    meta = json.loads((folder / 'audio.json').read_text(encoding='utf-8'))
    all_segments = []
    language_mode = sys.argv[3] if len(sys.argv) > 3 else 'zh_en'
    region = json.loads(sys.argv[4]) if len(sys.argv) > 4 else None
    # Transcription never loads speaker models, even if a prior run has a cache
    # or a saved participant count. Only the separate user action runs them.
    warnings = []
    for track in meta['tracks']:
        if region and track['key'] != region['track']:
            continue
        wav = folder / (track['key'] + '.wav')
        if not wav.exists():
            raise RuntimeError(f'{track["speaker"]}的音频文件缺失')
        with wave.open(str(wav), 'rb') as wav_info:
            if wav_info.getnframes() == 0:
                emit({'type': 'progress', 'text': f'“{track["speaker"]}”没有录到声音，保留空音轨。'})
                continue
        emit({'type': 'progress', 'text': f'正在转录“{track["speaker"]}”的声音…'})
        state = {'language': 'zh'}
        crop_offset = 0.
        if region:
            with wave.open(str(wav), 'rb') as original:
                rate = original.getframerate()
                first = max(0, int((region['start'] - track.get('offset', 0)) * rate))
                last = min(original.getnframes(), int((region['end'] - track.get('offset', 0)) * rate))
                if last <= first:
                    raise ValueError('选中段落没有可重新识别的音频。')
                original.setpos(first)
                buffer = io.BytesIO()
                with wave.open(buffer, 'wb') as clipped:
                    clipped.setparams(original.getparams())
                    clipped.writeframes(original.readframes(last - first))
                buffer.seek(0)
                audio = decode_audio(buffer, sampling_rate=16000)
                crop_offset = first / rate
        else:
            audio = decode_audio(str(wav), sampling_rate=16000)
        ranges = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=700,
                                      min_speech_duration_ms=250, speech_pad_ms=250, max_speech_duration_s=24))
        # Detect language per utterance rather than imposing the dominant language
        # of a whole meeting; otherwise short English turns can disappear in Chinese.
        for speech in ranges:
            clip = audio[speech['start']:speech['end']]
            segments, language = decode_speech(model, clip, state, True, language_mode)
            for segment, quality in segments:
                offset = speech['start'] / 16000 + track.get('offset', 0) + crop_offset
                if not quality['accepted']:
                    if quality['reason'] not in ('empty', 'likely_noise', 'unsupported_outro', 'implausible_timing'):
                        all_segments.append(uncertain_segment(offset + segment.start,
                            offset + min(segment.end, len(clip) / 16000), track, [quality['reason']]))
                    continue
                raw_text = segment.text.strip()
                text = simplified(raw_text)
                if not text:
                    continue
                value = {'start': round(segment.start + offset, 3),
                         'end': round(min(segment.end, len(clip) / 16000) + offset, 3),
                         'speaker': track['speaker'], 'track': track['key'], 'text': text,
                         **({'raw_text': raw_text} if raw_text != text else {}),
                         'review': quality['review'], 'language': language, 'quality': quality}
                if track['key'] in ('imported', 'system') and segment.words:
                    value['words'] = [dict(start=round(w.start + offset, 3),
                        end=round(min(w.end, len(clip) / 16000) + offset, 3),
                        word=simplified(w.word)) for w in segment.words]
                all_segments.append(value)
                emit({'type': 'segment', 'segment': value})
        emit({'type': 'progress', 'text': f'“{track["speaker"]}”转录完成'})
    all_segments.sort(key=lambda segment: (segment['start'], segment['track']))
    # Preserve both originals. Flag likely loudspeaker bleed rather than silently
    # deleting a real repetition or asserting that it was the user's own speech.
    remote_segments = [s for s in all_segments if s['track'] == 'system']
    remote_starts = [s['start'] for s in remote_segments]
    for segment in all_segments:
        if segment['track'] != 'mic':
            continue
        own = re.sub(r'\W+', '', segment['text'].casefold())
        if len(own) < 6:
            continue
        left = bisect_left(remote_starts, segment['start'] - 2)
        right = bisect_right(remote_starts, segment['start'] + 2)
        for other in remote_segments[left:right]:
            remote = re.sub(r'\W+', '', other['text'].casefold())
            if SequenceMatcher(None, own, remote).ratio() > .9:
                segment['echo_suspected'] = True
                segment['review'] = True
                break
    emit({'type': 'result', 'segments': all_segments, 'device': 'cuda', 'model': str(model_path),
          'warnings': warnings, 'diarization': None})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        emit({'type': 'error', 'text': str(exc)})
        sys.exit(1)
