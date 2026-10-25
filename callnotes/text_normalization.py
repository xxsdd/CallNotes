"""Simplified text without installing into the user's VoiceStudio environment."""
import re
import sys
import threading
from pathlib import Path

_converter = None
_lock = threading.Lock()
_han = re.compile(r'[\u3400-\u9fff]')


def simplified(text):
    global _converter
    if not text or not _han.search(text):
        return text
    if _converter is None:
        with _lock:
            if _converter is None:
                runtime = Path(r'D:\Apps\CallNotes\runtime\text')
                if runtime.exists() and str(runtime) not in sys.path:
                    sys.path.insert(0, str(runtime))
                from opencc import OpenCC
                _converter = OpenCC('t2s')
    return _converter.convert(text)
