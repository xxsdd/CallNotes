"""Build self-contained UI/worker folders; model assets are staged separately."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess

PROJECT = Path(__file__).resolve().parent.parent


def run(command):
    print('Running', command[0], command[1:5], flush=True)
    subprocess.run(list(map(str, command)), cwd=PROJECT, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--ui-python', required=True)
    parser.add_argument('--worker-python', required=True)
    options = parser.parse_args()
    root = options.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ['PYINSTALLER_CONFIG_DIR'] = str(root / 'pyinstaller-cache')
    os.environ['TEMP'] = os.environ['TMP'] = str(root / 'tmp')
    (root / 'tmp').mkdir(exist_ok=True)
    common = ['-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--noupx',
              '--distpath', str(root / 'dist'), '--workpath', str(root / 'build'),
              '--specpath', str(root / 'spec')]
    run([options.ui_python, *common, '--name', 'CallNotes', '--windowed',
         '--icon', PROJECT / 'assets' / 'arknights-bighead-v1.ico',
         '--add-data', str(PROJECT / 'assets' / 'arknights-bighead-v1.ico') + ';assets',
         '--add-data', str(PROJECT / 'assets' / 'arknights-bighead-v1.png') + ';assets',
         '--add-data', str(PROJECT / 'CHANGELOG.md') + ';.',
         '--add-binary', str(PROJECT / 'callnotes/native/process_loopback.dll') + ';callnotes/native',
         '--collect-data', 'opencc', PROJECT / 'main.py'])
    worker_modules = ['asr_worker', 'live_worker', 'media_worker', 'diarization_worker',
                      'text_normalization', 'asr_quality', 'inference', 'runtime',
                      'speaker_alignment', 'speaker_windows', 'speaker_refinement']
    extra = []
    for name in worker_modules:
        extra += ['--hidden-import', name]
    for name in ['faster_whisper', 'ctranslate2', 'sherpa_onnx', 'sherpa_onnx_core', 'opencc']:
        extra += ['--collect-all', name]
    run([options.worker_python, *common, '--name', 'CallNotesWorker', '--console',
         '--paths', PROJECT / 'callnotes', *extra, PROJECT / 'worker_entry.py'])
    bundle = root / 'dist' / 'CallNotes'
    shutil.copytree(root / 'dist' / 'CallNotesWorker', bundle / 'worker', dirs_exist_ok=True)
    shutil.copytree(root / 'models', bundle / 'models', dirs_exist_ok=True)
    shutil.copytree(root / 'vendor', bundle / 'vendor', dirs_exist_ok=True)
    shutil.copytree(PROJECT / 'packaging' / 'notices', bundle / 'ThirdPartyNotices', dirs_exist_ok=True)
    (bundle / '使用说明.txt').write_text('通话笺 0.1.3\n\n双击 CallNotes.exe。无需另装 Python 或 Voice Studio。\n'
        '内置 large-v3-turbo 与说话人模型；CPU 可用，速度取决于电脑性能。\n'
        'AI 分析：设置 → AI 分析 → 登录 Codex。请使用自己的账号。\n'
        '录音和字幕均在本机完成，分析只发送明确提交的文字。\n'
        '默认资料位置：%LOCALAPPDATA%\\CallNotes\\Data；已有 D 盘记录保持原位置。\n'
        '卸载保留个人录音、文字稿与账号登录。\n', encoding='utf-8-sig')
    print(json.dumps({'bundle': str(bundle), 'bytes': sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file())}), flush=True)


if __name__ == '__main__':
    main()
