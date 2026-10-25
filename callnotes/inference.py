"""Choose a usable local backend, including PCs without NVIDIA/CUDA."""
import os


def load_whisper(model_path, progress):
    import ctranslate2
    import numpy as np
    from faster_whisper import WhisperModel
    preference = os.environ.get('CALLNOTES_ASR_DEVICE', 'auto')
    try:
        gpu = preference == 'cuda' or (preference == 'auto' and ctranslate2.get_cuda_device_count() > 0)
    except (RuntimeError, OSError):
        gpu = preference == 'cuda'
    if gpu:
        try:
            progress('正在准备本地字幕模型（GPU）…')
            model = WhisperModel(str(model_path), device='cuda', compute_type='float16', local_files_only=True)
            # CUDA libraries can load only on first inference: verify before declaring ready.
            segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language='zh', beam_size=1)
            list(segments)
            return model
        except (RuntimeError, OSError):
            if preference == 'cuda':
                raise
            progress('GPU 环境不可用，自动使用 CPU 继续识别…')
    progress('正在准备本地字幕模型（CPU；速度取决于电脑性能）…')
    return WhisperModel(str(model_path), device='cpu', compute_type='int8',
                        cpu_threads=max(1, min(8, (os.cpu_count() or 4) - 1)), local_files_only=True)
