"""Shared local decoding policy for Chinese/English conversation.

Recognition scores are diagnostic signals, not calibrated accuracy percentages.
No guessed replacement text is generated for rejected audio.
"""
import re
from statistics import mean

UNSUPPORTED_SCRIPT = re.compile(r'[\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff\u0600-\u06ff]')
OUTRO = re.compile(r'^(?:thank you for watching|thanks for watching|please subscribe|感谢观看|谢谢观看|感谢收看)[.!。！\s]*$', re.I)


def choose_language(probabilities, seconds, previous='zh', mode='zh_en'):
    if mode in ('zh', 'en'):
        return mode
    probabilities = dict(probabilities or [])
    previous = previous if previous in ('zh', 'en') else 'zh'
    zh, en = probabilities.get('zh', 0.), probabilities.get('en', 0.)
    # Never pick a language outside the requested pair. Short ambiguous sounds
    # should not reset an otherwise Chinese conversation to English/Japanese.
    if en >= (.92 if seconds < 2.5 else .65) and en > max(zh * 3, .01):
        return 'en'
    if zh >= .45 and zh > en * 1.5:
        return 'zh'
    return previous


def assess(segment):
    text = segment.text.strip()
    duration = max(0., segment.end - segment.start)
    logprob = float(segment.avg_logprob)
    no_speech = float(segment.no_speech_prob)
    words = segment.words or []
    probability = mean(w.probability for w in words) if words else None
    reason = None
    if not text:
        reason = 'empty'
    elif UNSUPPORTED_SCRIPT.search(text):
        reason = 'unexpected_language'
    elif duration < .10 or (duration < .5 and len(re.sub(r'\W', '', text)) > 8):
        reason = 'implausible_timing'
    elif OUTRO.fullmatch(text) and (duration < 1.2 or logprob < -.45 or no_speech > .6):
        reason = 'unsupported_outro'
    elif no_speech > .85 and logprob < -.7:
        reason = 'likely_noise'
    elif logprob < -1.6 or (probability is not None and probability < .18 and logprob < -.9):
        reason = 'unreliable_speech'
    elif getattr(segment, 'compression_ratio', 0.) > 3.0:
        reason = 'repetition'
    review = bool(reason or logprob < -.85 or (probability is not None and probability < .5))
    return dict(accepted=not reason, reason=reason, review=review,
        avg_logprob=round(logprob, 4), no_speech_prob=round(no_speech, 4),
        word_probability=round(probability, 4) if probability is not None else None)


def decode_speech(model, audio, state, final=True, mode='zh_en'):
    seconds = len(audio) / 16000
    # Detect once on the growing utterance, then explicitly set zh/en for the
    # decoder. multilingual=True would override that constraint every segment.
    _, _, probabilities = model.detect_language(audio=audio)
    language = choose_language(probabilities, seconds, state.get('language', 'zh'), mode)
    chunks, _ = model.transcribe(audio, language=language, task='transcribe',
        beam_size=5 if final else 2, temperature=0.0, multilingual=False,
        condition_on_previous_text=False, vad_filter=False,
        word_timestamps=final, hallucination_silence_threshold=.8 if final else None,
        compression_ratio_threshold=2.4, log_prob_threshold=-1.0, no_speech_threshold=.6)
    values = [(chunk, assess(chunk)) for chunk in chunks]
    if final and any(quality['accepted'] for _, quality in values) and seconds >= 2.5:
        state['language'] = language
    return values, language


def uncertain_segment(start, end, track, reasons):
    return dict(start=round(start, 3), end=round(end, 3), speaker=track['speaker'],
        track=track['key'], text='[未听清]', review=True, unintelligible=True,
        quality={'rejected_reasons': sorted(set(reasons))})
