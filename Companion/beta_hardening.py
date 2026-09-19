import os
import queue
import time
from pathlib import Path

import soundfile as sf


MAX_PENDING_SPEECH = 32
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3
MAX_CACHE_BYTES = 1536 * 1024 * 1024
MAX_CACHE_FILES = 10000
TEMP_MAX_AGE_SEC = 6 * 60 * 60


def wav_info(path):
    path = Path(path)
    if not path.exists() or path.stat().st_size < 64:
        return None
    try:
        info = sf.info(str(path))
        if info.frames > 0 and info.samplerate > 0 and info.channels > 0:
            return info
    except Exception:
        return None
    return None


def is_valid_wav(path):
    return wav_info(path) is not None


def make_atomic_synthesizer(original):
    """Wrap TTS writes so an interrupted generation never leaves a cache hit that is half a WAV."""
    def atomic(kokoro, voice, text, wav, speed=1.0, volume=1.0):
        total_started = time.perf_counter()
        wav = Path(wav)
        tmp = wav.with_name(wav.name + f".{os.getpid()}.{time.time_ns()}.tmp.wav")
        try:
            synth_started = time.perf_counter()
            result = original(kokoro, voice, text, tmp, speed, volume)
            wrapped_synth_ms = (time.perf_counter() - synth_started) * 1000.0

            validate_started = time.perf_counter()
            info = wav_info(tmp)
            validate_ms = (time.perf_counter() - validate_started) * 1000.0
            if info is None:
                raise RuntimeError("Generated WAV failed validation")

            rename_started = time.perf_counter()
            tmp.replace(wav)
            rename_ms = (time.perf_counter() - rename_started) * 1000.0
            total_ms = (time.perf_counter() - total_started) * 1000.0

            try:
                kokoro.last_atomic_timings = {
                    "wrapped_synth_ms": wrapped_synth_ms,
                    "validation_ms": validate_ms,
                    "rename_ms": rename_ms,
                    "atomic_total_ms": total_ms,
                    "audio_duration_ms": (info.frames / float(info.samplerate)) * 1000.0,
                }
            except Exception:
                pass
            print(
                "LATENCY WAV_ATOMIC "
                f"wrapped_synth={wrapped_synth_ms:.1f}ms validate={validate_ms:.1f}ms "
                f"rename={rename_ms:.1f}ms total={total_ms:.1f}ms"
            )
            return result
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
    return atomic


def make_validating_player(original):
    """Reject corrupt WAVs and expose file/audio dispatch cost separately."""
    def play(path, blocking=False):
        path = Path(path)
        validate_started = time.perf_counter()
        info = wav_info(path)
        validation_ms = (time.perf_counter() - validate_started) * 1000.0
        if info is None:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            raise RuntimeError(f"Invalid/corrupt cached WAV removed: {path.name}")

        audio_duration_ms = (info.frames / float(info.samplerate)) * 1000.0
        call_started = time.perf_counter()
        result = original(path, blocking=blocking)
        call_ms = (time.perf_counter() - call_started) * 1000.0
        excess_ms = max(0.0, call_ms - audio_duration_ms) if blocking else call_ms

        play.last_timings = {
            "validation_ms": validation_ms,
            "play_call_ms": call_ms,
            "audio_duration_ms": audio_duration_ms,
            "excess_over_audio_ms": excess_ms,
            "blocking": bool(blocking),
        }
        print(
            "LATENCY PLAYBACK_IO "
            f"validate={validation_ms:.1f}ms call={call_ms:.1f}ms "
            f"audio_duration={audio_duration_ms:.1f}ms excess={excess_ms:.1f}ms "
            f"blocking={int(bool(blocking))}"
        )
        return result

    play.last_timings = {}
    return play


def make_bounded_controller(base_cls, max_pending=MAX_PENDING_SPEECH):
    """Keep the speech backlog bounded during event floods, preserving the newest dialogue."""
    class BoundedSpeechController(base_cls):
        pending_limit = int(max_pending)

        def enqueue(self, kind, npc_guid, npc_name, text):
            dropped = 0
            while self.jobs.qsize() >= self.pending_limit:
                try:
                    self.jobs.get_nowait()
                    self.jobs.task_done()
                    dropped += 1
                except queue.Empty:
                    break
            if dropped:
                print(f"Speech backlog protection: dropped {dropped} stale queued message(s).")
                try:
                    self.state.set(queue_size=self.jobs.qsize())
                except Exception:
                    pass
            return super().enqueue(kind, npc_guid, npc_name, text)

    BoundedSpeechController.__name__ = "BoundedSpeechController"
    return BoundedSpeechController


def rotate_log(path, max_bytes=MAX_LOG_BYTES, backups=LOG_BACKUPS):
    path = Path(path)
    try:
        if not path.exists() or path.stat().st_size <= int(max_bytes):
            return False
        for index in range(int(backups), 0, -1):
            src = path.with_name(path.name + f".{index}")
            if index == int(backups):
                try:
                    src.unlink(missing_ok=True)
                except Exception:
                    pass
            elif src.exists():
                src.replace(path.with_name(path.name + f".{index + 1}"))
        path.replace(path.with_name(path.name + ".1"))
        return True
    except Exception:
        return False


def clean_stale_temps(cache_dir, max_age_sec=TEMP_MAX_AGE_SEC, now=None):
    cache_dir = Path(cache_dir)
    now = time.time() if now is None else float(now)
    removed = 0
    if not cache_dir.exists():
        return removed
    for path in cache_dir.glob("*.tmp.wav"):
        try:
            if now - path.stat().st_mtime >= float(max_age_sec):
                path.unlink()
                removed += 1
        except Exception:
            pass
    return removed


def prune_cache(cache_dir, max_bytes=MAX_CACHE_BYTES, max_files=MAX_CACHE_FILES):
    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        return 0, 0
    files = []
    total = 0
    for path in cache_dir.glob("*.wav"):
        if ".tmp.wav" in path.name:
            continue
        try:
            stat = path.stat()
            files.append((stat.st_mtime, stat.st_size, path))
            total += stat.st_size
        except Exception:
            pass

    files.sort(key=lambda item: item[0])
    removed_files = 0
    removed_bytes = 0
    while files and (len(files) > int(max_files) or total > int(max_bytes)):
        _, size, path = files.pop(0)
        try:
            path.unlink()
            removed_files += 1
            removed_bytes += size
            total -= size
        except Exception:
            pass
    return removed_files, removed_bytes


def startup_housekeeping(data_dir, cache_dir, log_file):
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    rotated = rotate_log(log_file)
    temps = clean_stale_temps(cache_dir)
    cache_files, cache_bytes = prune_cache(cache_dir)
    if rotated:
        print("Beta hardening: rotated oversized log file.")
    if temps:
        print(f"Beta hardening: removed {temps} stale temporary audio file(s).")
    if cache_files:
        print(
            f"Beta hardening: pruned {cache_files} old cache file(s) "
            f"({cache_bytes / (1024 * 1024):.1f} MiB)."
        )
