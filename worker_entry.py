"""Frozen worker dispatch: no Qt, user database or microphone initialization."""
import json
import multiprocessing
import runpy
import sys
from pathlib import Path


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'callnotes'))
    from callnotes.runtime import prepare_environment
    prepare_environment()
    name = sys.argv[1] if len(sys.argv) > 1 else ''
    if name == 'probe':
        import av, ctranslate2, faster_whisper, sherpa_onnx, scipy, opencc
        print(json.dumps({'ok': True, 'frozen': bool(getattr(sys, 'frozen', False)),
                          'backend': ctranslate2.__version__}), flush=True)
        return
    if name not in ('asr_worker', 'live_worker', 'media_worker', 'diarization_worker'):
        raise ValueError('Unknown worker')
    sys.argv = [name + '.py', *sys.argv[2:]]
    runpy.run_module(name, run_name='__main__')


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
