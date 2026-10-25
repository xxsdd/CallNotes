"""Synthetic-only regression checks: no microphone capture or cloud requests."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_MEDIA_BACKEND'] = 'windows'
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFontDatabase, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QTabWidget, QComboBox, QDialog, QDialogButtonBox, QSpinBox, QLineEdit, QPlainTextEdit, QListWidget, QCheckBox
from callnotes.analysis_view import AnalysisBrowser
from callnotes.markdown_display import readable_markdown
from callnotes.citations import cite_markdown, indices_from_label, parse_source_url
from callnotes.codex_client import LEGACY_REPORT, REPORT, is_report
from callnotes.text_normalization import simplified
from callnotes.storage import Store, atomic_json, finalize_pcm
from callnotes.turns import speaker_turns
from callnotes.ui import MainWindow
from callnotes.theme import configure_app
from callnotes.analysis_templates import TEMPLATES, report_prompt, question_prompt, question_label


def spin(ms):
    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)


def segment(start, end, text, track='system'):
    return dict(start=start, end=end, text=text, track=track,
                speaker='我' if track == 'mic' else '对方')


class ParagraphTests(unittest.TestCase):
    def test_simplified_chinese_retains_english_and_raw_segment(self):
        text = '這個預算是 1200 dollars，我們下週聯繫。'
        source = segment(0, 3, text)
        self.assertEqual(simplified(text), '这个预算是 1200 dollars，我们下周联系。')
        self.assertEqual(speaker_turns([source])[0]['text'], simplified(text))
        self.assertEqual(source['text'], text)

    def test_citation_ranges_lists_and_bare_ids_keep_reading_order(self):
        value = cite_markdown('先 S0009，后 [S0001–S0003]，再 [S0014、S0016]，重复 [S0009](segment:2:8)。', 2)
        self.assertEqual(value, '先 [\\[1\\]](source:2:8)，后 [\\[2\\]](source:2:0,1,2)，再 [\\[3\\]](source:2:13,15)，重复 [\\[1\\]](source:2:8)。')
        self.assertEqual(indices_from_label('S0000'), [])
        self.assertEqual(indices_from_label('S0003-S0001'), [])
        self.assertEqual(indices_from_label('S0001-S9000'), [])
        self.assertEqual(parse_source_url('source:2:0,1,0'), (2, [0, 1]))
        with self.assertRaises(ValueError):
            parse_source_url('source:2:-1')
        self.assertTrue(is_report(LEGACY_REPORT) and is_report(REPORT))

    def test_long_turn_has_no_two_second_reading_fragments(self):
        values = [segment(i, i + 2, '连续发言') for i in range(0, 90, 2)]
        turns = speaker_turns(values)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]['end'], 90)
        self.assertEqual(turns[0]['indices'], list(range(45)))

    def test_switch_and_long_pause_start_new_turns(self):
        values = [segment(0, 2, 'The budget is'), segment(2, 4, '1200.'),
                  segment(4, 5, '好的', 'mic'), segment(5, 7, '谢谢'),
                  segment(11, 12, '另外一件事')]
        turns = speaker_turns(values)
        self.assertEqual(len(turns), 4)
        self.assertEqual(turns[0]['text'], 'The budget is 1200.')

    def test_draft_recovery_preserves_manual_corrections(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            rid = store.create()
            atomic_json(store.folder(rid) / 'live-transcript.json',
                        {'segments': [segment(0, 2, '字幕草稿')], 'complete': False})
            store.recover()
            self.assertIn('结尾可能不完整', store.get(rid)['error'])
            store.save_transcript(rid, [segment(0, 2, '人工修改')])
            store.recover()
            self.assertEqual(store.transcript(rid)['segments'][0]['text'], '人工修改')
            store.db.close()


class SyntheticRecorder:
    def __init__(self, folder, *args):
        self.folder, self.errors = Path(folder), []
        self.levels = {'mic': .1, 'system': .2}
        self.started = time.perf_counter()
        self.paused = False
        self.meta = {'tracks': [dict(key=k, rate=16000, channels=1, offset=0)
                                for k in self.levels]}

    def start(self):
        for track in self.meta['tracks']:
            (self.folder / (track['key'] + '.pcm')).write_bytes(b'\0' * 32000)
        atomic_json(self.folder / 'audio.json', self.meta)

    def stop(self):
        for track in self.meta['tracks']:
            finalize_pcm(self.folder, track)
        atomic_json(self.folder / 'audio.json', self.meta)
        return 1

    def set_paused(self, paused):
        self.paused = paused


class SyntheticLive:
    is_ready = True
    def __init__(self):
        self.folder = None
        self.finished = threading.Event()

    def run(self, folder, settings, progress, caption):
        self.folder = folder
        caption({'type': 'ready'})
        for text, final in [('预算是 10000', False), ('预算是 1200', False), ('预算是 1200。', True)]:
            caption(dict(type='caption', utterance='system-0', track='system', final=final,
                         segments=[segment(0, 1, text)]))
        self.finished.wait(3)
        return {'segments': [segment(0, 1, '预算是 1200。')]}

    def finish(self, folder):
        self.finished.set()

    def stop(self):
        self.finished.set()


def synthetic_identify(folder, settings, count, progress):
    from callnotes.storage import read_json
    source = read_json(Path(folder) / 'transcript.json')['segments']
    values = [{**s, 'speaker': '说话人 1', 'speaker_id': 'system-speaker-1'} if s['track'] == 'system' else s for s in source]
    return dict(segments=values, diarization=dict(track='system', detected_speakers=1, requested_speakers=count))


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        configure_app(cls.app)
        QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\msyh.ttc')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.patches = [patch('callnotes.ui.MainWindow.load_models', lambda s: None),
                        patch('callnotes.ui.devices', return_value=([], [])),
                        patch('callnotes.ui.enumerate_voice_apps', return_value=[]),
                        patch('callnotes.ui.Recorder', SyntheticRecorder),
                        patch('callnotes.ui.LiveTranscriber', SyntheticLive),
                        patch('callnotes.ui.Transcriber.identify_speakers', side_effect=synthetic_identify)]
        for p in self.patches:
            p.start()
        self.window = MainWindow(self.store, prewarm=False)
        self.window.show()
        spin(30)

    def tearDown(self):
        self.window.close()
        QApplication.processEvents()
        for p in reversed(self.patches):
            p.stop()
        self.store.db.close()
        self.tmp.cleanup()

    def test_same_history_item_opens_again_after_new_record(self):
        rid = self.store.create('历史点击测试')
        self.store.save_transcript(rid, [segment(0, 1, '旧记录')])
        self.window.refresh_history(record_id=rid)
        self.window.show_record(rid)
        for _ in range(2):
            self.window.new_record()
            item = self.window.history.item(0)
            QTest.mouseClick(self.window.history.viewport(), Qt.LeftButton,
                             pos=self.window.history.visualItemRect(item).center())
            self.assertEqual(self.window.current_id, rid)
            self.assertEqual(self.window.pages.currentWidget(), self.window.review_page)

    def test_clean_header_and_context_actions_are_bound_to_clicked_record(self):
        a,b=self.store.create('记录甲'),self.store.create('记录乙')
        for rid,words in ((a,'甲的原文'),(b,'乙的原文')):
            self.store.save_transcript(rid,[segment(0,1,words)])
            self.store.update(rid,status='ready')
        self.window.show_record(a)
        texts=[button.text() for button in self.window.findChildren(QPushButton) if button.isVisible()]
        for hidden in ('重命名','导出','移入回收区','版本记录'):
            self.assertNotIn(hidden,texts)
        self.assertIn('Codex 分析',texts)
        self.assertGreaterEqual(self.window.ai_button.minimumWidth(),150)
        menu=self.window.record_actions_menu(b)
        actions={action.text():action for action in menu.actions() if not action.isSeparator()}
        self.assertEqual(set(actions),{'重命名','导出','版本记录','移入回收区'})
        with patch('callnotes.ui.QInputDialog.getText',return_value=('乙已改名',True)):
            actions['重命名'].trigger()
        self.assertEqual(self.store.get(a)['title'],'记录甲')
        self.assertEqual(self.store.get(b)['title'],'乙已改名')
        self.window.show_record(a)
        target=Path(self.tmp.name)/'乙.txt'
        with patch('callnotes.ui.QFileDialog.getSaveFileName',return_value=(str(target),'')):
            actions['导出'].trigger()
        self.assertIn('乙的原文',target.read_text('utf-8-sig'))
        self.assertNotIn('甲的原文',target.read_text('utf-8-sig'))
        self.window.show_record(a)
        actions['移入回收区'].trigger()
        self.assertEqual(self.store.get(b)['deleted'],1)
        self.assertEqual(self.store.get(a)['deleted'],0)
        restore=self.window.record_actions_menu(b)
        next(action for action in restore.actions() if action.text()=='恢复记录').trigger()
        self.assertEqual(self.store.get(b)['deleted'],0)
        self.window.busy_id=a
        busy=self.window.record_actions_menu(b)
        for action in busy.actions():
            if action.text() in ('重命名','移入回收区'):
                self.assertFalse(action.isEnabled())
        self.window.busy_id=None

    def test_history_right_click_opens_the_target_records_menu(self):
        a,b=self.store.create('甲'),self.store.create('乙')
        self.window.refresh_history(record_id=a)
        self.window.show_record(a)
        row=next(i for i in range(self.window.history.count()) if self.window.history.item(i).data(Qt.UserRole)==b)
        item=self.window.history.item(row)
        position=self.window.history.visualItemRect(item).center()
        menu=MagicMock()
        def inspect(*args):
            self.assertEqual(self.window.current_id,b)
        menu.popup.side_effect=inspect
        with patch.object(self.window,'record_actions_menu',return_value=menu) as make:
            self.window.history_context_menu(position)
            make.assert_called_once_with(b)
            menu.popup.assert_called_once()

    def test_fullscreen_reader_has_only_results_tracks_theme_and_keeps_citations(self):
        window=self.window
        rid=self.store.create('全屏阅读')
        self.store.save_transcript(rid,[segment(1,3,'原话来源')])
        self.store.add_message(rid,dict(question=REPORT,answer='## 分析标题\n\n正文 **重要结论** [S0001]',revision=1))
        window.show_record(rid)
        window.ai_button.click()
        with patch.object(window.codex,'analyze') as submit:
            window.expand_reading_button.click()
            reader=window.reading_dialog
            self.assertTrue(reader.isFullScreen())
            self.assertFalse(reader.isModal())
            self.assertEqual(reader.findChildren(QPlainTextEdit),[])
            self.assertEqual(reader.findChildren(QComboBox),[])
            self.assertEqual(reader.findChildren(QLineEdit),[])
            self.assertEqual([b.text() for b in reader.findChildren(QPushButton)],['退出阅读 · Esc'])
            self.assertIn('重要结论',reader.view.toPlainText())
            self.assertNotIn('**',reader.view.toPlainText())
            for mode in ('night','day'):
                window.set_theme(mode)
                self.assertEqual(reader.view.reading_mode,mode)
                self.assertGreaterEqual(reader.view.document().find('重要结论').charFormat().fontWeight(),700)
            reader.view.anchorClicked.emit(QUrl('source:1:0'))
            self.assertEqual(window.source_dialog.parent(),reader)
            self.assertEqual(window.source_dialog.quote.toPlainText(),'原话来源')
            window.source_dialog.close()
            window.report_view.setMarkdown('## 更新中的分析\n\n新结果 **重点** [S0001]')
            self.assertIn('新结果',reader.view.toPlainText())
            other=self.store.create('另一条通话')
            window.show_record(other)
            self.assertIn('新结果',reader.view.toPlainText())
            self.assertEqual(reader.property('recordId'),rid)
            QTest.keyClick(reader.view,Qt.Key_Escape)
            spin(30)
            self.assertIsNone(window.reading_dialog)
            submit.assert_not_called()

    def test_changelog_is_local_versioned_and_available_in_both_themes(self):
        from callnotes.release import VERSION,changelog_text
        window=self.window
        self.assertTrue(window.changelog_button.isVisible())
        self.assertIn('## '+VERSION,changelog_text())
        window.changelog_button.click()
        dialog=window.changelog_dialog
        view=dialog.findChild(AnalysisBrowser,'changelogText')
        self.assertTrue(dialog.isVisible())
        text=view.toPlainText()
        for expected in ('历史补录','实时字幕','局部重新识别','先出文字','回收区','全屏'):
            self.assertIn(expected,text)
        for mode in ('night','day'):
            window.set_theme(mode)
            self.assertEqual(view.reading_mode,mode)
        self.store.set_settings(reading_size=21)
        window.apply_reading()
        self.assertEqual(view.reading['size'],21)
        dialog.close()
        self.assertIsNone(window.changelog_dialog)

    def test_version_history_views_exports_restores_exact_draft_without_overwrite(self):
        rid = self.store.create('旧稿保留测试')
        self.store.save_transcript(rid, [segment(1, 3, '第一稿金额 1000')])
        self.store.save_transcript(rid, [segment(1, 3, '新稿金额 1200')])
        self.store.update(rid, status='ready')
        self.window.show_record(rid)
        menu=self.window.record_actions_menu(rid)
        next(a for a in menu.actions() if a.text()=='版本记录').trigger()
        dialog = self.window.version_dialog
        self.assertFalse(dialog.isModal())
        chooser = dialog.findChild(QListWidget, 'versionList')
        preview = dialog.findChild(QListWidget, 'timeline')
        path = dialog.findChild(QLineEdit, 'versionPath')
        self.assertEqual(chooser.count(), 2)
        chooser.setCurrentRow(1)
        self.assertEqual(preview.item(0).text(), '第一稿金额 1000')
        self.assertTrue(path.text().endswith('transcript-v1.json'))
        self.assertTrue(path.isReadOnly())
        with patch.object(self.window, 'seek') as seek:
            preview.itemClicked.emit(preview.item(0))
            seek.assert_called_once_with(1, True)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(self.store.transcript(rid)['revision'], 2)
        target = Path(self.tmp.name) / 'v1.txt'
        actions = {b.text(): b for b in dialog.findChildren(QPushButton)}
        with patch('callnotes.ui.QFileDialog.getSaveFileName', return_value=(str(target), '')):
            actions['导出此版本'].click()
        exported = target.read_text(encoding='utf-8-sig')
        self.assertIn('第一稿金额 1000', exported)
        self.assertNotIn('新稿金额 1200', exported)
        with patch('callnotes.ui.QDesktopServices.openUrl') as open_folder:
            actions['打开所在文件夹'].click()
            self.assertEqual(Path(open_folder.call_args.args[0].toLocalFile()), self.store.folder(rid))
        actions['恢复为当前文字稿'].click()
        self.assertEqual(self.store.transcript(rid)['revision'], 3)
        self.assertEqual(self.window.segments[0]['text'], '第一稿金额 1000')
        self.assertEqual(self.store.transcript_version(rid, 2)['segments'][0]['text'], '新稿金额 1200')
        self.assertEqual(chooser.count(), 3)
        dialog.close()

    def test_history_available_for_empty_latest_and_refreshes_without_switching_draft(self):
        rid = self.store.create('空稿也保留历史')
        self.store.save_transcript(rid, [segment(1, 3, '旧稿内容')])
        self.store.save_transcript(rid, [])
        self.window.show_record(rid)
        menu=self.window.record_actions_menu(rid)
        versions=next(a for a in menu.actions() if a.text()=='版本记录')
        self.assertTrue(versions.isEnabled())
        versions.trigger()
        dialog = self.window.version_dialog
        chooser = dialog.findChild(QListWidget, 'versionList')
        chooser.setCurrentRow(1)
        self.window.busy_id = rid
        restore = next(b for b in dialog.findChildren(QPushButton) if b.text() == '恢复为当前文字稿')
        self.store.save_transcript(rid, [segment(1, 3, '刚完成的新稿')])
        spin(1200)
        self.assertEqual(chooser.count(), 3)
        self.assertEqual(chooser.currentItem().data(Qt.UserRole)['revision'], 1)
        self.assertFalse(restore.isEnabled())
        self.store.set_settings(reading_size=21)
        self.window.apply_reading()
        self.assertEqual(dialog.reading, self.window.reading)
        self.window.busy_id = None
        dialog.close()

    def test_delete_multiple_reading_turns_keeps_all_unselected_sources_and_undoes(self):
        rid = self.store.create('删除选中内容')
        values = [segment(0, 2, '背景一'), segment(2, 3, '背景二'),
                  segment(4, 6, '保留本人', 'mic'), segment(7, 9, '背景三')]
        self.store.save_transcript(rid, values)
        self.store.update(rid, status='ready')
        self.window.show_record(rid)
        self.assertEqual(self.window.timeline.count(), 3)
        self.window.timeline.item(0).setSelected(True)
        self.window.timeline.item(2).setSelected(True)
        self.window.remove_segments_action.trigger()
        self.assertEqual(self.window.segments, [values[2]])
        self.assertEqual(self.store.transcript_version(rid, 1)['segments'], values)
        self.assertTrue(self.window.undo_transcript_button.isVisible())
        self.window.open_citation(QUrl('source:1:3'))
        self.assertEqual(self.window.source_dialog.quote.toPlainText(), '背景三')
        self.window.source_dialog.close()
        self.window.undo_transcript_button.click()
        self.assertEqual(self.window.segments, values)
        self.assertEqual(self.store.transcript(rid)['revision'], 3)
        self.assertFalse(self.window.undo_transcript_button.isVisible())

    def test_recycle_version_ui_can_view_and_restore_without_switching_current(self):
        rid = self.store.create('删除版本测试')
        self.store.save_transcript(rid, [segment(1, 3, '旧版原话')])
        self.store.save_transcript(rid, [segment(1, 3, '新版原话')])
        self.window.show_record(rid)
        self.window.show_versions()
        dialog = self.window.version_dialog
        chooser = dialog.findChild(QListWidget, 'versionList')
        recycle = next(b for b in dialog.findChildren(QPushButton) if b.text() == '删除此版本')
        self.assertFalse(recycle.isEnabled())  # current cannot be recycled
        chooser.setCurrentRow(1)
        recycle.click()
        self.assertEqual(chooser.count(), 1)
        toggle = dialog.findChild(QCheckBox, 'deletedVersions')
        toggle.setChecked(True)
        self.assertEqual(chooser.count(), 1)
        self.assertEqual(chooser.currentItem().data(Qt.UserRole)['revision'], 1)
        self.assertEqual(recycle.text(), '放回历史列表')
        self.assertIn('transcript-trash', dialog.findChild(QLineEdit, 'versionPath').text())
        self.window.open_citation(QUrl('source:1:0'))
        self.assertEqual(self.window.source_dialog.quote.toPlainText(), '旧版原话')
        self.window.source_dialog.close()
        recycle.click()
        self.assertEqual(chooser.count(), 0)
        toggle.setChecked(False)
        self.assertEqual(chooser.count(), 2)
        self.assertEqual(self.store.transcript(rid)['revision'], 2)
        self.assertEqual(self.window.segments[0]['text'], '新版原话')
        dialog.close()

    def test_delete_refuses_busy_and_stale_selection_and_undo_does_not_clobber_edits(self):
        rid = self.store.create('并行修改保护')
        self.store.save_transcript(rid, [segment(1, 3, '第一稿')])
        self.window.show_record(rid)
        self.window.timeline.item(0).setSelected(True)
        self.window.busy_id = rid
        self.window.delete_selected_segments()
        self.assertEqual(self.store.transcript(rid)['revision'], 1)
        self.window.busy_id = None
        self.store.save_transcript(rid, [segment(1, 3, '后台更新')])
        self.window.delete_selected_segments()
        self.assertIn('已更新', self.window.notice.text())
        self.assertEqual(self.store.transcript(rid)['revision'], 2)
        self.window.show_record(rid)
        self.window.timeline.item(0).setSelected(True)
        self.window.delete_selected_segments()
        self.assertEqual(self.window.segments, [])
        self.store.save_transcript(rid, [segment(1, 3, '后续人工修订')])
        self.window.undo_segment_deletion()
        self.assertEqual(self.store.transcript(rid)['segments'][0]['text'], '后续人工修订')

    def test_pause_resume_keeps_record_and_can_stop_while_paused(self):
        window = self.window
        window.mic_box.addItem('麦克风', 1)
        window.system_box.addItem('电脑声音', 2)
        window.start_recording()
        rid = window.recording_id
        window.toggle_record_pause()
        self.assertTrue(window.recorder.paused)
        self.assertEqual(window.pause_record_button.text(), '继续录音')
        window.toggle_record_pause()
        self.assertFalse(window.recorder.paused)
        self.assertEqual(window.recording_id, rid)
        window.toggle_record_pause()
        window.stop_recording()
        spin(150)
        self.assertIsNone(window.recorder)
        self.assertEqual(len(self.store.listing()), 1)
        self.assertTrue((self.store.folder(rid) / 'mic.wav').exists())

    def test_template_selection_changes_report_and_question_without_submitting(self):
        window = self.window
        with patch.object(window, 'submit') as submit:
            window.template_box.setCurrentIndex(window.template_box.findData('friends'))
            submit.assert_not_called()
            window.submit_report()
            sent = submit.call_args.args[0]
            self.assertTrue(is_report(sent))
            self.assertIn('不强行列需求', sent)
            window.question.setPlainText('我们聊了哪些比赛？')
            window.submit_question()
            question = submit.call_args.args[0]
            self.assertEqual(question_label(question), '我们聊了哪些比赛？')
            self.assertIn('朋友闲聊', question)
        self.assertEqual(self.store.settings()['analysis_template'], 'friends')
        self.assertEqual(len(TEMPLATES), 5)

    def test_reading_settings_save_updates_transcript_report_and_source(self):
        window = self.window
        rid = self.store.create('阅读测试')
        self.store.save_transcript(rid, [segment(0, 1, '正常正文和重点')])
        self.store.update(rid, status='ready')
        window.show_record(rid)
        window.report_view.setMarkdown('## 标题\n\n普通正文 **重点** [S0001]')
        def save(dialog):
            dialog.findChild(QSpinBox, 'readingSize').setValue(21)
            dialog.findChild(QSpinBox, 'readingSpacing').setValue(200)
            dialog.findChild(QSpinBox, 'readingWidth').setValue(600)
            QTest.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save), Qt.LeftButton)
            return QDialog.Accepted
        with patch.object(QDialog, 'exec', save):
            window.settings_dialog()
        self.assertEqual(window.reading, dict(size=21, spacing=200, width=600))
        self.assertEqual(window.report_view.reading, window.reading)
        doc = window.timeline.itemDelegate().document(window.timeline.indexFromItem(window.timeline.item(0)), 1100)
        self.assertEqual(doc.textWidth(), 600)
        self.assertIn('21px', doc.toHtml().replace(' ', ''))
        window.open_citation(QUrl('source:1:0'))
        self.assertEqual(window.source_dialog.quote.reading, window.reading)
        window.source_dialog.close()

    def test_import_creates_one_local_track_and_starts_transcription(self):
        window = self.window
        source = Path(self.tmp.name) / '演示音频.wav'
        source.write_bytes(b'unchanged original')
        def convert(path, folder, settings, progress):
            track = dict(key='imported', speaker='导入音频', rate=16000, channels=1, offset=0)
            (folder / 'imported.pcm').write_bytes(b'\0' * 32000)
            finalize_pcm(folder, track)
            atomic_json(folder / 'audio.json', dict(tracks=[track]))
            return dict(duration=1)
        imported = dict(start=0, end=1, text='这是导入的语音', speaker='导入音频', track='imported')
        with patch.object(window.transcriber, 'import_media', side_effect=convert), patch.object(window.transcriber, 'run', return_value=dict(segments=[imported])):
            window.import_media(source)
            self.assertFalse(window.import_button.isEnabled())
            spin(200)
        rid = window.current_id
        self.assertEqual(self.store.get(rid)['status'], 'ready')
        self.assertEqual(self.store.get(rid)['duration'], 1)
        self.assertEqual(window.segments, [imported])
        self.assertEqual(source.read_bytes(), b'unchanged original')
        self.assertIn('imported', window.players)
        window.transcriber.identify_speakers.assert_not_called()
        self.assertIn('文字稿已生成',window.notice.text())

    def test_local_recognition_replaces_only_selected_track_and_archives_sources(self):
        window = self.window
        rid = self.store.create('局部测试')
        original = [segment(0, 2, '原来的正确句', 'mic'), segment(3, 5, '识别错的句子'), segment(3.5, 4, '同时说话', 'mic')]
        self.store.save_transcript(rid, original)
        self.store.update(rid, status='ready')
        folder = self.store.folder(rid)
        (folder / 'system.wav').write_bytes(b'placeholder')
        window.show_record(rid)
        window.timeline.setCurrentRow(1)
        candidate = segment(3.1, 4.9, '重新识别正确的句子')
        with patch.object(window.transcriber, 'run', return_value=dict(segments=[candidate])) as recognition, patch.object(QDialog, 'exec', return_value=QDialog.Accepted):
            window.transcribe_region()
            spin(200)
        self.assertEqual(recognition.call_args.kwargs['region'], dict(start=3, end=5, track='system'))
        current = self.store.transcript(rid)
        self.assertEqual(current['revision'], 2)
        self.assertEqual(current['segments'][0], original[0])
        self.assertEqual(current['segments'][2], original[2])
        self.assertEqual(current['segments'][1]['text'], candidate['text'])
        from callnotes.storage import read_json
        self.assertEqual(read_json(folder / 'transcript-v1.json')['segments'], original)

    def test_cancel_local_recognition_keeps_manual_text_and_revision(self):
        window = self.window
        rid = self.store.create('取消替换测试')
        original = [segment(0, 2, '已经人工改过的名字')]
        self.store.save_transcript(rid, original)
        self.store.update(rid, status='ready')
        (self.store.folder(rid) / 'system.wav').write_bytes(b'placeholder')
        window.show_record(rid)
        window.timeline.setCurrentRow(0)
        with patch.object(window.transcriber, 'run', return_value=dict(segments=[segment(0, 2, '另一个识别结果')])), patch.object(QDialog, 'exec', return_value=QDialog.Rejected):
            window.transcribe_region()
            spin(200)
        self.assertEqual(self.store.transcript(rid), dict(revision=1, segments=original))
        self.assertIsNone(window.busy_id)

    def test_imported_speaker_bulk_rename_versions_and_aliases(self):
        window = self.window
        rid = self.store.create('群聊导入')
        a = dict(start=0,end=2,text='你好',track='imported',speaker='说话人 1',speaker_id='imported-speaker-1',speaker_run='test')
        b = dict(start=3,end=4,text='收到',track='imported',speaker='说话人 2',speaker_id='imported-speaker-2',speaker_run='test')
        self.store.save_transcript(rid, [a,b,{**a,'start':5,'end':6}])
        self.store.update(rid,status='ready')
        atomic_json(self.store.folder(rid) / 'diarization.json', dict(signature='test',requested_speakers=0))
        window.show_record(rid)
        self.assertTrue(window.speakers_button.isVisible())
        def save(dialog):
            dialog.findChild(QLineEdit, 'imported-speaker-1').setText('小王')
            dialog.findChild(QLineEdit, 'imported-speaker-2').setText('小李')
            return QDialog.Accepted
        with patch.object(QDialog, 'exec', save):
            window.speakers_dialog()
        current = self.store.transcript(rid)
        self.assertEqual([s['speaker'] for s in current['segments']], ['小王','小李','小王'])
        self.assertEqual(current['revision'], 2)
        from callnotes.storage import read_json
        self.assertEqual(read_json(self.store.folder(rid)/'transcript-v1.json')['segments'][0]['speaker'], '说话人 1')
        self.assertEqual(read_json(self.store.folder(rid)/'speaker-names.json')['names']['imported-speaker-1'], '小王')
        window.timeline.setCurrentRow(0)
        def reassign(dialog):
            dialog.findChild(QComboBox).setCurrentText('小李')
            return QDialog.Accepted
        with patch.object(QDialog, 'exec', reassign):
            window.edit_segment()
        self.assertEqual(window.segments[0]['speaker_id'], 'imported-speaker-2')
        self.assertTrue(window.segments[0]['speaker_manual'])
        self.assertEqual(window.segments[-1]['speaker'], '小王')

    def test_reidentify_keeps_text_and_does_not_transcribe(self):
        window = self.window
        rid = self.store.create('旧导入')
        a = dict(start=0,end=2,text='人工修正的内容',track='imported',speaker='导入音频')
        self.store.save_transcript(rid,[a])
        self.store.update(rid,status='ready')
        window.show_record(rid)
        candidate = {**a,'speaker':'说话人 1','speaker_id':'imported-speaker-1'}
        with patch.object(window.transcriber,'identify_speakers',return_value=dict(segments=[candidate],diarization=dict(detected_speakers=1,requested_speakers=2,track='imported'))) as identify, patch.object(window.transcriber,'run') as asr:
            window.reidentify_speakers(2)
            spin(200)
        self.assertEqual(identify.call_args.args[2],2)
        asr.assert_not_called()
        self.assertEqual(window.segments[0]['text'],a['text'])
        self.assertEqual(window.segments[0]['speaker'],'说话人 1')
        self.assertIn('尚未完整区分',window.notice.text())
        self.assertTrue(window.notice.property('error'))

    def test_recorded_call_exposes_speaker_management_and_bulk_rename_keeps_me(self):
        window = self.window
        rid = self.store.create('电脑录音')
        own = segment(0,1,'我的话','mic')
        remote = dict(start=2,end=3,text='朋友说的话',track='system',speaker='说话人 1',speaker_id='system-speaker-1',speaker_run='systemrun')
        self.store.save_transcript(rid,[own,remote])
        self.store.update(rid,status='ready')
        window.show_record(rid)
        self.assertTrue(window.speakers_button.isVisible())
        def save(dialog):
            self.assertIsNone(dialog.findChild(QLineEdit,'legacy:我'))
            dialog.findChild(QLineEdit,'system-speaker-1').setText('朋友甲')
            return QDialog.Accepted
        with patch.object(QDialog,'exec',save):
            window.speakers_dialog()
        self.assertEqual(window.segments[0],own)
        self.assertEqual(window.segments[1]['speaker'],'朋友甲')

    def test_reading_only_unknown_bridge_renders_underline_and_citation_stays_exact(self):
        window=self.window
        rid=self.store.create('连续发言')
        values=[dict(start=0,end=1,text='开始',track='system',speaker='小王',speaker_id='a'),
                dict(start=1,end=2,text='不清楚',track='system',speaker='待确认',speaker_id='unknown',speaker_uncertain=True),
                dict(start=2,end=3,text='结束',track='system',speaker='小王',speaker_id='a')]
        self.store.save_transcript(rid,values)
        self.store.update(rid,status='ready')
        window.show_record(rid)
        self.assertEqual(window.timeline.count(),1)
        turn=window.timeline.item(0).data(Qt.UserRole+2)
        self.assertEqual(turn['uncertain_ranges'],[(2,5)])
        window.locate_source(1,1,False)
        focus=window.timeline.item(0).data(Qt.UserRole+3)
        self.assertEqual(focus['range'],(2,5))
        self.assertEqual(self.store.transcript(rid)['segments'],values)
        with patch('callnotes.ui.QInputDialog.getItem',return_value=('',False)),patch.object(QDialog,'exec') as edit:
            window.edit_segment()
        edit.assert_not_called()

    def test_local_recognition_of_mixed_reading_paragraph_keeps_unknown_attribution(self):
        window=self.window
        rid=self.store.create('混合归属局部识别')
        values=[dict(start=0,end=1,text='开始',track='system',speaker='小王',speaker_id='a'),
                dict(start=1,end=2,text='不清楚',track='system',speaker='待确认',speaker_id='unknown',speaker_uncertain=True),
                dict(start=2,end=3,text='结束',track='system',speaker='小王',speaker_id='a')]
        self.store.save_transcript(rid,values)
        self.store.update(rid,status='ready')
        folder=self.store.folder(rid)
        (folder/'system.wav').write_bytes(b'placeholder')
        atomic_json(folder/'diarization.json',dict(track='system',signature='mix',turns=[
            dict(start=0,end=3,speaker_id='a'),dict(start=1,end=2,speaker_id='b')]))
        window.show_record(rid)
        window.timeline.setCurrentRow(0)
        candidates=[dict(start=s['start'],end=s['end'],text=s['text'],track='system',speaker='对方') for s in values]
        with patch.object(window.transcriber,'run',return_value=dict(segments=candidates)),patch.object(QDialog,'exec',return_value=QDialog.Accepted):
            window.transcribe_region()
            spin(200)
        updated=self.store.transcript(rid)['segments']
        self.assertEqual(updated[1]['speaker_id'],'unknown')
        self.assertEqual(updated[0]['speaker'],'小王')
        self.assertEqual(''.join(s['text'] for s in updated),'开始不清楚结束')

    def test_ai_label_emphasis_displays_bold_not_stars_and_keeps_sources(self):
        view = self.window.report_view
        answer = '## 话题\n\n**各方表达： **有人认为。 [S0001]\n\n**需求或顾虑：**没有明确需求。\n\n普通 **加粗** 和 **双方意见。**内容'
        view.setMarkdown(cite_markdown(answer, 1))
        self.assertNotIn('**', view.toPlainText())
        self.assertIn('各方表达：有人认为。', view.toPlainText())
        for label in ('各方表达：', '需求或顾虑：', '加粗', '双方意见。'):
            cursor = view.document().find(label)
            self.assertGreaterEqual(cursor.charFormat().fontWeight(), 700)
        cursor = view.document().find('[1]')
        self.assertEqual(cursor.charFormat().anchorHref(), 'source:1:0')
        self.assertIn('**各方表达： **', answer)
        # The expanded reader copies rich text and must keep the same result.
        copy = AnalysisBrowser()
        copy.setHtml(view.toHtml())
        self.assertEqual(copy.toPlainText(), view.toPlainText())
        copy.deleteLater()

    def test_global_theme_and_clean_emphasis(self):
        from callnotes import theme
        rid = self.store.create()
        self.store.save_transcript(rid, [segment(0, 2, '原话')])
        self.store.add_message(rid, dict(question=REPORT, answer='## 标题\n\n普通文字 **重点** [S0001]', revision=1))
        self.store.set_settings(reading_text_day='#00ffff', reading_text_night='#00ffff')
        self.window.show_record(rid)
        self.window.open_citation(QUrl('source:1:0'))
        for mode, bg in [('day', '#ffffff'), ('night', '#222a36'), ('day', '#ffffff')]:
            self.window.theme_box.setCurrentIndex(self.window.theme_box.findData(mode))
            self.window.set_theme(mode)
            self.assertEqual(theme.BACKGROUND, bg)
            self.assertEqual(self.store.settings()['theme_mode'], mode)
            self.assertEqual(self.window.report_view.reading_mode, mode)
            self.assertEqual(self.window.source_dialog.quote.reading_mode, mode)
            for label in ('普通文字', '重点'):
                cursor = self.window.report_view.document().find(label)
                self.assertEqual(cursor.charFormat().background().color().alpha(), 0)
                self.assertNotEqual(cursor.charFormat().foreground().color().name(), '#00ffff')
            doc = self.window.timeline.itemDelegate().document(self.window.timeline.model().index(0, 0), 600)
            self.assertIn(theme.TEXT, doc.toHtml())
        texts = [b.text() for b in self.window.findChildren(QPushButton)]
        self.assertNotIn('文字颜色', texts)
        self.assertIn('展开阅读', texts)

    def test_unified_report_shows_latest_report_once_and_preserves_questions(self):
        rid = self.store.create()
        self.store.save_transcript(rid, [segment(0, 2, '原话')])
        for question, answer in [(REPORT, '旧报告'), ('预算是多少', '历史回答'), (REPORT, '新版报告')]:
            self.store.add_message(rid, dict(question=question, answer=answer, revision=1))
        self.window.show_record(rid)
        text = self.window.report_view.toPlainText()
        self.assertEqual(text.count('新版报告'), 1)
        self.assertNotIn('旧报告', text)
        self.assertIn('预算是多少', text)
        self.assertIn('历史回答', text)
        self.assertFalse(self.window.ai_panel.findChildren(QTabWidget))
        self.assertEqual(len(self.store.messages(rid)), 3)

    def test_report_and_question_submit_once_without_confirmation(self):
        rid = self.store.create()
        self.store.save_transcript(rid, [segment(0, 2, '合成文本')])
        self.window.show_record(rid)
        self.window.models_loaded([dict(model='test-model', displayName='测试模型',
            defaultReasoningEffort='high', supportedReasoningEfforts=[dict(reasoningEffort='high')])])
        self.window.ai_button.setChecked(True)
        release = threading.Event()
        def analyze(store, record_id, question, model, effort, progress, delta):
            release.wait(2)
            delta('测试回答')
            store.add_message(record_id, dict(question=question, answer='测试回答', revision=1))
            return {}
        with patch('callnotes.ui.QDialog.exec', side_effect=AssertionError('Unexpected confirmation')), patch.object(self.window.codex, 'analyze', side_effect=analyze) as service:
            for number, control in enumerate([self.window.report_button, self.window.ask_button], 1):
                release.clear()
                self.window.question.setPlainText('客户顾虑是什么')
                QTest.mouseClick(control, Qt.LeftButton)
                spin(30)
                self.assertEqual(self.window.busy_type, 'analysis')
                self.assertFalse(control.isEnabled())
                QTest.mouseClick(control, Qt.LeftButton)
                self.assertEqual(service.call_count, number)
                self.assertEqual(service.call_args.args[3:5], ('test-model', 'high'))
                release.set()
                spin(200)
                self.assertIsNone(self.window.busy_id)
            self.assertEqual(service.call_args.args[2], '客户顾虑是什么')
        self.assertEqual(self.window.report_view.toPlainText().count('测试回答'), 2)

    def test_emphasis_repair_preserves_code_literal_stars_and_streaming_tail(self):
        literal = r'\*\*原样： \*\*' + '\n`**代码： **`\n```python\n**代码： **\n```\n    **代码： **\n2**3 + 4**2\n**还没写完'
        self.assertEqual(readable_markdown(literal), literal)
        view = self.window.report_view
        view.setMarkdown('**各方表达： ')
        view.setMarkdown('**各方表达： **后来补齐。')
        self.assertEqual(view.toPlainText(), '各方表达：后来补齐。')

    def test_citation_seeks_exact_member_inside_grouped_turn(self):
        rid = self.store.create()
        self.store.save_transcript(rid, [segment(0, 2, '第一句'), segment(2, 5, '第二句')])
        self.window.show_record(rid)
        self.assertEqual(self.window.timeline.count(), 1)
        positions = []
        with patch.object(self.window, 'seek', side_effect=positions.append):
            self.window.open_citation(QUrl('segment:1:1'))
        self.assertEqual(positions, [2])

    def test_rendered_badge_click_opens_source_without_playing(self):
        rid = self.store.create()
        self.store.save_transcript(rid, [segment(0, 2, '第一句'), segment(2, 5, '第二句')])
        self.store.add_message(rid, dict(question=LEGACY_REPORT, answer='## 预算\n\n已经确认。[S0001–S0002]', revision=1))
        self.window.show_record(rid)
        self.window.ai_button.setChecked(True)
        spin(30)
        view = self.window.report_view
        self.assertIn('[1]', view.toPlainText())
        self.assertNotIn('S0001', view.toPlainText())
        cursor = view.document().find('[1]')
        cursor.setPosition(cursor.selectionStart() + 1)
        point = view.cursorRect(cursor).center()
        self.assertEqual(view.anchorAt(point), 'source:1:0,1')
        with patch.object(self.window, 'seek') as seek:
            QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=point)
            spin(20)
            dialog = self.window.source_dialog
            self.assertIsNotNone(dialog)
            self.assertEqual(dialog.quote.toPlainText(), '第一句')
            self.assertEqual(dialog.choose.count(), 2)
            seek.assert_not_called()
            dialog.choose.setCurrentRow(1)
            jump = next(b for b in dialog.findChildren(QPushButton) if b.text() == '定位并回听')
            QTest.mouseClick(jump, Qt.LeftButton)
            seek.assert_called_once_with(2)
            self.assertTrue(dialog.isVisible())
            focus = self.window.timeline.item(0).data(Qt.UserRole + 3)
            self.assertEqual(focus['range'], (3, 6))

    def test_adjacent_badges_open_visible_group_and_select_clicked_source(self):
        from callnotes.citations import parse_source_target
        rid = self.store.create()
        self.store.save_transcript(rid, [segment(1, 2, '第一处'), segment(3, 4, '第二处'), segment(5, 6, '第三处')])
        self.store.add_message(rid, dict(question=REPORT, answer='同一个结论。[S0001][S0002][S0003]\n\n另一个结论。[S0001]', revision=1))
        self.window.show_record(rid)
        self.window.ai_button.setChecked(True)
        spin(30)
        view = self.window.report_view
        cursor = view.document().find('[2]')
        cursor.setPosition(cursor.selectionStart() + 1)
        point = view.cursorRect(cursor).center()
        self.assertEqual(parse_source_target(view.anchorAt(point)), (1, [0, 1, 2], 1))
        with patch.object(self.window, 'seek') as seek:
            QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=point)
            spin(20)
            dialog = self.window.source_dialog
            self.assertEqual(dialog.choose.count(), 3)
            self.assertEqual(dialog.choose.currentRow(), 1)
            self.assertEqual(dialog.quote.toPlainText(), '第二处')
            self.assertFalse(dialog.findChildren(QComboBox))
            item = dialog.choose.item(2)
            self.assertTrue(dialog.choose.visualItemRect(item).intersects(dialog.choose.viewport().rect()))
            QTest.mouseClick(dialog.choose.viewport(), Qt.LeftButton, pos=dialog.choose.visualItemRect(item).center())
            self.assertEqual(dialog.quote.toPlainText(), '第三处')
            seek.assert_not_called()
            action = next(b for b in dialog.findChildren(QPushButton) if b.text() == '定位并回听')
            QTest.mouseClick(action, Qt.LeftButton)
            seek.assert_called_once_with(5)
            self.assertTrue(dialog.isVisible())

    def test_capture_scope_routes_only_selected_app_and_remembers_choice(self):
        target = dict(kind='process', pid=123, created=456, exe='discord.exe', name='Discord')
        self.window.mic_box.addItem('合成麦克风', 1)
        self.window.system_box.addItem('合成电脑声音', 2)
        with patch('callnotes.ui.enumerate_voice_apps', return_value=[target]), patch('callnotes.ui.support_error', return_value=''):
            self.window.refresh_voice_apps()
            self.window.capture_mode.setCurrentIndex(self.window.capture_mode.findData('process'))
            self.assertTrue(self.window.system_box.isHidden())
            self.assertFalse(self.window.app_box.isHidden())
            self.assertIn('排除浏览器', self.window.capture_hint.text())
            with patch('callnotes.ui.Recorder', wraps=SyntheticRecorder) as recorder:
                self.window.start_recording()
                self.assertEqual(recorder.call_args.args[1:], (1, target))
                self.assertFalse(self.window.capture_mode.isEnabled())
                spin(60)
                self.window.stop_recording()
                spin(150)
            self.assertEqual(self.store.settings()['capture_mode'], 'process')
            self.assertEqual(self.store.settings()['voice_app'], 'discord.exe')
            self.window.capture_mode.setCurrentIndex(self.window.capture_mode.findData('all'))
            self.assertFalse(self.window.system_box.isHidden())
            self.assertTrue(self.window.app_box.isHidden())

    def test_recording_settings_live_only_in_dialog_and_cancel_restores(self):
        window = self.window
        window.new_record()
        window.mic_box.addItem('原麦克风', 1)
        window.mic_box.addItem('备用麦克风', 2)
        before = self.store.settings()
        self.assertFalse(window.record_page.findChildren(QComboBox))
        self.assertNotIn('重命名',[b.text() for b in window.findChildren(QPushButton) if b.isVisible()])
        self.assertTrue(window.ai_button.isHidden())
        self.assertTrue(window.notice.isHidden())
        for _ in range(2):
            def cancel(dialog):
                tabs = dialog.findChild(QTabWidget, 'settingsTabs')
                self.assertEqual([tabs.tabText(i) for i in range(tabs.count())], ['录音与声音', '文字识别', 'AI 分析', '本地存储', '阅读排版'])
                self.assertIs(tabs.widget(0), window.audio_settings)
                window.mic_box.setCurrentIndex(1)
                window.capture_mode.setCurrentIndex(window.capture_mode.findData('process'))
                self.assertEqual(self.store.settings(), before)
                return QDialog.Rejected
            with patch.object(QDialog, 'exec', cancel):
                window.settings_dialog()
            self.assertEqual(window.capture_mode.currentData(), 'all')
            self.assertEqual(window.mic_box.currentText(), '原麦克风')
            self.assertEqual(self.store.settings(), before)
            self.assertIs(window.audio_settings.parent(), window)
            self.assertTrue(window.audio_settings.isHidden())

    def test_settings_save_applies_source_and_persists_without_touching_storage_root(self):
        window = self.window
        target = dict(kind='process', pid=123, created=456, exe='discord.exe', name='Discord')
        window.mic_box.addItem('耳机麦克风', 1)
        window.system_box.addItem('耳机播放', 2)
        window.app_box.clear()
        window.app_box.addItem('Discord', target)
        def save(dialog):
            window.capture_mode.setCurrentIndex(window.capture_mode.findData('process'))
            controls = dialog.findChild(QDialogButtonBox)
            QTest.mouseClick(controls.button(QDialogButtonBox.Save), Qt.LeftButton)
            return QDialog.Accepted
        with patch.object(QDialog, 'exec', save), patch('callnotes.ui.atomic_json') as bootstrap:
            window.settings_dialog()
            bootstrap.assert_not_called()
        config = self.store.settings()
        self.assertEqual(config['capture_mode'], 'process')
        self.assertEqual(config['mic_device'], '耳机麦克风')
        self.assertEqual(config['voice_app'], 'discord.exe')
        self.assertIn('Discord', window.device_summary.text())
        self.assertFalse(window.record_page.findChildren(QComboBox))
        with patch('callnotes.ui.Recorder', wraps=SyntheticRecorder) as recorder, patch('callnotes.ui.support_error', return_value=''):
            window.start_recording()
            self.assertEqual(recorder.call_args.args[1:], (1, target))
            self.assertFalse(window.audio_settings_button.isEnabled())
            spin(50)
            self.assertIs(window.live_canvas.currentWidget(), window.live_timeline)
            window.stop_recording()
            spin(150)

    def test_missing_or_unsupported_app_cannot_fall_back_to_whole_computer(self):
        self.window.mic_box.addItem('合成麦克风', 1)
        self.window.system_box.addItem('全部电脑声音', 2)
        self.window.capture_mode.setCurrentIndex(self.window.capture_mode.findData('process'))
        with patch('callnotes.ui.Recorder') as recorder, patch('callnotes.ui.support_error', return_value=''):
            self.window.start_recording()
            recorder.assert_not_called()
            self.assertIsNone(self.window.recorder)
            self.assertIn('Discord 或微信', self.window.notice.text())
        self.window.app_box.clear()
        self.window.app_box.addItem('微信', dict(kind='process', pid=12, created=34, exe='weixin.exe', name='微信'))
        with patch('callnotes.ui.Recorder') as recorder, patch('callnotes.ui.support_error', return_value='此系统不支持按软件录音'):
            self.window.start_recording()
            recorder.assert_not_called()
            self.assertIn('不支持', self.window.notice.text())

    def test_source_stays_open_and_bound_to_original_record(self):
        rid = self.store.create('原通话')
        self.store.save_transcript(rid, [segment(3, 5, '舊版原話')])
        self.store.save_transcript(rid, [segment(3, 6, '新版修改')])
        self.window.show_record(rid)
        self.window.open_citation(QUrl('source:1:0'))
        dialog = self.window.source_dialog
        self.assertEqual(dialog.quote.toPlainText(), '旧版原话')
        other = self.store.create('其他通话')
        self.store.save_transcript(other, [segment(20, 22, '另一条')])
        self.window.show_record(other)
        with patch.object(self.window, 'seek') as seek:
            play = next(b for b in dialog.findChildren(QPushButton) if b.text() == '定位并回听')
            QTest.mouseClick(play, Qt.LeftButton)
            seek.assert_called_once_with(3)
        self.assertTrue(dialog.isVisible())
        self.assertIs(self.window.source_dialog, dialog)
        self.assertEqual(self.window.current_id, rid)
        self.assertEqual(self.store.transcript(rid)['segments'][0]['text'], '新版修改')
        self.assertIsNotNone(self.window.timeline.item(0).data(Qt.UserRole + 3))
        dialog.close()
        spin(20)
        self.assertIsNone(self.window.source_dialog)

    def test_combined_source_action_starts_players_without_closing_source(self):
        rid = self.store.create()
        self.store.update(rid, duration=60)
        self.store.save_transcript(rid, [segment(12, 18, '回听原话')])
        self.window.show_record(rid)
        self.window.open_citation(QUrl('source:1:0'))
        dialog = self.window.source_dialog
        self.window.players = {'mic': MagicMock(), 'system': MagicMock()}
        self.window.offsets = {'mic': 0, 'system': .5}
        action = next(b for b in dialog.findChildren(QPushButton) if b.text() == '定位并回听')
        QTest.mouseClick(action, Qt.LeftButton)
        self.assertTrue(self.window.playing)
        self.assertTrue(dialog.isVisible())
        self.window.players['mic'].setPosition.assert_called_with(12000)
        self.window.players['system'].setPosition.assert_called_with(11500)
        for player in self.window.players.values():
            player.play.assert_called_once()
        self.assertIsNotNone(self.window.timeline.item(0).data(Qt.UserRole + 3))

    def test_long_turn_highlight_targets_exact_repeated_sentence(self):
        rid = self.store.create()
        values = [segment(i * 2, i * 2 + 2, '这是一句重复的内容。' * 10) for i in range(45)]
        self.store.save_transcript(rid, values)
        self.window.show_record(rid)
        with patch.object(self.window, 'seek') as seek:
            self.window.locate_source(1, 40, True)
            seek.assert_called_once_with(80)
        item = self.window.timeline.item(0)
        focus = item.data(Qt.UserRole + 3)
        self.assertEqual(focus['range'], (len(values[0]['text']) * 40, len(values[0]['text']) * 41))
        self.assertGreater(self.window.timeline.verticalScrollBar().value(), 0)
        doc = self.window.timeline.itemDelegate().document(self.window.timeline.model().index(0, 0), self.window.timeline.viewport().width())
        self.assertIn('background-color:', doc.toHtml())
        doc.size()
        body = doc.begin().next()
        block_top = doc.documentLayout().blockBoundingRect(body).top()
        line = body.layout().lineForTextPosition(focus['range'][0])
        self.assertTrue(line.isValid())
        visible_y = self.window.timeline.visualItemRect(item).top() + block_top + line.y() + 14
        self.assertGreaterEqual(visible_y, 0)
        self.assertLess(visible_y, self.window.timeline.viewport().height())
        self.window.set_theme('night')
        self.assertEqual(self.window.timeline.item(0).data(Qt.UserRole + 3), focus)

    def test_missing_archived_citation_never_falls_back_to_current(self):
        rid = self.store.create()
        self.store.save_transcript(rid, [segment(0, 1, '当前版本')])
        self.window.show_record(rid)
        with patch.object(self.window, 'seek') as seek:
            self.window.open_citation(QUrl('source:999:0'))
            self.assertIsNone(self.window.source_dialog)
            seek.assert_not_called()
        self.assertIn('未找到', self.window.notice.text())

    def test_live_revisions_replace_and_stop_only_finishes_tail(self):
        self.window.mic_box.addItem('合成麦克风', 1)
        self.window.system_box.addItem('合成电脑声音', 2)
        self.window.start_recording()
        rid = self.window.current_id
        spin(100)
        self.assertEqual(self.window.live_timeline.count(), 1)
        self.assertEqual(self.window.live_timeline.item(0).text(), '预算是 1200。')
        with patch.object(self.window.transcriber, 'run', side_effect=AssertionError('full replay')):
            QTest.mouseClick(self.window.stop_button, Qt.LeftButton)
            spin(200)
        self.assertIsNone(self.window.recorder)
        self.assertIsNone(self.window.busy_id)
        self.assertEqual(self.store.get(rid)['status'], 'ready')
        self.assertEqual(len(self.store.transcript(rid)['segments']), 1)
        self.assertTrue((self.store.folder(rid) / 'mic.wav').exists())
        self.assertEqual(self.store.settings().get('import_speakers',0),0)
        self.assertNotIn('speaker_id',self.store.transcript(rid)['segments'][0])
        self.assertFalse((self.store.folder(rid)/'speaker-settings.json').exists())
        self.assertEqual(self.store.transcript(rid)['revision'],1)
        self.window.transcriber.identify_speakers.assert_not_called()

    def test_close_while_recording_saves_both_tracks(self):
        self.window.mic_box.addItem('合成麦克风', 1)
        self.window.system_box.addItem('合成电脑声音', 2)
        self.window.start_recording()
        rid = self.window.current_id
        self.window.close()
        self.assertIsNone(self.window.recorder)
        self.assertTrue((self.store.folder(rid) / 'mic.wav').exists())
        self.assertTrue((self.store.folder(rid) / 'system.wav').exists())
        self.window.transcriber.identify_speakers.assert_not_called()

    def test_recording_does_not_depend_on_speaker_models(self):
        window=self.window
        window.mic_box.addItem('合成麦克风',1)
        window.system_box.addItem('合成电脑声音',2)
        with patch.object(window.transcriber,'identify_speakers',side_effect=RuntimeError('不应加载说话人模型')) as identify:
            window.start_recording()
            spin(70)
            rid=window.current_id
            window.stop_recording()
            spin(200)
            identify.assert_not_called()
        self.assertEqual(self.store.transcript(rid)['segments'][0]['text'],'预算是 1200。')
        self.assertEqual(self.store.transcript(rid)['revision'],1)
        self.assertEqual(self.store.get(rid)['status'],'ready')
        self.assertIsNone(window.busy_id)

    def test_only_explicit_start_runs_speaker_identification_with_selected_count(self):
        window=self.window
        rid=self.store.create('先文字稿后手动区分')
        values=[dict(start=i,end=i+1,text=f'原话{i}',track='imported',speaker='导入音频') for i in range(3)]
        self.store.save_transcript(rid,values)
        self.store.update(rid,status='ready')
        window.show_record(rid)
        self.assertEqual(window.speakers_button.text(),'区分说话人')
        def choose(dialog):
            count=dialog.findChild(QSpinBox,'speakerCount')
            self.assertEqual(count.minimum(),1)
            self.assertEqual(count.specialValueText(),'')
            count.setValue(3)
            return QDialog.Rejected
        with patch.object(window.transcriber,'identify_speakers') as identify, patch.object(QDialog,'exec',choose):
            window.speakers_dialog()
            identify.assert_not_called()
            window.reidentify_speakers(0)
            identify.assert_not_called()
        candidates=[{**s,'speaker':f'说话人 {i+1}','speaker_id':f'imported-speaker-{i+1}'} for i,s in enumerate(values)]
        result=dict(segments=candidates,diarization=dict(track='imported',requested_speakers=3,detected_speakers=3))
        def start(dialog):
            choose(dialog)
            next(b for b in dialog.findChildren(QPushButton) if b.text()=='开始区分说话人').click()
            return QDialog.Rejected
        with patch.object(window.transcriber,'identify_speakers',return_value=result) as identify, patch.object(QDialog,'exec',start):
            window.speakers_button.click()
            spin(200)
            identify.assert_called_once()
            self.assertEqual(identify.call_args.args[2],3)
        self.assertEqual(self.store.transcript(rid)['revision'],2)
        self.assertEqual(self.store.transcript_version(rid,1)['segments'],values)
        self.assertEqual([s['text'] for s in window.segments],[s['text'] for s in values])

    def test_collapsible_codex_keeps_model_effort_choices_without_submitting(self):
        rid = self.store.create('面板交互测试')
        self.store.save_transcript(rid, [segment(0, 2, '确认一下价格')])
        self.window.show_record(rid)
        self.assertTrue(self.window.ai_panel.isHidden())
        models = [dict(model=name, displayName=name, defaultReasoningEffort=efforts[0],
                       supportedReasoningEfforts=[dict(reasoningEffort=e) for e in efforts])
                  for name, efforts in [('test-model-a', ['low', 'high']),
                                        ('test-model-b', ['medium', 'max'])]]
        self.window.models_loaded(models)
        self.window.model_box.setCurrentIndex(1)
        self.window.effort_box.setCurrentIndex(1)
        with patch.object(self.window.codex, 'analyze') as analyze:
            for _ in range(2):
                QTest.mouseClick(self.window.ai_button, Qt.LeftButton)
                spin(20)
                self.assertTrue(self.window.ai_panel.isVisible())
                self.assertEqual(self.window.effort_box.currentData(), 'max')
                QTest.mouseClick(self.window.ai_button, Qt.LeftButton)
                self.assertTrue(self.window.ai_panel.isHidden())
            analyze.assert_not_called()
        self.assertEqual(self.store.settings()['model'], 'test-model-b')
        self.assertEqual(self.store.settings()['effort'], 'max')

    def test_new_caption_preserves_scrolled_history_and_rows(self):
        self.window.live_segments = [segment(i * 5, i * 5 + 2, '完整发言。' * 30,
            'mic' if i % 2 else 'system') for i in range(40)]
        self.window.render_live()
        QApplication.processEvents()
        scroll = self.window.live_timeline.verticalScrollBar()
        scroll.setValue(120)
        first = self.window.live_timeline.item(0)
        self.window.live_partials = {'system-99': [dict(**segment(205, 207, '新字幕'), partial=True)]}
        self.window.render_live()
        QApplication.processEvents()
        self.assertIs(first, self.window.live_timeline.item(0))
        self.assertEqual(scroll.value(), 120)


if __name__ == '__main__':
    unittest.main()
