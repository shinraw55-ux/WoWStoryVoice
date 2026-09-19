import threading
import time


_local = threading.local()


def _log_snapshot(runtime_module):
    fn = getattr(getattr(runtime_module, "LOG", None), "wsv_thread_io_snapshot", None)
    if callable(fn):
        try:
            return fn()
        except Exception:
            pass
    return 0.0, 0


def install(runtime_module, voice_profiles_module):
    """Add low-observer-effect latency probes around the active speech path.

    Timings are accumulated silently and emitted only after the first blocking
    playback call returns, so instrumentation itself cannot delay the measured
    call to Windows audio. Bridge scans are rare and are logged directly.
    """
    if getattr(runtime_module, "_latency_instrumentation_installed", False):
        return

    # Small isolated unit-test runtimes intentionally do not carry the full
    # companion transport module. Instrumentation must never change whether
    # those functional tests can exercise speaker/performance logic.
    engine = getattr(runtime_module, "engine", None)
    if engine is None:
        return

    base_registry = engine.VoiceRegistry

    class TimedVoiceRegistry(base_registry):
        def voice_for(self, npc_guid, npc_name):
            _local.profile_ms = 0.0
            _local.split_ms = 0.0
            _local.emotion_ms = 0.0
            _local.delivery_ms = 0.0
            _local.job_prep_started = time.perf_counter()
            _local.log_start = _log_snapshot(runtime_module)
            _local.first_play_pending = True
            started = time.perf_counter()
            try:
                return super().voice_for(npc_guid, npc_name)
            finally:
                _local.profile_ms = (time.perf_counter() - started) * 1000.0

    TimedVoiceRegistry.__name__ = "TimedVoiceRegistry"
    engine.VoiceRegistry = TimedVoiceRegistry

    original_split = engine.split_dialogue

    def timed_split(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original_split(*args, **kwargs)
        finally:
            _local.split_ms = getattr(_local, "split_ms", 0.0) + (
                time.perf_counter() - started
            ) * 1000.0

    engine.split_dialogue = timed_split

    original_infer = runtime_module.emotion_profiles.infer_emotion

    def timed_infer(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original_infer(*args, **kwargs)
        finally:
            _local.emotion_ms = getattr(_local, "emotion_ms", 0.0) + (
                time.perf_counter() - started
            ) * 1000.0

    runtime_module.emotion_profiles.infer_emotion = timed_infer

    original_delivery = runtime_module.emotion_profiles.delivery_for_segment

    def timed_delivery(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original_delivery(*args, **kwargs)
        finally:
            _local.delivery_ms = getattr(_local, "delivery_ms", 0.0) + (
                time.perf_counter() - started
            ) * 1000.0

    runtime_module.emotion_profiles.delivery_for_segment = timed_delivery

    original_play = engine.play_wav

    def timed_play(path, blocking=False):
        first = bool(getattr(_local, "first_play_pending", False))
        if first:
            _local.first_play_pending = False
            prep_end = time.perf_counter()
            prep_total_ms = (
                prep_end - getattr(_local, "job_prep_started", prep_end)
            ) * 1000.0
            log_before_ms, log_before_calls = getattr(_local, "log_start", (0.0, 0))
            log_now_ms, log_now_calls = _log_snapshot(runtime_module)
            snapshot = {
                "total_ms": prep_total_ms,
                "profile_ms": float(getattr(_local, "profile_ms", 0.0)),
                "split_ms": float(getattr(_local, "split_ms", 0.0)),
                "emotion_ms": float(getattr(_local, "emotion_ms", 0.0)),
                "delivery_ms": float(getattr(_local, "delivery_ms", 0.0)),
                "log_io_ms": max(0.0, log_now_ms - log_before_ms),
                "log_calls": max(0, log_now_calls - log_before_calls),
            }
        else:
            snapshot = None

        result = original_play(path, blocking=blocking)

        # Emit after playback returns: the measurement cannot postpone first audio.
        if snapshot is not None:
            print(
                "LATENCY PREPLAY "
                f"total={snapshot['total_ms']:.1f}ms "
                f"profile={snapshot['profile_ms']:.1f}ms split={snapshot['split_ms']:.1f}ms "
                f"emotion={snapshot['emotion_ms']:.1f}ms delivery={snapshot['delivery_ms']:.1f}ms "
                f"log_io={snapshot['log_io_ms']:.1f}ms log_calls={snapshot['log_calls']}"
            )
        return result

    engine.play_wav = timed_play

    original_find_bridge = engine.find_bridge

    def timed_find_bridge(*args, **kwargs):
        started = time.perf_counter()
        result = original_find_bridge(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        found = bool(result and result[0])
        print(f"LATENCY BRIDGE_SCAN found={int(found)} scan={elapsed_ms:.1f}ms")
        return result

    engine.find_bridge = timed_find_bridge
    runtime_module._latency_instrumentation_installed = True
