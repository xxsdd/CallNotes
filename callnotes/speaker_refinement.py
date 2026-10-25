"""Same-record acoustic evidence for overlap; never uses words or real names."""
from collections import defaultdict

REFINEMENT = 'exclusive-reference-v1'


def exclusive_regions(turns):
    events = []
    for turn in turns:
        events.extend([(turn['start'], 1, turn['speaker_id']), (turn['end'], -1, turn['speaker_id'])])
    active, regions, previous = defaultdict(int), [], 0.
    for now, change, speaker in sorted(events):
        keys = [key for key, count in active.items() if count > 0]
        if now > previous and keys:
            identity = tuple(sorted(keys))
            if regions and regions[-1]['speakers'] == identity and regions[-1]['end'] == previous:
                regions[-1]['end'] = now
            else:
                regions.append(dict(start=previous, end=now, speakers=identity))
        active[speaker] += change
        previous = now
    return regions


def acoustic_refinement(samples, offset, turns, model, progress=lambda text: None):
    import numpy as np
    import sherpa_onnx
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(model), num_threads=2))

    def embedding(start, end):
        first = max(0, int((start - offset) * 16000))
        last = min(len(samples), int((end - offset) * 16000))
        if last - first < 16000 * .65:
            return None
        stream = extractor.create_stream()
        stream.accept_waveform(sample_rate=16000, waveform=np.ascontiguousarray(samples[first:last]))
        stream.input_finished()
        if not extractor.is_ready(stream):
            return None
        vector = np.asarray(extractor.compute(stream))
        return vector / max(float(np.linalg.norm(vector)), 1e-9)

    regions = exclusive_regions(turns)
    clean = defaultdict(list)
    for region in regions:
        if len(region['speakers']) == 1 and region['speakers'][0] != 'unknown' and region['end'] - region['start'] >= 1.5:
            clean[region['speakers'][0]].append(region)
    references = {}
    for speaker, regions_for_speaker in clean.items():
        examples = sorted(regions_for_speaker, key=lambda r: r['end'] - r['start'], reverse=True)[:6]
        vectors = [embedding(r['start'] + .08, min(r['end'] - .08, r['start'] + 5.)) for r in examples]
        vectors = [v for v in vectors if v is not None]
        if vectors:
            centroid = np.mean(vectors, axis=0)
            references[speaker] = centroid / max(float(np.linalg.norm(centroid)), 1e-9)
    evidence = []
    if len(references) >= 2:
        progress('正在用本次录音的清晰声音核对重叠片段…')
        for region in regions:
            if len(region['speakers']) < 2 or region['end'] - region['start'] < .65:
                continue
            start = region['start']
            while start < region['end'] - .64:
                end = min(region['end'], start + 3.)
                vector = embedding(start, end)
                if vector is not None:
                    scores = sorted(((speaker, float(vector @ reference)) for speaker, reference in references.items()),
                                    key=lambda item: item[1], reverse=True)
                    speaker, similarity = scores[0]
                    margin = similarity - scores[1][1]
                    # Similarity is a diagnostic, not a calibrated probability.
                    if speaker in region['speakers'] and similarity >= .60 and margin >= .13:
                        evidence.append(dict(start=round(start, 3), end=round(end, 3), speaker_id=speaker,
                            similarity=round(similarity, 4), margin=round(margin, 4)))
                start += 1.5
    return dict(policy=REFINEMENT, reference_speakers=sorted(references), evidence=evidence)
