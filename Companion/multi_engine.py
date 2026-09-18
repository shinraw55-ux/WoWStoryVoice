import hashlib
from pathlib import Path
import numpy as np
import soundfile as sf

import tts_registry

def configure_runtime(runtime_module):
    if getattr(runtime_module, "_multi_engine_configured", False):
        return
    runtime_module.SETTINGS.setdefault("tts_engine", tts_registry.DEFAULT_ENGINE)
    runtime_module.SETTINGS["tts_engine"] = tts_registry.normalize_engine(runtime_module.SETTINGS["tts_engine"])
    runtime_module.save_settings(runtime_module.SETTINGS)

    selected = runtime_module.SETTINGS["tts_engine"]
    spec = tts_registry.SPECS[selected]
    runtime_module.TTS_ENGINE_NAME = spec.label
    runtime_module.CACHE = runtime_module.DATA / ("cache-" + selected)
    runtime_module.CACHE.mkdir(parents=True, exist_ok=True)

    def initialize_tts():
        key = tts_registry.normalize_engine(runtime_module.SETTINGS.get("tts_engine"))
        backend = tts_registry.load_backend(key, runtime_module.DATA)
        try:
            backend.warmup()
        except Exception as e:
            print(f"{backend.engine_name} warm-up warning: {type(e).__name__}: {e}")
        available = backend.profile_voice_ids()
        runtime_module.STATE.set(tts_engine=backend.engine_name, tts_device=getattr(backend, "device", ""))
        print(f"TTS ready: {backend.engine_name} / {getattr(backend, 'device', '')}")
        return backend, available

    def synthesize_to_wav(tts, voice, text, wav, speed=1.0, volume=1.0):
        audio, sr, source = tts.generate(str(text), voice=voice, speed=speed)
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            raise RuntimeError(f"{tts.engine_name} returned empty audio")
        peak = float(np.max(np.abs(audio)))
        if not np.isfinite(peak) or peak <= 0.00001:
            raise RuntimeError(f"{tts.engine_name} returned silent/invalid audio (peak={peak})")
        volume = max(0.0, min(1.0, float(volume)))
        audio *= volume
        sf.write(wav, audio, int(sr), subtype="PCM_16")
        return int(sr), audio.size, peak * volume

    runtime_module.initialize_tts = initialize_tts
    runtime_module.synthesize_to_wav = synthesize_to_wav
    runtime_module._multi_engine_configured = True

def set_selected_engine(runtime_module, key):
    key = tts_registry.normalize_engine(key)
    runtime_module.SETTINGS["tts_engine"] = key
    runtime_module.save_settings(runtime_module.SETTINGS)
    return key
