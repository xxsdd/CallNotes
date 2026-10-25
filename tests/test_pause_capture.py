"""Exercise the real capture writer without opening audio hardware."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from callnotes.audio import Recorder


class BufferedStream:
    def __init__(self):
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.active = True

    def feed(self, value):
        with self.lock:
            self.buffer.extend(np.full(800, value, dtype=np.int16).tobytes())

    def get_read_available(self):
        with self.lock:
            return len(self.buffer) // 2

    def read(self, count, **kwargs):
        with self.lock:
            result = bytes(self.buffer[:count * 2])
            del self.buffer[:count * 2]
            return result

    def start_stream(self):
        pass

    def is_active(self):
        return self.active

    def stop_stream(self):
        self.active = False

    def close(self):
        pass


class FakeAudio:
    def __init__(self):
        self.streams = []

    def get_device_info_by_index(self, index):
        return dict(defaultSampleRate=16000, maxInputChannels=1, name='Synthetic')

    def open(self, **kwargs):
        stream = BufferedStream()
        self.streams.append(stream)
        return stream

    def terminate(self):
        pass


class CapturePauseTests(unittest.TestCase):
    def test_pause_discards_audio_and_freezes_common_clock(self):
        with tempfile.TemporaryDirectory() as directory, patch('callnotes.audio.pa.PyAudio', FakeAudio):
            recorder = Recorder(Path(directory), 0, 1)
            recorder.start()
            for stream in recorder.streams:
                stream.feed(1200)
            time.sleep(.09)
            recorder.set_paused(True)
            frozen = recorder.elapsed
            for stream in recorder.streams:
                stream.feed(25000)
            time.sleep(.15)
            self.assertAlmostEqual(recorder.elapsed, frozen, places=5)
            recorder.set_paused(False)
            for stream in recorder.streams:
                stream.feed(-1200)
            time.sleep(.09)
            recorder.set_paused(True)
            duration = recorder.stop()
            self.assertLess(duration, .4)
            self.assertFalse(recorder.errors)
            for key in ('mic', 'system'):
                samples = np.fromfile(Path(directory) / (key + '.pcm'), dtype=np.int16)
                self.assertIn(1200, samples)
                self.assertIn(-1200, samples)
                self.assertNotIn(25000, samples)
                self.assertTrue((Path(directory) / (key + '.wav')).is_file())
