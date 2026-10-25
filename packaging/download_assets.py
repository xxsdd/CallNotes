from pathlib import Path
import hashlib
import json
import urllib.request
import zipfile
import argparse
import tarfile
import shutil

parser = argparse.ArgumentParser()
parser.add_argument('--root', type=Path, required=True)
ROOT = parser.parse_args().root.resolve()
ROOT.mkdir(parents=True, exist_ok=True)


def download(url, target, expected=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and (not expected or digest(target) == expected):
        print('Reusing', target.name, flush=True)
        return
    part = target.with_suffix(target.suffix + '.part')
    req = urllib.request.Request(url, headers={'User-Agent': 'CallNotes-build/0.1.3'})
    with urllib.request.urlopen(req, timeout=90) as response, part.open('wb') as out:
        while block := response.read(4 * 1024 * 1024):
            out.write(block)
    if expected and digest(part) != expected:
        raise ValueError('SHA256 mismatch: ' + target.name)
    part.replace(target)
    print('Ready', target.name, target.stat().st_size, flush=True)


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


revision = '0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf'
model = ROOT / 'models' / 'faster-whisper-large-v3-turbo'
for name in ['config.json', 'preprocessor_config.json', 'tokenizer.json', 'vocabulary.json', 'README.md', 'model.bin']:
    download(f'https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo/resolve/{revision}/{name}',
             model / name, 'e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da' if name == 'model.bin' else None)
(model / 'source.json').write_text(json.dumps(dict(repo='dropbox-dash/faster-whisper-large-v3-turbo',
    revision=revision, license='MIT', sha256=digest(model / 'model.bin')), indent=2), encoding='utf-8')
archive = ROOT / 'downloads' / 'codex-windows-0.164.0.zip'
download('https://github.com/openai/codex/releases/download/rust-v0.164.0/codex-x86_64-pc-windows-msvc.exe.zip', archive,
         '349827ef53a06a27c7ad9f93ad97a5ac019b0975ed7ab669f73bb0deccd7b4fa')
with zipfile.ZipFile(archive) as bundle:
    names = [x for x in bundle.namelist() if x.endswith('.exe') and 'codex-x86_64' in x]
    if len(names) != 1:
        raise ValueError('Unexpected Codex bundle contents: ' + repr(bundle.namelist()))
    destination = ROOT / 'vendor' / 'codex.exe'
    destination.parent.mkdir(exist_ok=True)
    with destination.open('wb') as target:
        target.write(bundle.read(names[0]))
download('https://raw.githubusercontent.com/openai/codex/rust-v0.164.0/LICENSE', ROOT / 'vendor' / 'Codex-LICENSE')
diarization = ROOT / 'models' / 'diarization'
segmentation_archive = ROOT / 'downloads' / 'segmentation.tar.bz2'
download('https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2',
         segmentation_archive, '24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488')
diarization.mkdir(parents=True, exist_ok=True)
with tarfile.open(segmentation_archive) as archive:
    model_members = [m for m in archive.getmembers() if m.isfile() and m.name.endswith('/model.onnx')]
    if len(model_members) != 1:
        raise ValueError('Unexpected segmentation archive')
    with archive.extractfile(model_members[0]) as source, (diarization / 'segmentation.onnx').open('wb') as out:
        shutil.copyfileobj(source, out)
download('https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx',
         diarization / '3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx',
         '1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b')
print('Installer assets ready', flush=True)
