from __future__ import annotations

import math
import os
import threading
import time
from pathlib import Path

import numpy as np
import pyaudiowpatch as pa

from .storage import atomic_json, finalize_pcm
from .process_audio import ProcessLoopbackStream


def devices():
    with pa.PyAudio() as audio:
        host = audio.get_host_api_info_by_type(pa.paWASAPI)
        default_in = int(host['defaultInputDevice'])
        default_out = int(host['defaultOutputDevice'])
        microphones, outputs = [], []
        for index in range(audio.get_device_count()):
            device = audio.get_device_info_by_index(index)
            if device['hostApi'] != host['index']:
                continue
            if device.get('isLoopbackDevice'):
                device['default'] = False
                outputs.append(device)
            elif device['maxInputChannels'] > 0:
                device['default'] = index == default_in
                microphones.append(device)
        try:
            loop = audio.get_default_wasapi_loopback()
            for item in outputs:
                item['default'] = item['index'] == loop['index']
        except Exception:
            pass
        return microphones, outputs


class Recorder:
    def __init__(self, folder: Path, mic, loopback):
        self.folder = Path(folder)
        self.audio = pa.PyAudio()
        self.stop_event = threading.Event()
        self.streams = []
        self.threads = []
        self.levels = {'mic': 0., 'system': 0.}
        self.errors = []
        self.running = False
        self.started = None
        self.capture_lock = threading.RLock()
        self.paused = False
        self.pause_started = None
        self.paused_seconds = 0.
        self.meta = {'version': 2, 'tracks': []}
        try:
            for key, device, name in [('mic', mic, '我'), ('system', loopback, '对方')]:
                if key == 'system' and isinstance(device, dict):
                    if device.get('kind') != 'process':
                        raise ValueError('无效的通话软件录音来源')
                    stream = ProcessLoopbackStream(device)
                    self.streams.append(stream)
                    self.meta['capture_mode'] = 'process'
                    self.meta['tracks'].append({'key': key, 'speaker': name,
                        'rate': stream.rate, 'channels': stream.channels,
                        'device': stream.name, 'offset': 0., 'source': dict(device)})
                    continue
                info = self.audio.get_device_info_by_index(int(device))
                rate = int(info['defaultSampleRate'])
                channels = min(2, int(info['maxInputChannels']))
                if channels < 1:
                    raise RuntimeError(f'{name}的录音设备没有可用输入')
                stream = self.audio.open(format=pa.paInt16, channels=channels, rate=rate,
                                         input=True, input_device_index=int(device),
                                         frames_per_buffer=1024, start=False)
                self.streams.append(stream)
                self.meta['tracks'].append({'key': key, 'speaker': name, 'rate': rate,
                                           'channels': channels, 'device': info['name'], 'offset': 0.})
            self.meta.setdefault('capture_mode', 'all')
        except Exception:
            self.close_streams()
            raise

    def start(self):
        self.started = time.perf_counter()
        self.meta['wall_started'] = time.time()
        atomic_json(self.folder / 'audio.json', self.meta)
        self.running = True
        try:
            for stream, track in zip(self.streams, self.meta['tracks']):
                stream.start_stream()
                track['offset'] = time.perf_counter() - self.started
                thread = threading.Thread(target=self._capture, args=(stream, track), daemon=True)
                thread.start()
                self.threads.append(thread)
            atomic_json(self.folder / 'audio.json', self.meta)
        except Exception:
            self.stop()
            raise

    def _capture(self, stream, track):
        frames = 0
        last_flush = time.perf_counter()
        last_data = time.perf_counter()
        # If a loopback endpoint stops emitting while idle, insert silence using
        # PortAudio's stream clock when data resumes. This keeps the other track aligned.
        with (self.folder / (track['key'] + '.pcm')).open('wb') as raw:
            try:
                while not self.stop_event.is_set():
                    if self.paused:
                        raw.flush()
                    available = stream.get_read_available()
                    if available <= 0:
                        if not stream.is_active():
                            raise RuntimeError('设备停止提供音频')
                        if time.perf_counter() - last_data > .25:
                            # Some loopback devices emit no frames during silence.
                            self.levels[track['key']] = 0.
                        self.stop_event.wait(.01)
                        continue
                    count = min(available, 4096)
                    with self.capture_lock:
                        count = min(stream.get_read_available(), 4096)
                        if count <= 0:
                            continue
                        data = stream.read(count, exception_on_overflow=True)
                        if self.paused:
                            raw.flush()
                            self.levels[track['key']] = 0.
                            continue
                        elapsed = self.elapsed
                        # Commit under the same lock as pause, so no data read
                        # before/while paused can be appended after resuming.
                        expected = int((elapsed - track['offset']) * track['rate'])
                        gap = expected - frames - count
                        if gap > track['rate'] * .20 and available <= 4096:
                            remaining = gap * track['channels'] * 2
                            while remaining:
                                count_bytes = min(remaining, 1024 * 1024)
                                raw.write(b'\0' * count_bytes)
                                remaining -= count_bytes
                            frames += gap
                            track['silence_inserted_seconds'] = track.get('silence_inserted_seconds', 0) + gap / track['rate']
                        raw.write(data)
                        frames += len(data) // (2 * track['channels'])
                    now = time.perf_counter()
                    # Bound clock correction: do not shift ordinary scheduling jitter.
                    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                    rms = float(np.sqrt(np.mean(samples * samples))) / 32768 if len(samples) else 0.
                    self.levels[track['key']] = min(1., math.sqrt(rms) * 2)
                    last_data = now
                    if now - last_flush >= .25:
                        raw.flush()
                        os.fsync(raw.fileno())
                        last_flush = now
            except Exception as exc:
                self.errors.append(f'{track["speaker"]}：{exc}')
                self.stop_event.set()
            finally:
                raw.flush()
                os.fsync(raw.fileno())

    @property
    def elapsed(self):
        if self.started is None:
            return 0.
        now = self.pause_started if self.paused else time.perf_counter()
        return max(0., now - self.started - self.paused_seconds)

    def set_paused(self, paused):
        with self.capture_lock:
            if self.paused == paused:
                return
            if paused:
                self.pause_started = time.perf_counter()
            else:
                # Drain buffered pause audio before reopening the write gate.
                for stream in self.streams:
                    available = stream.get_read_available()
                    if available > 0:
                        stream.read(available, exception_on_overflow=True)
                self.paused_seconds += time.perf_counter() - self.pause_started
            self.paused = paused
            self.levels = {'mic': 0., 'system': 0.}
            atomic_json(self.folder / 'record-state.json', dict(paused=paused,
                paused_seconds=self.paused_seconds, pause_wall=time.time() if paused else None))

    def close_streams(self):
        for stream in self.streams:
            try:
                if stream.is_active():
                    stream.stop_stream()
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass
        self.streams = []
        self.audio.terminate()

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=5)
        # A blocked driver must not be mistaken for a clean save.
        stuck = any(t.is_alive() for t in self.threads)
        self.close_streams()
        if stuck:
            for thread in self.threads:
                thread.join(timeout=2)
        if any(t.is_alive() for t in self.threads):
            raise RuntimeError('音频驱动未停止，原始录音已保留，请等待后再退出。')
        for track in self.meta['tracks']:
            finalize_pcm(self.folder, track)
        self.meta['errors'] = list(self.errors)
        self.meta['wall_duration'] = time.perf_counter() - self.started if self.started else 0
        self.meta['active_duration'] = self.elapsed
        atomic_json(self.folder / 'audio.json', self.meta)
        self.running = False
        return max((t.get('duration', 0) + t['offset'] for t in self.meta['tracks']), default=0.)
