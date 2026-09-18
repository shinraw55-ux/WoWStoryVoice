from pathlib import Path
import numpy as np

ENGINE_NAME = "Kokoro ONNX"
ENGINE_NAMESPACE = "kokoro-onnx-v1"

class KokoroBackend:
    def __init__(self, data_dir):
        import companion as engine
        self.engine = engine
        self.data_dir = Path(data_dir)
        model, voices = engine.ensure_models()
        from kokoro_onnx import Kokoro
        self.model = Kokoro(str(model), str(voices))
        self.available = list(self.model.get_voices())
        if not self.available:
            raise RuntimeError("Kokoro reported no voices")
        self.engine_name = ENGINE_NAME
        self.device = "ONNX"
        self.cache_namespace = ENGINE_NAMESPACE

    def profile_voice_ids(self):
        return list(self.available)

    def warmup(self):
        self.generate("Voice system ready.", voice=self.available[0])

    def generate(self, text, *, voice="", speed=1.0, **_):
        voice = voice if voice in self.available else self.available[0]
        audio, sr = self.model.create(str(text), voice=voice, speed=float(speed), lang="en-us")
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            raise RuntimeError("Kokoro returned empty audio")
        return audio, int(sr), voice
