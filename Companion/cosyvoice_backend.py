"""CosyVoice adapter.

CosyVoice upstream is intentionally isolated from the main packaged Python
environment because its documented install uses its own Python/Conda stack and
Matcha-TTS checkout. This adapter only activates when that runtime is present;
it never silently pretends CosyVoice is available.
"""
import os
import sys
from pathlib import Path
import numpy as np

ENGINE_NAME = "CosyVoice"
ENGINE_NAMESPACE = "cosyvoice-v1"

class CosyVoiceBackend:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.root = Path(os.environ.get("WSV_COSYVOICE_ROOT", self.data_dir / "CosyVoice"))
        self.model_dir = Path(os.environ.get("WSV_COSYVOICE_MODEL", self.root / "pretrained_models" / "CosyVoice2-0.5B"))
        matcha = self.root / "third_party" / "Matcha-TTS"
        if not self.root.exists():
            raise RuntimeError(f"CosyVoice runtime not installed: {self.root}")
        if not self.model_dir.exists():
            raise RuntimeError(f"CosyVoice model not installed: {self.model_dir}")
        for p in (self.root, matcha):
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
        try:
            from cosyvoice.cli.cosyvoice import CosyVoice2
        except Exception as e:
            raise RuntimeError(f"CosyVoice import failed: {e}") from e
        self.model = CosyVoice2(str(self.model_dir), load_jit=True, load_onnx=False, load_trt=False)
        self.engine_name = ENGINE_NAME
        self.device = "CUDA/CPU (CosyVoice runtime)"
        self.cache_namespace = ENGINE_NAMESPACE
        self.available = ["cosyvoice-zero-shot"]

    def profile_voice_ids(self):
        return list(self.available)

    def warmup(self):
        return None

    def generate(self, text, *, voice="", speed=1.0, prompt_text="", prompt_wav=None, **_):
        if not prompt_wav:
            raise RuntimeError("CosyVoice zero-shot requires a reference WAV")
        chunks = self.model.inference_zero_shot(str(text), str(prompt_text or ""), str(prompt_wav), stream=False)
        arrays = []
        sample_rate = int(getattr(self.model, "sample_rate", 22050))
        for item in chunks:
            speech = item.get("tts_speech") if isinstance(item, dict) else None
            if speech is None:
                continue
            if hasattr(speech, "detach"):
                speech = speech.detach().cpu().numpy()
            arrays.append(np.asarray(speech, dtype=np.float32).reshape(-1))
        if not arrays:
            raise RuntimeError("CosyVoice returned no audio")
        return np.concatenate(arrays), sample_rate, str(prompt_wav)
