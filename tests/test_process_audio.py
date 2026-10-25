"""No hardware capture: verify Recorder's source boundary and failure cleanup."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from callnotes.audio import Recorder
from callnotes.citations import cite_markdown, parse_source_target
from callnotes.process_audio import _render_target


class SourceBoundaryTests(unittest.TestCase):
    def render_api(self, pids):
        library = MagicMock()
        def emit(output, capacity, count):
            for i, pid in enumerate(pids):
                output[i] = pid
            count._obj.value = len(pids)
            return 0
        library.cn_rendering_pids.side_effect = emit
        return library

    def test_actual_audio_worker_is_selected_without_other_application(self):
        tree = {10: ('discord.exe', 1), 11: ('discord.exe', 10), 12: ('discord.exe', 11), 20: ('browser.exe', 1)}
        target = dict(pid=10, created=110, name='Discord')
        with patch('callnotes.process_audio._process_entries', return_value=tree), patch('callnotes.process_audio.process_creation_time', side_effect=lambda pid: pid + 100):
            actual = _render_target(self.render_api([12, 20]), target)
            self.assertEqual(actual['pid'], 12)
            self.assertEqual(actual['created'], 112)
            self.assertEqual(actual['name'], 'Discord')

    def test_no_app_audio_or_reused_root_pid_is_rejected(self):
        target = dict(pid=10, created=110)
        with patch('callnotes.process_audio._process_entries', return_value={10: ('weixin.exe', 1), 20: ('browser.exe', 1)}), patch('callnotes.process_audio.process_creation_time', return_value=110):
            with self.assertRaisesRegex(RuntimeError, '先接通'):
                _render_target(self.render_api([20]), target)
        with patch('callnotes.process_audio.process_creation_time', return_value=999):
            with self.assertRaisesRegex(RuntimeError, '重新启动'):
                _render_target(self.render_api([10]), target)

    def test_unsupported_deep_audio_branches_are_not_silently_partial(self):
        tree = {10: ('discord.exe', 1), 11: ('discord.exe', 10), 12: ('discord.exe', 11), 13: ('discord.exe', 10)}
        with patch('callnotes.process_audio._process_entries', return_value=tree), patch('callnotes.process_audio.process_creation_time', return_value=110):
            with self.assertRaisesRegex(RuntimeError, '无法完整分离'):
                _render_target(self.render_api([12, 13]), dict(pid=10, created=110))

    def test_historical_parent_pid_does_not_include_an_unrelated_process(self):
        tree = {10: ('weixin.exe', 1), 12: ('browser.exe', 10)}
        with patch('callnotes.process_audio._process_entries', return_value=tree), patch('callnotes.process_audio.process_creation_time', side_effect=lambda pid: 110 if pid == 10 else 90):
            with self.assertRaisesRegex(RuntimeError, '先接通'):
                _render_target(self.render_api([12]), dict(pid=10, created=110))

    def test_grouping_stays_with_statement_and_preserves_clicked_badge(self):
        import re
        value = cite_markdown('原话 [S0001][S0002]。\n另一个结论 [S0003]。', 8)
        links = re.findall(r'\((source:[^)]*)\)', value)
        self.assertEqual([parse_source_target(link) for link in links],
            [(8, [0, 1], 0), (8, [0, 1], 1), (8, [2], 2)])
        with self.assertRaises(ValueError):
            parse_source_target('source:8:0,1?selected=5')

    def test_process_source_never_opens_system_loopback_device(self):
        target = dict(kind='process', pid=123, created=456, exe='weixin.exe', name='微信')
        with tempfile.TemporaryDirectory() as root, patch('callnotes.audio.pa.PyAudio') as audio, patch('callnotes.audio.ProcessLoopbackStream') as source:
            audio.return_value.get_device_info_by_index.return_value = dict(defaultSampleRate=16000, maxInputChannels=1, name='合成麦克风')
            source.return_value.rate, source.return_value.channels, source.return_value.name = 48000, 2, '微信'
            recorder = Recorder(Path(root), 42, target)
            self.assertEqual(audio.return_value.get_device_info_by_index.call_args_list[0].args, (42,))
            self.assertEqual(audio.return_value.open.call_count, 1)
            source.assert_called_once_with(target)
            self.assertEqual(recorder.meta['capture_mode'], 'process')
            self.assertEqual(recorder.meta['tracks'][1]['source'], target)
            source.return_value.is_active.side_effect = RuntimeError('目标退出')
            recorder.close_streams()
            source.return_value.close.assert_called_once()

    def test_failed_process_activation_closes_microphone_and_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as root, patch('callnotes.audio.pa.PyAudio') as audio, patch('callnotes.audio.ProcessLoopbackStream', side_effect=RuntimeError('所选软件退出')):
            audio.return_value.get_device_info_by_index.return_value = dict(defaultSampleRate=16000, maxInputChannels=1, name='麦克风')
            with self.assertRaisesRegex(RuntimeError, '所选软件退出'):
                Recorder(Path(root), 42, dict(kind='process', pid=123, created=456))
            self.assertEqual(audio.return_value.open.call_count, 1)
            audio.return_value.open.return_value.close.assert_called_once()
            audio.return_value.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
