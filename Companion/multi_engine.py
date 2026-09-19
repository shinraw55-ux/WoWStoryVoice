import time
from pathlib import Path
import numpy as np
import soundfile as sf

import tts_registry


def configure_runtime(runtime_module):
    if getattr(runtime_module, "_multi_engine_configured", False):
        return
    runtime_module.SETTINGS.setdefault("tts_engine", tts_registry.DEFAULT_ENGINE)
    runtime_module.SETTINGS["tts_engine"] = tts_registry.normalize_engine(runtime_module.SETTINGS["tts_engine"])
    if not tts_registry.SPECS[runtime_module.SETTINGS["tts_engine"]].bundled:
        old_engine = runtime_module.SETTINGS["tts_engine"]
        print(f"Configured external TTS {old_engine} is not bundled; falling back to {tts_registry.DEFAULT_ENGINE}.")
        runtime_module.SETTINGS["tts_engine"] = tts_registry.DEFAULT_ENGINE
    runtime_module.save_settings(runtime_module.SETTINGS)

    selected = runtime_module.SETTINGS["tts_engine"]
    spec = tts_registry.SPECS[selected]
    runtime_module.TTS_ENGINE_NAME = spec.label
    runtime_module.CACHE = runtime_module.DATA / ("cache-" + selected)
    runtime_module.CACHE.mkdir(parents=True, exist_ok=True)
    if selected == tts_registry.ENGINE_CHATTERBOX:
        runtime_module.FAST_TTS_SEGMENT_CHARS = 48

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
        pipeline_started = time.perf_counter()
        rendered_text = str(text)

        decorate_started = time.perf_counter()
        if getattr(tts, "engine_name", "") == "Chatterbox Turbo":
            try:
                import chatterbox_backend
                profile = runtime_module.emotion_profiles.infer_emotion("dialogue", rendered_text, "")
                rendered_text = chatterbox_backend.decorate_text(rendered_text, profile.name, profile.score)
            except Exception as e:
                print(f"Chatterbox emotion decoration warning: {type(e).__name__}: {e}")
        decorate_ms = (time.perf_counter() - decorate_started) * 1000.0

        generate_started = time.perf_counter()
        audio, sr, source = tts.generate(rendered_text, voice=voice, speed=speed)
        generate_ms = (time.perf_counter() - generate_started) * 1000.0

        post_started = time.perf_counter()
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            raise RuntimeError(f"{tts.engine_name} returned empty audio")
        peak = float(np.max(np.abs(audio)))
        if not np.isfinite(peak) or peak <= 0.00001:
            raise RuntimeError(f"{tts.engine_name} returned silent/invalid audio (peak={peak})")
        volume = max(0.0, min(1.0, float(volume)))
        audio *= volume
        post_ms = (time.perf_counter() - post_started) * 1000.0

        write_started = time.perf_counter()
        sf.write(wav, audio, int(sr), subtype="PCM_16")
        wav_write_ms = (time.perf_counter() - write_started) * 1000.0

        total_ms = (time.perf_counter() - pipeline_started) * 1000.0
        backend_timings = dict(getattr(tts, "last_timings", {}) or {})
        tts.last_pipeline_timings = {
            "decorate_ms": decorate_ms,
            "generate_call_ms": generate_ms,
            "postprocess_ms": post_ms,
            "wav_write_ms": wav_write_ms,
            "pipeline_total_ms": total_ms,
            "backend": backend_timings,
            "voice_source": str(source),
        }
        print(
            "LATENCY TTS_PIPELINE "
            f"engine={getattr(tts, 'engine_name', 'unknown')!r} chars={len(rendered_text)} "
            f"decorate={decorate_ms:.1f}ms generate_call={generate_ms:.1f}ms "
            f"post={post_ms:.1f}ms wav_write={wav_write_ms:.1f}ms total={total_ms:.1f}ms"
        )
        return int(sr), audio.size, peak * volume

    runtime_module.initialize_tts = initialize_tts
    runtime_module.synthesize_to_wav = synthesize_to_wav
    runtime_module._multi_engine_configured = True


def set_selected_engine(runtime_module, key):
    key = tts_registry.normalize_engine(key)
    runtime_module.SETTINGS["tts_engine"] = key
    runtime_module.save_settings(runtime_module.SETTINGS)
    return key
