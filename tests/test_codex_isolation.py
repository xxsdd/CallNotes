"""Session isolation and local follow-up context; synthetic data, no network."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from callnotes.codex_client import CodexClient, analysis_context
from callnotes.storage import Store, transcript_text


class CodexIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.rid = self.store.create('合成通话')
        self.store.save_transcript(self.rid, [dict(start=0, end=5, text='交付日为星期三。',
                                                   speaker='对方', track='system')])
        self.client = CodexClient(Path(self.tmp.name) / 'analysis-workspace')
        self.client.start = Mock()
        self.client.close = Mock()
        self.client.request = Mock(side_effect=self.reply)
        self.client.turn = Mock(return_value=('星期三。[S0001]', {'requested_model': 'test-model', 'effort': 'low'}))

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def reply(self, method, params):
        self.assertEqual(method, 'thread/start')
        self.assertIs(params['ephemeral'], True)
        self.assertEqual(params['dynamicTools'], [])
        self.assertEqual(params['model'], 'test-model')
        return dict(thread=dict(id='temporary-' + str(self.client.request.call_count),
                                ephemeral=True, path=None), model='test-model')

    def analyze(self, rid=None, question='什么时候交付？'):
        return self.client.analyze(self.store, rid or self.rid, question, 'test-model', 'low',
                                   lambda text: None, lambda text: None)

    def test_legacy_persistent_thread_never_resumed_or_overwritten(self):
        self.store.update(self.rid, thread_id='legacy-persistent', thread_revision=1)
        answer, meta = self.analyze()
        self.assertEqual(meta['session_mode'], 'ephemeral')
        self.assertEqual(self.store.get(self.rid)['thread_id'], 'legacy-persistent')
        self.assertEqual(self.store.messages(self.rid)[-1]['answer'], answer)
        self.assertEqual(self.store.messages(self.rid)[-1]['revision'], 1)
        self.assertIn(transcript_text(self.store.transcript(self.rid)), self.client.turn.call_args.args[1])
        self.client.close.assert_called_once()
        self.assertIsNone(self.client.active_thread)

    def test_repeated_questions_use_fresh_threads_and_saved_local_answers(self):
        self.analyze()
        self.analyze(question='他提到的那天是周几？')
        self.assertEqual(self.client.request.call_count, 2)
        self.assertEqual(self.client.turn.call_count, 2)  # short context needs one model turn
        prompt = self.client.turn.call_args.args[1]
        self.assertIn('什么时候交付？', prompt)
        self.assertIn('星期三。[S0001]', prompt)
        self.assertIn('他提到的那天是周几？', prompt)
        self.assertEqual(len(self.store.messages(self.rid)), 2)
        self.assertEqual(self.client.close.call_count, 2)

    def test_followup_after_client_restart_uses_local_context(self):
        self.store.add_message(self.rid, dict(revision=1, question='之前的问题', answer='之前的回答'))
        self.analyze(question='继续之前的问题')
        self.assertIn('之前的回答', self.client.turn.call_args.args[1])

    def test_other_record_and_old_revision_answers_are_excluded(self):
        other = self.store.create('另一条通话')
        self.store.add_message(other, dict(revision=1, question='其他记录', answer='其他记录的私有内容'))
        self.store.add_message(self.rid, dict(revision=0, question='旧版本', answer='过时的内容'))
        self.analyze()
        prompt = self.client.turn.call_args.args[1]
        self.assertNotIn('其他记录的私有内容', prompt)
        self.assertNotIn('过时的内容', prompt)

    def test_unconfirmed_isolation_submits_no_transcript_and_keeps_local_data(self):
        for thread in (dict(id='wrong', ephemeral=False, path='stored.jsonl'),
                       dict(id='missing'), dict(id='wrong', ephemeral=True, path='stored.jsonl')):
            with self.subTest(thread=thread):
                self.client.request.side_effect = None
                self.client.request.return_value = dict(thread=thread)
                with self.assertRaisesRegex(RuntimeError, '未发送文字稿'):
                    self.analyze()
                self.client.turn.assert_not_called()
                self.assertEqual(self.store.messages(self.rid), [])
                self.assertEqual(self.store.transcript(self.rid)['revision'], 1)
        self.assertEqual(self.client.close.call_count, 3)

    def test_failure_releases_session_and_does_not_save_partial_answer(self):
        self.client.turn.side_effect = RuntimeError('合成网络错误')
        with self.assertRaisesRegex(RuntimeError, '合成网络错误'):
            self.analyze()
        self.client.close.assert_called_once()
        self.assertIsNone(self.client.active_thread)
        self.assertEqual(self.store.messages(self.rid), [])

    def test_long_context_chunks_keep_all_text_and_history(self):
        transcript = self.store.transcript(self.rid)
        history = [dict(revision=1, question='长问题', answer='长答案' * 3000)]
        whole = analysis_context(transcript, history, limit=100000)
        parts = analysis_context(transcript, history, limit=500)
        self.assertGreater(len(parts), 1)
        self.assertEqual(''.join(parts), ''.join(whole))
        self.assertTrue(all(len(part) <= 500 for part in parts))

    def test_cancel_during_context_upload_does_not_submit_question_or_save(self):
        self.store.add_message(self.rid, dict(revision=1, question='长问题', answer='长答案' * 20000))
        def cancel(*args):
            self.client.cancel.set()
            return '已阅读本部分', {}
        self.client.turn.side_effect = cancel
        with self.assertRaisesRegex(RuntimeError, '取消'):
            self.analyze(question='不应提交的问题')
        self.assertEqual(self.client.turn.call_count, 1)
        self.assertNotIn('不应提交的问题', self.client.turn.call_args.args[1])
        self.assertEqual(len(self.store.messages(self.rid)), 1)
        self.client.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
