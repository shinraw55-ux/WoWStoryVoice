import time
from pathlib import Path

import numpy as np
import soundfile as sf

import chatterbox_backend


# Chatterbox Turbo is autoregressive: smaller first segments reduce the amount
# of speech that must be generated before the first WAV can start playing.
CHATTERBOX_FIRST_SEGMENT_CHARS = 90


def configure_runtime(runtime_module):
    if getattr(runtime_module, "_chatterbox_configured", False):
        return

    runtime_module.CACHE = runtime_module.DATA / "cache-chatterbox-turbo"
    runtime_module.CACHE.mkdir(parents=True, exist_ok=True)
    runtime_module.FAST_TTS_SEGMENT_CHARS = CHATTERBOX_FIRST_SEGMENT_CHARS
    runtime_module.TTS_ENGINE_NAME = chatterbox_backend.ENGINE_NAME

    def initialize_tts():
        print(f"WoW Story Voice v{runtime_module.VERSION}")
        print("Transport: WSV6 binary RGB + chunk reassembly")
        print("Speech engine: Chatterbox Turbo (experimental)")
        print("Preparing Chatterbox Turbo. First launch downloads the model from Hugging Face...")

        started = time.perf_counter()
        backend = chatterbox_backend.ChatterboxBackend(runtime_module.DATA)
        load_ms = (time.perf_counter() - started) * 1000.0
        print(
            f"Chatterbox Turbo ready on {backend.device.upper()} in {load_ms:.0f} ms. "
            f"Reference voice folder: {backend.voice_dir}"
        )

        warm_started = time.perf_counter()
        try:
            backend.warmup()
            warm_ms = (time.perf_counter() - warm_started) * 1000.0
            print(f"Chatterbox warm-up complete in {warm_ms:.0f} ms.")
        except Exception as e:
            print(f"Chatterbox warm-up warning: {type(e).__name__}: {e}")

        runtime_module.STATE.set(
            tts_engine=backend.engine_name,
            tts_device=backend.device.upper(),
        )
        return backend, chatterbox_backend.profile_voice_ids()

    def synthesize_to_wav(tts, voice, text, wav, speed=1.0, volume=1.0):
        # Reuse the existing text/context classifier to add only conservative
        # Turbo-native event tags on strongly signalled lines. Chatterbox Turbo
        # does not expose Kokoro's speed parameter, so speed remains handled by
        # segmentation/pause policy rather than an expensive post-process.
        try:
            profile = runtime_module.emotion_profiles.infer_emotion("dialogue", text, "")
            rendered_text = chatterbox_backend.decorate_text(text, profile.name, profile.score)
            emotion = profile.name
        except Exception:
            rendered_text = str(text)
            emotion = "neutral"

        audio, sr, voice_source = tts.generate(rendered_text, voice=voice)
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            raise RuntimeError("Chatterbox returned an empty audio buffer")
        source_peak = float(np.max(np.abs(audio)))
        if not np.isfinite(source_peak) or source_peak <= 0.00001:
            raise RuntimeError(f"Chatterbox returned silent/invalid audio (peak={source_peak})")

        volume = max(0.0, min(1.0, float(volume)))
        audio = audio * volume
        sf.write(wav, audio, sr, subtype="PCM_16")
        print(
            f"Chatterbox render: voice_profile={voice}, voice_source={voice_source}, "
            f"emotion={emotion}, requested_speed={speed:.2f}"
        )
        return sr, audio.size, source_peak * volume

    runtime_module.initialize_tts = initialize_tts
    runtime_module.synthesize_to_wav = synthesize_to_wav
    runtime_module._chatterbox_configured = True
