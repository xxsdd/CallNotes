import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from callnotes.asr_quality import assess, choose_language, decode_speech


def segment(text, duration=2., logprob=-.2, no_speech=.1, word_prob=.9):
    return SimpleNamespace(text=text, start=0., end=duration, avg_logprob=logprob,
        no_speech_prob=no_speech, compression_ratio=1.,
        words=[SimpleNamespace(probability=word_prob)])


class QualityTests(unittest.TestCase):
    def test_restricted_language_selection_and_short_utterance_context(self):
        self.assertEqual(choose_language([('ja', .8), ('zh', .15), ('en', .05)], 1), 'zh')
        self.assertEqual(choose_language([('en', .7), ('zh', .1)], 1), 'zh')
        self.assertEqual(choose_language([('en', .98), ('zh', .01)], 4), 'en')
        self.assertEqual(choose_language([('zh', .9), ('en', .05)], 4, 'en'), 'zh')
        self.assertEqual(choose_language([('en', .99)], 5, mode='zh'), 'zh')

    def test_noise_outros_and_impossible_timing_are_rejected(self):
        for value in [segment('Thank you for watching.', duration=.16),
                      segment('谢谢观看。', logprob=-.8),
                      segment('随机内容', logprob=-1., no_speech=.95),
                      segment('ドゥロリーティアとは'),
                      segment('这是一段虚构文字', duration=0),
                      segment('无法可靠识别', logprob=-2.)]:
            self.assertFalse(assess(value)['accepted'], value.text)

    def test_real_english_is_not_filtered_by_its_phrase_alone(self):
        for text in ['Thank you for watching.', 'Thank you.', 'The budget is 1200 dollars.', '这次 budget 是 1200 dollars。']:
            self.assertTrue(assess(segment(text, duration=3))['accepted'])
        uncertain = assess(segment('人名可能有误', logprob=-.95, word_prob=.4))
        self.assertTrue(uncertain['accepted'])
        self.assertTrue(uncertain['review'])

    def test_decoder_does_not_override_selected_language_or_use_random_fallback(self):
        model = MagicMock()
        model.detect_language.return_value = ('ja', .8, [('ja', .8), ('zh', .15), ('en', .05)])
        model.transcribe.return_value = ([segment('中文内容')], None)
        values, language = decode_speech(model, [0.] * 48000, {}, final=True)
        self.assertEqual(language, 'zh')
        options = model.transcribe.call_args.kwargs
        self.assertFalse(options['multilingual'])
        self.assertEqual(options['temperature'], 0.)
        self.assertTrue(options['word_timestamps'])
        self.assertEqual(options['language'], 'zh')
        self.assertTrue(values[0][1]['accepted'])


if __name__ == '__main__':
    unittest.main()
