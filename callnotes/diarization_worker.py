"""Local CPU speaker segmentation and clustering; never downloads at runtime."""
import hashlib
import json
import os
from pathlib import Path
import sys
import wave

try:
    from .runtime import speaker_models
except ImportError:
    from runtime import speaker_models
MODEL_ROOT = speaker_models()
POLICY = 'sherpa-eres2net-v4-auto-windows'


def load_samples(wav):
    import av
    import numpy as np
    parts = []
    with av.open(str(wav)) as audio:
        resampler = av.AudioResampler(format='fltp', layout='mono', rate=16000)
        for frame in audio.decode(audio=0):
            parts.extend(converted.to_ndarray().reshape(-1) for converted in resampler.resample(frame))
        parts.extend(converted.to_ndarray().reshape(-1) for converted in resampler.resample(None))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def refine(data, wav, offset, embedding, progress, samples=None):
    try:
        from .speaker_refinement import acoustic_refinement, REFINEMENT
    except ImportError:
        from speaker_refinement import acoustic_refinement, REFINEMENT
    if data.get('refinement', {}).get('policy') != REFINEMENT:
        data['refinement'] = acoustic_refinement(load_samples(wav) if samples is None else samples,
            offset, data['turns'], embedding, progress)
    return data


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def identify(folder, count=0, progress=lambda text: None):
    import numpy as np
    import sherpa_onnx
    folder = Path(folder)
    meta = json.loads((folder / 'audio.json').read_text(encoding='utf-8'))
    track = next((t for t in meta['tracks'] if t['key'] == 'imported'), None)
    track = track or next((t for t in meta['tracks'] if t['key'] == 'system'), None)
    if not track:
        raise ValueError('没有可区分的电脑声音或导入音轨，原文字稿保留。')
    key, offset = track['key'], track.get('offset', 0.)
    wav = folder / (key + '.wav')
    count = int(count)
    if count < 0 or count > 20:
        raise ValueError('人数需要是自动或 1–20 人。')
    segmentation = MODEL_ROOT / 'segmentation.onnx'
    embedding = MODEL_ROOT / '3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx'
    if not segmentation.is_file() or not embedding.is_file():
        raise RuntimeError('本地说话人模型缺失，请检查 D:\\Apps\\CallNotes\\models\\diarization。音频已保留。')
    fingerprint = [POLICY, key, offset, wav.stat().st_size, wav.stat().st_mtime_ns, count,
                   segmentation.stat().st_mtime_ns, embedding.stat().st_mtime_ns]
    signature = hashlib.sha256(json.dumps(fingerprint).encode()).hexdigest()
    cached = folder / 'diarization.json'
    if cached.exists():
        previous = json.loads(cached.read_text(encoding='utf-8'))
        if previous.get('signature') == signature:
            previous = refine(previous, wav, offset, embedding, progress)
            save_json(cached, previous)
            return previous
    progress('正在本机区分说话人…')
    # Capture can be 48kHz stereo; conversion is in memory, never overwrites it.
    samples = load_samples(wav)
    duration = len(samples) / 16000
    rows = []
    if duration >= .3 and np.max(np.abs(samples), initial=0.) > .0001:
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation)), num_threads=2),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(embedding), num_threads=2),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=count if count else -1, threshold=.8),
            min_duration_on=.3, min_duration_off=.4)
        if not config.validate():
            raise RuntimeError('说话人模型配置无效，原音频保留。')
        engine = sherpa_onnx.OfflineSpeakerDiarization(config)
        last = [-1]
        def callback(done, total):
            percent = int(done * 100 / max(1, total))
            if percent // 10 != last[0]:
                last[0] = percent // 10
                progress(f'正在区分说话人 {percent}%…')
            return 0
        result = engine.process(samples, callback=callback).sort_by_start_time()
        identifiers = {}
        for row in result:
            start, end = max(0., row.start), min(duration, row.end)
            if end <= start:
                continue
            identifiers.setdefault(row.speaker, f'{key}-speaker-{len(identifiers) + 1}')
            rows.append(dict(start=round(start + offset, 3), end=round(end + offset, 3),
                             speaker_id=identifiers[row.speaker]))
    data = dict(signature=signature, policy=POLICY, track=key, requested_speakers=count,
                detected_speakers=len(set(r['speaker_id'] for r in rows)), duration=duration, turns=rows)
    data['warnings'] = []
    if (count == 0 and rows) or (count > 1 and data['detected_speakers'] < count):
        initial_count = data['detected_speakers']
        try:
            try:
                from .speaker_windows import recover_count
            except ImportError:
                from speaker_windows import recover_count
            recovery = recover_count(samples, offset, rows, embedding, count, key, progress)
            if recovery:
                data['base_turns'], data['base_detected_speakers'] = rows, initial_count
                data['turns'] = recovery.pop('turns')
                data['count_recovery'] = recovery
                data['detected_speakers'] = recovery['detected_speakers']
        except Exception as exc:
            data['warnings'].append('短片段声音复核未完成：' + str(exc))
        if count > 0 and data['detected_speakers'] != count:
            data['warnings'].append(f'设置为 {count} 人，但目前只能分出 {data["detected_speakers"]} 个声音组，尚未完整区分；可回听并指定清晰片段核对。')
    data = refine(data, wav, offset, embedding, progress, samples)
    save_json(cached, data)
    return data


def aliases_for(folder, signature):
    path = Path(folder) / 'speaker-names.json'
    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('signature') == signature:
            return data.get('names', {})
    return {}


if __name__ == '__main__':
    try:
        from speaker_alignment import align_speakers, count_warning
        folder = Path(sys.argv[1])
        data = identify(folder, int(sys.argv[2]), lambda text: print(json.dumps(dict(type='progress', text=text)), flush=True))
        value = json.loads((folder / 'transcript.json').read_text(encoding='utf-8'))
        segments = align_speakers(value['segments'], data, aliases_for(folder, data['signature']))
        warnings = list(data.get('warnings', []))
        mismatch = count_warning(data, segments)
        if mismatch:
            warnings.append(mismatch)
        print(json.dumps(dict(type='result', segments=segments, diarization=data, warnings=warnings), ensure_ascii=True), flush=True)
    except Exception as exc:
        print(json.dumps(dict(type='error', text=str(exc)), ensure_ascii=True), flush=True)
        sys.exit(1)
