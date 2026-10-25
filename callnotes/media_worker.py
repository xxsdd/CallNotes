"""Streaming local media import, using PyAV bundled with the ASR runtime."""
import json
import os
import sys
import wave
from pathlib import Path


def convert(source, folder):
    import av
    source, folder = Path(source), Path(folder)
    target = folder / 'imported.wav'
    temporary = folder / 'imported.wav.tmp'
    frames = 0
    try:
        with av.open(str(source)) as container:
            if not container.streams.audio:
                raise ValueError('这个文件没有音轨，请选择带声音的音视频。')
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
            with wave.open(str(temporary), 'wb') as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(16000)
                for frame in container.decode(stream):
                    for converted in resampler.resample(frame):
                        out.writeframesraw(converted.to_ndarray().tobytes())
                        frames += converted.samples
                for converted in resampler.resample(None):
                    out.writeframesraw(converted.to_ndarray().tobytes())
                    frames += converted.samples
        if frames == 0:
            raise ValueError('这个文件的音轨是空的。')
        os.replace(temporary, target)
        meta = dict(version=2, capture_mode='import', source_name=source.name,
                    tracks=[dict(key='imported', speaker='导入音频', rate=16000,
                                 channels=1, offset=0., duration=frames / 16000)])
        meta_path = folder / 'audio.json.tmp'
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
        os.replace(meta_path, folder / 'audio.json')
        return dict(duration=frames / 16000)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        print(json.dumps(convert(sys.argv[1], sys.argv[2])), flush=True)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
