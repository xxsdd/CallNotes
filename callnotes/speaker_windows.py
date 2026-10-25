"""Review automatic or collapsed fixed-count diarization with acoustic windows.

Automatic mode estimates the count; fixed mode reviews a missing requested count.
No words, names, or arbitrary time splits are used to manufacture speakers.
"""
from collections import defaultdict

POLICY = 'contiguous-windows-auto-v2'


def clustered_windows(vectors, windows, count):
    import numpy as np
    from scipy.cluster.hierarchy import linkage, cut_tree
    vectors = np.asarray(vectors, dtype=np.float64)
    if vectors.ndim != 2 or len(vectors) != len(windows) or not np.isfinite(vectors).all():
        return None
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(norms < 1e-8):
        return None
    vectors = vectors / norms
    if count == 0:
        if len(vectors) < 2:
            return None
        # A distance threshold chooses the count from acoustic similarity.
        # Retry coarser cuts if tiny/weak groups fail the evidence checks below.
        tree = linkage(vectors, method='average', metric='cosine')
        tried = set()
        for height in (.65, .70, .75):
            selected = len(set(cut_tree(tree, height=height).reshape(-1)))
            if selected > 20 or selected in tried:
                continue
            tried.add(selected)
            result = clustered_windows(vectors, windows, selected)
            if result is not None:
                result['selection'] = dict(mode='automatic', distance_threshold=height)
                return result
        return None
    if len(vectors) < count * 2 or count < 1:
        return None
    labels = (np.zeros(len(vectors), dtype=int) if count == 1 else
              cut_tree(linkage(vectors, method='average', metric='cosine'), n_clusters=count).reshape(-1))
    centers, diagnostics = [], []
    for label in range(count):
        selected = [i for i, value in enumerate(labels) if value == label]
        coverage, stop = 0., -1.
        for start, end in sorted((windows[i]['start'], windows[i]['end']) for i in selected):
            coverage += max(0., end - max(start, stop))
            stop = max(stop, end)
        center = vectors[selected].mean(axis=0)
        center /= max(float(np.linalg.norm(center)), 1e-9)
        cohesion = float(np.median(vectors[selected] @ center))
        # Multiple overlapping windows must also cover enough unique audio.
        if len(selected) < 2 or coverage < 1.8 or cohesion < .65:
            return None
        centers.append(center)
        diagnostics.append(dict(windows=len(selected), seconds=round(coverage, 3), cohesion=round(cohesion, 4)))
    centers = np.asarray(centers)
    if count > 1 and np.max((centers @ centers.T)[~np.eye(count, dtype=bool)]) > .82:
        return None
    # Number voices by first supported appearance, independent of scipy labels.
    order = sorted(range(count), key=lambda label: min(windows[i]['start'] for i, value in enumerate(labels) if value == label))
    names = {label: i + 1 for i, label in enumerate(order)}
    output = []
    for window, vector, label in zip(windows, vectors, labels):
        scores = vector @ centers.T
        ranked = sorted(map(float, scores), reverse=True)
        margin = ranked[0] - ranked[1] if count > 1 else 1.
        accepted = int(scores.argmax()) == label and scores[label] >= .55 and margin >= .08
        output.append({**window, 'group': names[int(label)] if accepted else None,
                       'similarity': round(float(scores[label]), 4), 'margin': round(margin, 4)})
    if len({w['group'] for w in output if w['group'] is not None}) != count:
        return None
    return dict(detected_speakers=count, windows=output, clusters=[diagnostics[i] for i in order],
                center_similarities=np.round((centers @ centers.T)[np.ix_(order, order)], 4).tolist())


def window_turns(regions, windows, track):
    """Keep silence/overlap boundaries; split speech at acoustic-window changes."""
    grouped = defaultdict(list)
    for window in windows:
        grouped[window['region']].append(window)
    turns = []
    for index, region in enumerate(regions):
        values = sorted(grouped[index], key=lambda w: w['start']) if len(region['speakers']) == 1 else []
        if not values:
            turns.append(dict(start=region['start'], end=region['end'], speaker_id='unknown'))
            continue
        boundaries = [region['start']] + [((a['start'] + a['end']) / 2 + (b['start'] + b['end']) / 2) / 2
                                          for a, b in zip(values, values[1:])] + [region['end']]
        for i, window in enumerate(values):
            identity = f'{track}-speaker-{window["group"]}' if window['group'] else 'unknown'
            start, end = round(boundaries[i], 3), round(boundaries[i + 1], 3)
            if end <= start:
                continue
            if turns and turns[-1]['speaker_id'] == identity and abs(turns[-1]['end'] - start) < .001:
                turns[-1]['end'] = end
            else:
                turns.append(dict(start=start, end=end, speaker_id=identity))
    return turns


def recover_count(samples, offset, original_turns, model, count, track, progress):
    import numpy as np
    import sherpa_onnx
    try:
        from .speaker_refinement import exclusive_regions
    except ImportError:
        from speaker_refinement import exclusive_regions
    regions = exclusive_regions(original_turns)
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=2))
    vectors, windows = [], []
    progress('正在用清晰短片段自动判断说话人数…' if count == 0 else '人数与设置不符，正在用清晰短片段复核声音分组…')
    for index, region in enumerate(regions):
        if len(region['speakers']) != 1 or region['end'] - region['start'] < .85:
            continue
        start = region['start'] + .02
        while start < region['end'] - .77:
            end = min(start + 2.5, region['end'] - .02)
            if end - start < .75:
                break
            first, last = max(0, int((start - offset) * 16000)), min(len(samples), int((end - offset) * 16000))
            stream = extractor.create_stream()
            stream.accept_waveform(sample_rate=16000, waveform=np.ascontiguousarray(samples[first:last]))
            stream.input_finished()
            if extractor.is_ready(stream):
                vectors.append(np.asarray(extractor.compute(stream)))
                windows.append(dict(start=start, end=end, region=index))
            if end >= region['end'] - .10:
                break
            start += 1.25
    result = clustered_windows(vectors, windows, count)
    if result is None:
        return None
    turns = window_turns(regions, result['windows'], track)
    if len({t['speaker_id'] for t in turns if t['speaker_id'] != 'unknown'}) != result['detected_speakers']:
        return None
    return dict(policy=POLICY, turns=turns, **result)
