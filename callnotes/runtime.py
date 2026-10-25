"""Portable runtime locations; never copy a user's credentials or recordings."""
from __future__ import annotations

import os
from pathlib import Path
import sys

FROZEN = bool(getattr(sys, 'frozen', False))
APP_ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
# Worker lives one folder below the application executable.
if FROZEN and APP_ROOT.name.lower() == 'worker':
    APP_ROOT = APP_ROOT.parent
LOCAL = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'CallNotes'
LEGACY_DATA = Path(r'D:\Apps\CallNotes-Data')
DATA_ROOT = Path(os.environ.get('CALLNOTES_DATA') or (
    str(LEGACY_DATA) if (LEGACY_DATA / 'records.sqlite3').is_file() else str(LOCAL / 'Data')))
CACHE_ROOT = Path(os.environ.get('CALLNOTES_CACHE') or (
    r'D:\Apps\CallNotes-Cache' if not FROZEN and Path(r'D:\Apps\CallNotes-Cache').is_dir() else str(LOCAL / 'Cache')))
VOICE_ROOT = Path(r'D:\Apps\VoiceStudio-Environment')


def bundled_worker():
    return APP_ROOT / 'worker' / 'CallNotesWorker.exe'


def default_python():
    if bundled_worker().is_file():
        return str(bundled_worker())
    legacy = VOICE_ROOT / 'project' / '.venv' / 'Scripts' / 'python.exe'
    return str(legacy if legacy.is_file() else Path(sys.executable))


def model_directory(settings):
    chosen = settings.get('asr_model', '')
    if chosen:
        if not (Path(chosen) / 'model.bin').is_file():
            raise RuntimeError('所选字幕模型不完整，请在设置中重新选择，或清空路径使用内置模型。')
        return Path(chosen)
    built_in = APP_ROOT / 'models' / 'faster-whisper-large-v3-turbo'
    if (built_in / 'model.bin').is_file():
        return built_in
    existing = sorted((VOICE_ROOT / 'models' / 'models--Systran--faster-whisper-large-v3' / 'snapshots').glob('*/model.bin'))
    if existing:
        return existing[0].parent
    raise RuntimeError('字幕模型缺失，请重新安装完整版；原始录音仍会保存在本机。')


def worker_command(name, settings, *args):
    if bundled_worker().is_file():
        return [str(bundled_worker()), name, *map(str, args)]
    python = Path(settings.get('asr_python') or default_python())
    if not python.is_file():
        raise RuntimeError('转录环境不存在，请使用完整安装版，或在设置中指定 Python。')
    return [str(python), str(Path(__file__).with_name(name + '.py')), *map(str, args)]


def prepare_environment():
    for folder in (CACHE_ROOT, CACHE_ROOT / 'tmp', CACHE_ROOT / 'numba'):
        folder.mkdir(parents=True, exist_ok=True)
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['TEMP'] = os.environ['TMP'] = str(CACHE_ROOT / 'tmp')
    os.environ['NUMBA_CACHE_DIR'] = str(CACHE_ROOT / 'numba')
    os.environ.setdefault('HF_HOME', str(CACHE_ROOT / 'models'))
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'


def speaker_models():
    built_in = APP_ROOT / 'models' / 'diarization'
    return built_in if built_in.is_dir() else Path(r'D:\Apps\CallNotes\models\diarization')
