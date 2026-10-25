import importlib.util
import unittest
from callnotes.speaker_windows import clustered_windows, window_turns
from callnotes.speaker_alignment import count_warning


class SpeakerWindowTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('scipy'), 'clustering is tested in the ASR environment')
    def test_three_separated_acoustic_groups_numbered_by_appearance(self):
        import numpy as np
        vectors = [[0,1,0], [.02,1,0], [1,0,0], [1,.02,0], [0,0,1], [0,.02,1]]
        windows = [dict(start=t, end=t+2., region=i//2) for i,t in enumerate([0,1,5,6,10,11])]
        result = clustered_windows(vectors, windows, 3)
        self.assertIsNotNone(result)
        self.assertEqual([w['group'] for w in result['windows']], [1,1,2,2,3,3])
        # Similar embeddings are not accepted as three distinct people.
        same = np.asarray([[1,.001*i,0] for i in range(6)])
        self.assertIsNone(clustered_windows(same, windows, 3))

    @unittest.skipUnless(importlib.util.find_spec('scipy'), 'clustering is tested in the ASR environment')
    def test_tiny_outlier_cannot_manufacture_a_third_speaker(self):
        vectors = [[1,0,0],[1,.01,0],[0,1,0],[.01,1,0],[0,0,1],[0,.01,1]]
        windows = [dict(start=t,end=t+d,region=i//2) for i,(t,d) in enumerate([(0,2),(1,2),(4,2),(5,2),(9,.7),(9.1,.7)])]
        self.assertIsNone(clustered_windows(vectors, windows, 3))

    @unittest.skipUnless(importlib.util.find_spec('scipy'), 'clustering is tested in the ASR environment')
    def test_auto_count_uses_voice_distance_without_a_requested_count(self):
        vectors = [[0,1,0], [.02,1,0], [1,0,0], [1,.02,0], [0,0,1], [0,.02,1]]
        windows = [dict(start=t, end=t+2., region=i//2) for i,t in enumerate([0,1,5,6,10,11])]
        result = clustered_windows(vectors,windows,0)
        self.assertEqual(result['detected_speakers'],3)
        self.assertEqual(result['selection']['mode'],'automatic')
        single = [[1,.001*i,0] for i in range(6)]
        self.assertEqual(clustered_windows(single,windows,0)['detected_speakers'],1)
        self.assertIsNone(clustered_windows([[0,0,0]]*6,windows,0))

    def test_short_windows_restore_voice_boundaries_and_do_not_fill_overlap_or_silence(self):
        regions = [dict(start=0.,end=6.,speakers=('collapsed',)),
                   dict(start=8.,end=9.,speakers=('a','b')),
                   dict(start=11.,end=13.,speakers=('collapsed',))]
        windows = [dict(start=0,end=3,region=0,group=1),dict(start=3,end=6,region=0,group=2),
                   dict(start=11,end=13,region=2,group=3)]
        result = window_turns(regions, windows, 'system')
        self.assertEqual(result, [dict(start=0.,end=3.,speaker_id='system-speaker-1'),
                                  dict(start=3.,end=6.,speaker_id='system-speaker-2'),
                                  dict(start=8.,end=9.,speaker_id='unknown'),
                                  dict(start=11.,end=13.,speaker_id='system-speaker-3')])

    def test_count_mismatch_and_missing_text_voice_are_visible(self):
        data = dict(requested_speakers=3,detected_speakers=2,track='system')
        values = [dict(track='system',speaker_id='system-speaker-1'), dict(track='mic',speaker_id='mic'),
                  dict(track='system',speaker_id='unknown')]
        self.assertIn('尚未完整区分', count_warning(data,values))
        data['detected_speakers']=3
        self.assertIn('仅出现 1', count_warning(data,values))
        values += [dict(track='system',speaker_id=f'system-speaker-{i}') for i in (2,3)]
        self.assertEqual(count_warning(data,values),'')
        data['requested_speakers']=0
        self.assertEqual(count_warning(data,values),'')
        self.assertIn('部分声音尚未对齐文字',count_warning(data,values[:2]))
