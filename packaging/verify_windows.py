"""Exercise installed binaries with isolated data, no Python/CUDA on their PATH."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import wave


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--speech', type=Path)
    options = parser.parse_args()
    app, output = options.app.resolve(), options.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(CALLNOTES_DATA=str(output / 'data'), CALLNOTES_CACHE=str(output / 'cache'),
               CALLNOTES_ASR_DEVICE='cpu', CODEX_HOME=str(output / 'codex'), QT_QPA_PLATFORM='offscreen',
               PATH=os.environ.get('SystemRoot', r'C:\Windows') + r'\System32', HF_HUB_OFFLINE='1')
    worker = app / 'worker' / 'CallNotesWorker.exe'
    results = {}

    def run(command, timeout=180):
        completed = subprocess.run(list(map(str, command)), env=env, capture_output=True,
                                   text=True, encoding='utf-8', errors='replace', timeout=timeout)
        if completed.returncode:
            raise RuntimeError(completed.stdout[-4000:] + completed.stderr[-4000:])
        return completed.stdout

    results['worker'] = json.loads(run([worker, 'probe']))
    assert results['worker']['frozen'] and results['worker']['ok']
    for mode in ['day', 'night']:
        data = output / 'data'
        data.mkdir(exist_ok=True)
        (data / 'settings.json').write_text(json.dumps({'theme_mode': mode}), encoding='utf-8')
        report = output / ('ui-' + mode + '.json')
        run([app / 'CallNotes.exe', '--self-test', report], timeout=60)
        results[mode] = json.loads(report.read_text(encoding='utf-8'))
        assert results[mode]['frozen'] and results[mode]['worker']
        assert not results[mode]['capture_error'], results[mode]
        assert Path(results[mode]['data']).resolve() == data.resolve()
    results['codex'] = run([app / 'vendor' / 'codex.exe', '--version']).strip()
    source = options.speech
    if source is None:
        source = output / 'silence.wav'
        with wave.open(str(source), 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\0\0' * 16000)
    record = output / 'import'
    record.mkdir(exist_ok=True)
    results['media'] = json.loads(run([worker, 'media_worker', source, record]))
    events = [json.loads(line) for line in run([worker, 'asr_worker', record,
        app / 'models' / 'faster-whisper-large-v3-turbo', 'en', 'null', '{}'], timeout=300).splitlines() if line.startswith('{')]
    errors = [e for e in events if e.get('type') == 'error']
    if errors:
        raise RuntimeError(str(errors))
    final = next(e for e in events if e.get('type') == 'result')
    results['transcript'] = final
    if options.speech:
        text = ' '.join(s['text'] for s in final['segments']).lower()
        assert 'budget' in text and 'friday' in text, text
    results['ok'] = True
    (output / 'verification.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'version': results['day']['version'], 'segments': len(final['segments'])}), flush=True)


if __name__ == '__main__':
    main()
