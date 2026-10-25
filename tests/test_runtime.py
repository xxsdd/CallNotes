import os
from pathlib import Path
import tempfile
import numpy
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from callnotes import runtime
from callnotes.inference import load_whisper


class RuntimeTests(unittest.TestCase):
    def test_bundle_resolves_model_and_worker_without_legacy_paths(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime, 'APP_ROOT', Path(folder)):
            model = Path(folder) / 'models' / 'faster-whisper-large-v3-turbo'
            model.mkdir(parents=True)
            (model / 'model.bin').touch()
            worker = Path(folder) / 'worker' / 'CallNotesWorker.exe'
            worker.parent.mkdir()
            worker.touch()
            self.assertEqual(runtime.model_directory({}), model)
            self.assertEqual(runtime.worker_command('media_worker', {'asr_python': r'Z:\missing.exe'}, 'a', 'b'),
                             [str(worker), 'media_worker', 'a', 'b'])

    def test_explicit_missing_model_is_reported_instead_of_silently_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, '所选字幕模型不完整'):
                runtime.model_directory({'asr_model': folder})

    def test_existing_custom_model_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'model.bin').touch()
            self.assertEqual(runtime.model_directory({'asr_model': folder}), Path(folder))

    def test_no_gpu_uses_int8_cpu(self):
        factory = MagicMock()
        with patch.dict('sys.modules', {'ctranslate2': SimpleNamespace(get_cuda_device_count=lambda: 0),
                                       'faster_whisper': SimpleNamespace(WhisperModel=factory)}), patch.dict(os.environ, {'CALLNOTES_ASR_DEVICE': 'auto'}):
            load_whisper('model', lambda _: None)
        self.assertEqual(factory.call_args.kwargs['device'], 'cpu')
        self.assertEqual(factory.call_args.kwargs['compute_type'], 'int8')

    def test_missing_cuda_library_on_first_inference_falls_back(self):
        gpu, cpu = MagicMock(), object()
        gpu.transcribe.side_effect = RuntimeError('cudnn missing')
        factory = MagicMock(side_effect=[gpu, cpu])
        with patch.dict('sys.modules', {'ctranslate2': SimpleNamespace(get_cuda_device_count=lambda: 1),
                                       'faster_whisper': SimpleNamespace(WhisperModel=factory)}), patch.dict(os.environ, {'CALLNOTES_ASR_DEVICE': 'auto'}):
            self.assertIs(load_whisper('model', lambda _: None), cpu)
        self.assertEqual([call.kwargs['device'] for call in factory.call_args_list], ['cuda', 'cpu'])

    def test_forced_cpu_never_initializes_cuda(self):
        cuda_query = MagicMock()
        factory = MagicMock()
        with patch.dict('sys.modules', {'ctranslate2': SimpleNamespace(get_cuda_device_count=cuda_query),
                                       'faster_whisper': SimpleNamespace(WhisperModel=factory)}), patch.dict(os.environ, {'CALLNOTES_ASR_DEVICE': 'cpu'}):
            load_whisper('model', lambda _: None)
        cuda_query.assert_not_called()


if __name__ == '__main__':
    unittest.main()
