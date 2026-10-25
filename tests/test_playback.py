"""Synthetic playback timing and real Qt key delivery; no capture or cloud."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_MEDIA_BACKEND'] = 'windows'
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QVBoxLayout
from callnotes.storage import Store
from callnotes.ui import MainWindow


class PlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.patches = [patch.object(MainWindow, 'load_models'),
                        patch('callnotes.ui.devices', return_value=([], [])),
                        patch('callnotes.ui.enumerate_voice_apps', return_value=[])]
        for item in self.patches:
            item.start()
        self.window = MainWindow(self.store, prewarm=False)
        self.window.timer.stop()
        self.rid = self.store.create('播放测试')
        self.store.update(self.rid, duration=60, status='ready')
        self.store.save_transcript(self.rid, [dict(start=0, end=60, text='合成文字',
                                                   track='system', speaker='对方')])
        self.window.show_record(self.rid)
        self.window.players = dict(mic=MagicMock(), system=MagicMock())
        self.window.offsets = dict(mic=0., system=.5)
        self.window.show()
        self.window.activateWindow()
        self.window.timeline.setFocus()
        QApplication.processEvents()

    def tearDown(self):
        self.window.close()
        QApplication.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.store.db.close()
        self.tmp.cleanup()

    def test_space_and_arrows_drive_player_and_clamp_at_bounds(self):
        w = self.window
        with patch('callnotes.ui.time.perf_counter', return_value=100):
            QTest.keyClick(w.timeline, Qt.Key_Space)
            self.assertTrue(w.playing)
            QTest.keyClick(w.timeline, Qt.Key_Right)
            self.assertEqual(w.play_position, 5)
            self.assertTrue(w.playing)
            QTest.keyClick(w.timeline, Qt.Key_Space)
            self.assertFalse(w.playing)
            QTest.keyClick(w.timeline, Qt.Key_Left)
            QTest.keyClick(w.timeline, Qt.Key_Left)
            self.assertEqual(w.play_position, 0)
            self.assertFalse(w.playing)
            w.seek(59)
            QTest.keyClick(w.timeline, Qt.Key_Right)
            self.assertEqual(w.play_position, 60)
            self.assertFalse(w.playing)
            self.assertEqual(w.slider.value(), 60000)
            QTest.keyClick(w.timeline, Qt.Key_Space)
            self.assertEqual(w.play_position, 0)
            self.assertTrue(w.playing)

    def test_rate_change_preserves_position_then_advances_both_tracks(self):
        w = self.window
        with patch('callnotes.ui.time.perf_counter', return_value=100):
            w.seek(10)
        with patch('callnotes.ui.time.perf_counter', return_value=102):
            w.speed_box.setCurrentIndex(w.speed_box.findData(2.))
            self.assertEqual(w.play_position, 12)
            self.assertTrue(w.playing)
            self.assertEqual(w.playback_rate, 2.)
            for player in w.players.values():
                player.setPlaybackRate.assert_called_with(2.)
            w.players['system'].setPosition.assert_called_once_with(9500)
        with patch('callnotes.ui.time.perf_counter', return_value=105):
            self.assertEqual(w.playback_position(), 18)
            w.pause()
            self.assertEqual(w.play_position, 18)
            self.assertEqual(w.slider.value(), 18000)
            self.assertEqual(w.play_label.text(), '00:18 / 01:00')
            w.speed_box.setCurrentIndex(w.speed_box.findData(.5))
            self.assertFalse(w.playing)
            self.assertEqual(w.play_position, 18)
            self.assertEqual(self.store.settings()['playback_rate'], .5)

    def test_tick_uses_rate_and_stops_at_actual_duration(self):
        w = self.window
        with patch('callnotes.ui.time.perf_counter', return_value=100):
            w.speed_box.setCurrentIndex(w.speed_box.findData(2.))
            w.seek(58)
        with patch('callnotes.ui.time.perf_counter', return_value=102):
            w.tick()
            self.assertFalse(w.playing)
            self.assertEqual(w.play_position, 60)
            self.assertEqual(w.play_label.text(), '01:00 / 01:00')
            self.assertEqual(w.slider.value(), 60000)

    def test_typing_space_and_cursor_movement_do_not_control_playback(self):
        w = self.window
        w.ai_button.setChecked(True)
        w.toggle_ai_panel(True)
        w.question.setFocus()
        QTest.keyClicks(w.question, 'a b')
        QTest.keyClick(w.question, Qt.Key_Left)
        QTest.keyClick(w.question, Qt.Key_Space)
        self.assertEqual(w.question.toPlainText(), 'a  b')
        self.assertFalse(w.playing)
        self.assertEqual(w.play_position, 0)
        w.search.setFocus()
        QTest.keyClicks(w.search, 'a b')
        QTest.keyClick(w.search, Qt.Key_Left)
        self.assertEqual(w.search.text(), 'a b')
        self.assertEqual(w.search.cursorPosition(), 2)
        self.assertFalse(w.playing)

    def test_dialog_combo_and_modified_keys_keep_native_behavior(self):
        w = self.window
        w.speed_box.setFocus()
        QTest.keyClick(w.speed_box, Qt.Key_Right)
        self.assertFalse(w.playing)
        self.assertEqual(w.play_position, 0)
        w.timeline.setFocus()
        QTest.keyClick(w.timeline, Qt.Key_Right, Qt.ControlModifier)
        self.assertEqual(w.play_position, 0)
        dialog = QDialog(w)
        layout = QVBoxLayout(dialog)
        edit = QLineEdit()
        layout.addWidget(edit)
        dialog.show()
        QTest.keyClicks(edit, 'space here')
        self.assertEqual(edit.text(), 'space here')
        self.assertFalse(w.playing)
        dialog.close()

    def test_recording_blocks_shortcuts_and_themes_keep_rate(self):
        w = self.window
        for mode in ('day', 'night'):
            w.set_theme(mode)
            self.assertTrue(w.speed_box.isVisible())
            self.assertTrue(w.back_button.isVisible())
            self.assertTrue(w.forward_button.isVisible())
            self.assertEqual(w.speed_box.currentData(), 1.)
        w.recorder = object()
        try:
            QTest.keyClick(w.timeline, Qt.Key_Space)
            QTest.keyClick(w.timeline, Qt.Key_Right)
            self.assertFalse(w.playing)
            self.assertEqual(w.play_position, 0)
        finally:
            w.recorder = None


if __name__ == '__main__':
    unittest.main()
