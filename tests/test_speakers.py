import unittest
from callnotes.speaker_alignment import align_speakers
from callnotes.turns import speaker_turns
from callnotes.storage import transcript_text
from callnotes.speaker_refinement import exclusive_regions


def source(text, words=None, **extra):
    return dict(start=0., end=4., track='imported', speaker='导入音频', text=text,
                **({'words': words} if words else {}), **extra)


def turns():
    return dict(signature='run1', turns=[dict(start=0., end=2., speaker_id='imported-speaker-1'),
                dict(start=2., end=4., speaker_id='imported-speaker-2')])


class AlignmentTests(unittest.TestCase):
    def test_word_level_switch_preserves_text_timing_and_names(self):
        words = [dict(start=0., end=1., word='Hello,'), dict(start=1., end=2., word=' friend.'),
                 dict(start=2., end=3., word='你好'), dict(start=3., end=4., word='。')]
        result = align_speakers([source('Hello, friend.你好。', words)], turns(), {'imported-speaker-1': '小王'})
        self.assertEqual([s['speaker'] for s in result], ['小王', '说话人 2'])
        self.assertEqual(''.join(s['text'] for s in result), 'Hello, friend.你好。')
        self.assertEqual([(s['start'], s['end']) for s in result], [(0., 2.), (2., 4.)])
        self.assertEqual(len(speaker_turns(result)), 2)

    def test_overlap_and_absent_evidence_are_marked_unknown(self):
        data = dict(signature='run1', turns=[dict(start=0., end=4., speaker_id='a'), dict(start=0., end=4., speaker_id='b')])
        for info in (data, dict(signature='empty', turns=[])):
            result = align_speakers([source('无法可靠归属的内容')], info)[0]
            self.assertEqual(result['text'], '无法可靠归属的内容')
            self.assertEqual(result['speaker_id'], 'unknown')
            self.assertTrue(result['speaker_uncertain'])
            self.assertIn('归属待确认', transcript_text(dict(segments=[result])))

    def test_human_text_never_replaced_by_stale_word_alignment(self):
        words = [dict(start=0., end=1., word='错词')]
        result = align_speakers([source('人工校正后的文字', words)], turns())
        self.assertEqual(''.join(s['text'] for s in result), '人工校正后的文字')
        self.assertTrue(result[0]['speaker_uncertain'])
        self.assertNotIn('words', result[0])

    def test_manual_speaker_and_recorded_tracks_unchanged(self):
        manual = source('已确认', speaker_manual=True, speaker_id='manual-张三')
        mic = dict(start=0, end=1, text='我', speaker='我', track='mic')
        self.assertEqual(align_speakers([manual, mic], turns()), [manual, mic])

    def test_same_display_name_does_not_merge_different_voices(self):
        a = dict(start=0, end=1, track='imported', text='第一人', speaker='朋友', speaker_id='a')
        b = dict(start=1, end=2, track='imported', text='第二人', speaker='朋友', speaker_id='b')
        self.assertEqual(len(speaker_turns([a,b])), 2)

    def test_small_asr_boundary_error_does_not_create_a_spurious_speaker(self):
        segment = source('Hello', [dict(start=.85,end=.98,word='Hello')])
        data = dict(signature='edge',turns=[dict(start=1.,end=2.,speaker_id='a')])
        result = align_speakers([segment],data)
        self.assertEqual(result[0]['speaker_id'],'a')
        self.assertFalse(result[0]['speaker_uncertain'])

    def test_recorded_computer_voices_split_but_microphone_is_preserved(self):
        words = [dict(start=0,end=2,word='朋友甲'),dict(start=2,end=4,word='朋友乙')]
        system = dict(start=0,end=4,text='朋友甲朋友乙',words=words,track='system',speaker='对方')
        mic = dict(start=1,end=2,text='我原来修改好的话',track='mic',speaker='我')
        data = dict(signature='system1',track='system',turns=[
            dict(start=0,end=2,speaker_id='system-speaker-1'),dict(start=2,end=4,speaker_id='system-speaker-2')])
        result = align_speakers([system,mic],data)
        self.assertEqual([s['speaker'] for s in result],['说话人 1','我','说话人 2'])
        self.assertEqual(result[1],mic)
        self.assertEqual(result[2]['track'],'system')

    def test_low_coverage_short_onset_gets_reference_without_claiming_confirmation(self):
        value=source('我',[dict(start=.8,end=1.06,word='我')])
        data=dict(signature='edge',turns=[dict(start=1.,end=4.,speaker_id='a')])
        assigned=align_speakers([value],data)[0]
        self.assertEqual(assigned['speaker_id'],'a')
        self.assertTrue(assigned['speaker_inferred'])
        self.assertIn('未经人工确认',transcript_text(dict(segments=[assigned])))

    def test_overlap_needs_acoustic_evidence_and_keeps_that_caveat(self):
        data=dict(signature='overlap',turns=[dict(start=0,end=4,speaker_id='a'),dict(start=0,end=4,speaker_id='b')])
        original=source('没有证据就保留未知')
        self.assertEqual(align_speakers([original],data)[0]['speaker_id'],'unknown')
        data['refinement']=dict(evidence=[dict(start=0,end=4,speaker_id='b',similarity=.8,margin=.3)])
        assigned=align_speakers([original],data)[0]
        self.assertEqual(assigned['speaker_id'],'b')
        self.assertTrue(assigned['speaker_inferred'])
        self.assertEqual(assigned['text'],original['text'])

    def test_reading_bridge_keeps_unknown_source_and_exact_source_indices(self):
        a=dict(start=0,end=1,text='开始',track='system',speaker='小王',speaker_id='a')
        unknown=dict(start=1,end=2,text='不清楚',track='system',speaker='待确认',speaker_id='unknown',speaker_uncertain=True)
        b=dict(start=2,end=3,text='结束',track='system',speaker='小王',speaker_id='a')
        originals=[dict(a),dict(unknown),dict(b)]
        result=speaker_turns(originals)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['indices'],[0,1,2])
        self.assertEqual(result[0]['uncertain_ranges'],[(2,5)])
        self.assertTrue(result[0]['contains_unresolved'])
        self.assertEqual(originals[1],unknown)
        self.assertEqual(result[0]['text'],'开始不清楚结束')

    def test_real_switch_long_gap_and_microphone_interruptions_never_bridged(self):
        a=dict(start=0,end=1,text='开始',track='system',speaker='小王',speaker_id='a')
        unknown=dict(start=1,end=2,text='不清楚',track='system',speaker='待确认',speaker_id='unknown')
        b=dict(start=2,end=3,text='结束',track='system',speaker='小王',speaker_id='a')
        c=dict(start=2,end=3,text='别人',track='system',speaker='小李',speaker_id='b')
        self.assertEqual(len(speaker_turns([a,unknown,c])),3)
        self.assertEqual(len(speaker_turns([a,{**unknown,'end':5},{**b,'start':5,'end':6}])),3)
        mic=dict(start=1.5,end=1.8,text='插话',track='mic',speaker='我')
        self.assertEqual(len(speaker_turns([a,unknown,mic,b])),4)

    def test_reference_regions_exclude_overlap_and_merge_contiguous_same_voice(self):
        raw=[dict(start=0,end=4,speaker_id='a'),dict(start=1,end=2,speaker_id='b'),dict(start=4,end=6,speaker_id='a')]
        regions=exclusive_regions(raw)
        self.assertEqual(regions,[dict(start=0,end=1,speakers=('a',)),dict(start=1,end=2,speakers=('a','b')),dict(start=2,end=6,speakers=('a',))])
