from __future__ import annotations

import json
import os
import subprocess
import threading
import queue
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from .runtime import CACHE_ROOT, VOICE_ROOT, model_directory, worker_command, default_python


class Signals(QObject):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(str)
    delta = Signal(str)
    event = Signal(object)
    finished = Signal()


class Job(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = Signals()

    def run(self):
        try:
            result = self.function(self.signals)
            self.signals.result.emit(result)
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


class Transcriber:
    def __init__(self):
        self.proc = None
        self.cancelled = threading.Event()
        self.process_lock = threading.Lock()

    def run(self, folder, settings, progress, region=None):
        if self.cancelled.is_set():
            raise RuntimeError('处理已停止。')
        model_path = model_directory(settings)
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        cache = CACHE_ROOT
        (cache / 'tmp').mkdir(parents=True, exist_ok=True)
        env['TEMP'] = str(cache / 'tmp')
        env['TMP'] = env['TEMP']
        result, errors = None, []
        # stderr has native-library logs only; do not put them in UI or user chats.
        with (cache / 'asr-last.log').open('w', encoding='utf-8') as log:
            with self.process_lock:
                if self.cancelled.is_set():
                    raise RuntimeError('处理已停止。')
                self.proc = subprocess.Popen(worker_command('asr_worker', settings,
                                              folder, model_path, settings.get('asr_language', 'zh_en'),
                                          json.dumps(region), json.dumps({})), stdout=subprocess.PIPE,
                                             stderr=log, text=True, encoding='utf-8', errors='replace',
                                             env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            for line in self.proc.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event['type'] == 'progress':
                    progress(event['text'])
                elif event['type'] == 'segment':
                    progress('已识别至 ' + str(round(event['segment']['end'])) + ' 秒…')
                elif event['type'] == 'error':
                    errors.append(event['text'])
                elif event['type'] == 'result':
                    result = event
            return_code = self.proc.wait()
        if self.cancelled.is_set():
            raise RuntimeError('转录已中断，录音已保存，可稍后重试。')
        if return_code or result is None:
            raise RuntimeError('\n'.join(errors) or f'本地转录失败，录音保留。详情见 {cache / "asr-last.log"}')
        return result

    def identify_speakers(self, folder, settings, count, progress):
        if self.cancelled.is_set():
            raise RuntimeError('处理已停止。')
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['TEMP'] = env['TMP'] = str(CACHE_ROOT / 'tmp')
        Path(env['TEMP']).mkdir(parents=True, exist_ok=True)
        result, errors = None, []
        log_path = CACHE_ROOT / 'diarization-last.log'
        with log_path.open('w', encoding='utf-8') as log:
            with self.process_lock:
                if self.cancelled.is_set():
                    raise RuntimeError('处理已停止。')
                self.proc = subprocess.Popen(worker_command('diarization_worker', settings,
                    folder, count), stdout=subprocess.PIPE, stderr=log,
                    text=True, encoding='utf-8', errors='replace', env=env,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            for line in self.proc.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event['type'] == 'progress':
                    progress(event['text'])
                elif event['type'] == 'error':
                    errors.append(event['text'])
                elif event['type'] == 'result':
                    result = event
            code = self.proc.wait()
        if self.cancelled.is_set() or code or result is None:
            raise RuntimeError('；'.join(errors) or '说话人识别已中断，原文字稿保留。')
        return result

    def import_media(self, source, folder, settings, progress):
        if self.cancelled.is_set():
            raise RuntimeError('处理已停止。')
        progress('正在本机提取音轨，原文件保持不变…')
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        with self.process_lock:
            if self.cancelled.is_set():
                raise RuntimeError('导入已停止。')
            self.proc = subprocess.Popen(worker_command('media_worker', settings,
                source, folder), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace', env=env,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        stdout, stderr = self.proc.communicate()
        if self.cancelled.is_set():
            raise RuntimeError('导入已停止，原文件保持不变。')
        if self.proc.returncode:
            raise RuntimeError('导入失败：' + stderr.strip()[-500:])
        return json.loads(stdout)

    def stop(self):
        with self.process_lock:
            self.cancelled.set()
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()


class LiveTranscriber(Transcriber):
    def __init__(self):
        super().__init__()
        self.folder = None
        self.ready = threading.Event()
        self.run_lock = threading.Lock()
        self.events = queue.Queue()
        self.signature = None
        self.failure = ''

    @property
    def is_ready(self):
        return bool(self.ready.is_set() and not self.failure and self.proc and self.proc.poll() is None)

    def preload(self, settings, progress):
        if self.cancelled.is_set():
            raise RuntimeError('字幕处理已停止。')
        model = str(model_directory(settings))
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['TEMP'] = str(CACHE_ROOT / 'tmp')
        env['TMP'] = env['TEMP']
        (Path(env['TEMP'])).mkdir(parents=True, exist_ok=True)
        command = worker_command('live_worker', settings, '--server', model)
        signature = tuple(command)
        with self.process_lock:
            if self.signature != signature or not self.proc or self.proc.poll() is not None or self.failure:
                if self.cancelled.is_set():
                    raise RuntimeError('字幕处理已停止。')
                previous = self.proc
                self.proc = None
                if previous and previous.poll() is None:
                    previous.terminate()
                    previous.wait(timeout=5)
                self.ready.clear()
                self.failure = ''
                self.events = events = queue.Queue()
                self.signature = signature
                with (CACHE_ROOT / 'live-last.log').open('w', encoding='utf-8') as log:
                    self.proc = proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                               text=True, encoding='utf-8', errors='replace', bufsize=1, env=env,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

                def receive():
                    for line in proc.stdout:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if event['type'] == 'engine_ready':
                            if self.proc is proc:
                                self.ready.set()
                        elif event['type'] == 'progress':
                            progress(event['text'])
                        else:
                            if event['type'] == 'error' and event.get('scope') == 'engine' and self.proc is proc:
                                self.failure = event['text']
                                self.ready.set()
                            events.put(event)
                    if self.proc is proc:
                        self.failure = self.failure or '本地字幕引擎已退出；录音仍保存在本机。'
                        self.ready.set()
                    events.put({'type': 'error', 'text': '本地字幕引擎已退出。'})

                threading.Thread(target=receive, daemon=True).start()
        while not self.ready.wait(.1):
            if self.cancelled.is_set():
                raise RuntimeError('字幕处理已停止。')
        if self.failure or self.cancelled.is_set():
            raise RuntimeError(self.failure or '字幕处理已停止。')
        return {'ready': True, 'pid': self.proc.pid}

    def run(self, folder, settings, progress, caption):
        with self.run_lock:
            self.folder = Path(folder)
            try:
                self.preload(settings, progress)
                while not self.events.empty():
                    self.events.get_nowait()
                with self.process_lock:
                    if self.cancelled.is_set():
                        raise RuntimeError('字幕处理已停止。')
                    self.proc.stdin.write(json.dumps({'type': 'record', 'folder': str(folder),
                        'language_mode': settings.get('asr_language', 'zh_en')}, ensure_ascii=True) + '\n')
                    self.proc.stdin.flush()
                while not self.cancelled.is_set():
                    try:
                        event = self.events.get(timeout=.2)
                    except queue.Empty:
                        continue
                    if event['type'] in {'caption', 'ready'}:
                        caption(event)
                    elif event['type'] == 'result':
                        return event
                    elif event['type'] == 'error':
                        raise RuntimeError(event['text'])
                raise RuntimeError('字幕处理已停止，原始录音与已确认字幕保留。')
            finally:
                self.folder = None

    def finish(self, folder):
        from .storage import atomic_json
        atomic_json(Path(folder) / 'live-stop.json', {'finish': True})

