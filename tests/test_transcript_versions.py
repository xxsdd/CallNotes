import tempfile
import unittest
from callnotes.storage import Store, atomic_json


def words(text):
    return [dict(start=1, end=3, text=text, track='system', speaker='说话人 1')]


class TranscriptVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.rid = self.store.create('版本测试')

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_all_revisions_are_visible_and_exact_without_fallback(self):
        self.assertEqual(self.store.transcript_versions(self.rid), [])
        for text in ('第一稿', '人工修正', '重新识别'):
            self.store.save_transcript(self.rid, words(text))
        entries = self.store.transcript_versions(self.rid)
        self.assertEqual([x['revision'] for x in entries], [3, 2, 1])
        self.assertEqual([x['current'] for x in entries], [True, False, False])
        self.assertTrue(entries[0]['path'].endswith('transcript.json'))
        self.assertTrue(entries[-1]['path'].endswith('transcript-v1.json'))
        self.assertEqual(self.store.transcript_version(self.rid, 2)['segments'], words('人工修正'))
        (self.store.folder(self.rid) / 'transcript-v2.json').unlink()
        with self.assertRaises(FileNotFoundError):
            self.store.transcript_version(self.rid, 2)
        for invalid in (0, -1, '1', True):
            with self.assertRaises(ValueError):
                self.store.transcript_version(self.rid, invalid)

    def test_restore_creates_new_revision_and_preserves_current_and_citations(self):
        self.store.save_transcript(self.rid, words('旧内容'))
        self.store.save_transcript(self.rid, words('新内容'))
        archive = self.store.folder(self.rid) / 'transcript-v1.json'
        before = archive.read_bytes()
        message = dict(question='原问题', answer='旧稿的引用', revision=2)
        self.store.add_message(self.rid, message)
        restored = self.store.restore_transcript(self.rid, 1)
        self.assertEqual(restored['revision'], 3)
        self.assertEqual(restored['segments'], words('旧内容'))
        self.assertEqual(self.store.transcript_version(self.rid, 2)['segments'], words('新内容'))
        self.assertEqual(archive.read_bytes(), before)
        self.assertEqual(self.store.messages(self.rid), [message])

    def test_empty_and_corrupt_archives_are_not_silently_hidden(self):
        self.store.save_transcript(self.rid, [])
        self.store.save_transcript(self.rid, words('后来的内容'))
        self.assertEqual(self.store.transcript_version(self.rid, 1)['segments'], [])
        archive = self.store.folder(self.rid) / 'transcript-v1.json'
        archive.write_text('{broken', encoding='utf-8')
        entries = self.store.transcript_versions(self.rid)
        self.assertEqual([x['revision'] for x in entries], [2, 1])
        self.assertTrue(entries[1]['error'])
        self.assertEqual(entries[1]['path'], str(archive))
        with self.assertRaises(ValueError):
            self.store.restore_transcript(self.rid, 1)
        self.assertEqual(self.store.transcript(self.rid)['revision'], 2)

    def test_version_recycle_is_reversible_and_keeps_exact_old_citations(self):
        self.store.save_transcript(self.rid, words('第一稿'))
        self.store.save_transcript(self.rid, words('当前稿'))
        original = (self.store.folder(self.rid) / 'transcript-v1.json').read_bytes()
        self.store.set_transcript_deleted(self.rid, 1)
        self.assertEqual([x['revision'] for x in self.store.transcript_versions(self.rid)], [2])
        recycled = self.store.transcript_versions(self.rid, deleted=True)
        self.assertEqual(recycled[0]['revision'], 1)
        self.assertTrue(recycled[0]['deleted'])
        self.assertEqual(self.store.transcript_version(self.rid, 1)['segments'], words('第一稿'))
        self.assertEqual(self.store.transcript(self.rid)['segments'], words('当前稿'))
        self.store.set_transcript_deleted(self.rid, 1, False)
        self.assertEqual(self.store.transcript_versions(self.rid, deleted=True), [])
        self.assertEqual((self.store.folder(self.rid) / 'transcript-v1.json').read_bytes(), original)
        with self.assertRaises(ValueError):
            self.store.set_transcript_deleted(self.rid, 2)
        with self.assertRaises(FileNotFoundError):
            self.store.set_transcript_deleted(self.rid, 99)

    def test_segment_delete_retains_others_audio_and_history_rejects_stale_revision(self):
        segments = [words(text)[0] for text in ('开头', '背景误识别', '保留的发言')]
        self.store.save_transcript(self.rid, segments)
        audio = self.store.folder(self.rid) / 'system.wav'
        audio.write_bytes(b'original audio unchanged')
        self.store.add_message(self.rid, dict(revision=1, answer='原分析'))
        result = self.store.delete_transcript_segments(self.rid, [1, 1], 1)
        self.assertEqual(result['revision'], 2)
        self.assertEqual(result['segments'], [segments[0], segments[2]])
        self.assertEqual(self.store.transcript_version(self.rid, 1)['segments'], segments)
        self.assertEqual(audio.read_bytes(), b'original audio unchanged')
        self.assertEqual(len(self.store.messages(self.rid)), 1)
        for indices, revision in (([0], 1), ([], 2), ([-1], 2), ([2], 2), ([True], 2)):
            with self.assertRaises(ValueError):
                self.store.delete_transcript_segments(self.rid, indices, revision)
        self.assertEqual(self.store.transcript(self.rid), result)

    def test_delete_every_segment_is_not_resurrected_from_live_checkpoint_on_restart(self):
        self.store.save_transcript(self.rid, words('背景文字'))
        atomic_json(self.store.folder(self.rid) / 'live-transcript.json', dict(segments=words('背景文字'), complete=True))
        self.store.delete_transcript_segments(self.rid, [0], 1)
        self.store.recover()
        self.assertEqual(self.store.transcript(self.rid), dict(revision=2, segments=[]))
        self.assertEqual(self.store.transcript_version(self.rid, 1)['segments'], words('背景文字'))
