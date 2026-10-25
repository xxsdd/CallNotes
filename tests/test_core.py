import json
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from callnotes.storage import Store, atomic_json, finalize_pcm, read_json, transcript_text
from callnotes.codex_client import split_transcript


class RecoveryAndVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.record_id = self.store.create('合成测试')

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_abrupt_recording_recovers_complete_pcm_frames(self):
        folder = self.store.folder(self.record_id)
        track = {'key': 'mic', 'rate': 16000, 'channels': 1, 'offset': .12}
        (folder / 'mic.pcm').write_bytes(struct.pack('<h', 200) * 16000 + b'\x01')
        atomic_json(folder / 'audio.json', {'tracks': [track]})
        self.store.recover()
        with wave.open(str(folder / 'mic.wav'), 'rb') as wav:
            self.assertEqual(wav.getnframes(), 16000)
            self.assertEqual(wav.readframes(1), struct.pack('<h', 200))
        row = self.store.get(self.record_id)
        self.assertEqual(row['status'], 'saved')
        self.assertAlmostEqual(row['duration'], 1.12)
        self.assertTrue((folder / 'mic.pcm').exists())

    def test_retranscription_preserves_user_edits_for_old_citations(self):
        original = [{'start': 0, 'end': 1, 'speaker': '我', 'track': 'mic', 'text': '原始文字'}]
        v1 = self.store.save_transcript(self.record_id, original, original=True)
        edited = [{**original[0], 'text': '修订金额 1200'}]
        v2 = self.store.save_transcript(self.record_id, edited)
        self.store.save_transcript(self.record_id, original, original=True)
        saved = read_json(self.store.folder(self.record_id) / 'transcript-v2.json')
        self.assertEqual(saved['segments'][0]['text'], '修订金额 1200')
        self.assertEqual(self.store.transcript(self.record_id)['revision'], 3)

    def test_recycle_does_not_destroy_record_or_questions(self):
        self.store.add_message(self.record_id, {'answer': '保留这条问答'})
        self.store.update(self.record_id, deleted=1)
        self.assertEqual(len(self.store.listing()), 0)
        self.assertEqual(len(self.store.listing(deleted=True)), 1)
        self.store.update(self.record_id, deleted=0)
        self.assertEqual(self.store.messages(self.record_id)[0]['answer'], '保留这条问答')

    def test_long_transcript_chunks_cover_every_segment_without_loss(self):
        segments = [{'start': i, 'end': i + 1, 'speaker': '我' if i % 2 else '对方',
                     'track': 'mic' if i % 2 else 'system', 'text': f'价格讨论 {i} budget and delivery ' * 15}
                    for i in range(500)]
        value = {'segments': segments, 'revision': 1}
        chunks = split_transcript(value, limit=10000)
        self.assertGreater(len(chunks), 1)
        combined = ''.join(chunks)
        self.assertEqual(combined.removesuffix('\n'), transcript_text(value))
        self.assertEqual(combined.count('[S'), 500)
        self.store.save_transcript(self.record_id, segments, original=True)
        self.assertEqual(len(self.store.listing('budget')), 1)


if __name__ == '__main__':
    unittest.main()
