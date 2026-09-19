import hashlib
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

if sys.platform == "win32":
    import winreg
else:
    winreg = None

import companion as engine
import emotion_profiles

VERSION = "0.8.0"
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/shinraw55-ux/WoWStoryVoice/main/release.json"
WORKFLOW_URL = "https://github.com/shinraw55-ux/WoWStoryVoice/actions/workflows/build-windows.yml"
BRIDGE_STALE_SEC = 40.0
FAST_TTS_SEGMENT_CHARS = 48
CAPTURE_POLL_SEC = 0.012

DATA = engine.DATA
CACHE = engine.CACHE
SETTINGS_FILE = DATA / "settings.json"
LOG_FILE = DATA / "WoWStoryVoice.log"


class LogWriter:
    def __init__(self, path, passthrough=None):
        self.path = Path(path)
        self.passthrough = passthrough
        self.lock = threading.Lock()
        self.lines = deque(maxlen=800)
        self.partial = ""

    def write(self, text):
        if not text:
            return 0
        text = str(text)
        with self.lock:
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(text)
            except Exception:
                pass
            merged = self.partial + text
            parts = merged.split("\n")
            self.partial = parts.pop() if parts else ""
            for line in parts:
                self.lines.append(f"{time.strftime('%H:%M:%S')}  {line}")
            if self.passthrough is not None:
                try:
                    self.passthrough.write(text)
                    self.passthrough.flush()
                except Exception:
                    pass
        return len(text)

    def flush(self):
        if self.passthrough is not None:
            try:
                self.passthrough.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def snapshot(self, limit=100):
        with self.lock:
            items = list(self.lines)
            if self.partial:
                items.append(self.partial)
            return items[-limit:]


LOG = LogWriter(LOG_FILE, sys.stdout)
sys.stdout = LOG
sys.stderr = LOG


def load_settings():
    result = {"volume": 1.0, "check_updates": True, "start_with_windows": False}
    if SETTINGS_FILE.exists():
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                result.update(raw)
        except Exception as e:
            print(f"Settings load warning: {type(e).__name__}: {e}")
    try:
        result["volume"] = max(0.0, min(1.0, float(result.get("volume", 1.0))))
    except Exception:
        result["volume"] = 1.0
    result["check_updates"] = bool(result.get("check_updates", True))
    result["start_with_windows"] = bool(result.get("start_with_windows", False))
    return result


def save_settings(settings):
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)


SETTINGS = load_settings()


def startup_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{Path(__file__).resolve().parent / "app.py"}"'


def set_start_with_windows(enabled):
    SETTINGS["start_with_windows"] = bool(enabled)
    save_settings(SETTINGS)
    if sys.platform != "win32" or winreg is None:
        return
    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "WoWStoryVoice", 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, "WoWStoryVoice")
            except FileNotFoundError:
                pass


class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {
            "listening": True,
            "bridge_connected": False,
            "bridge_position": "—",
            "addon_version": "unknown",
            "version_match": None,
            "speaking": False,
            "queue_size": 0,
            "last_message": "—",
            "last_delivery": "neutral",
            "last_latency_ms": None,
            "last_error": "",
            "update_status": "Not checked",
            "update_url": WORKFLOW_URL,
        }

    def set(self, **kwargs):
        with self.lock:
            self.data.update(kwargs)

    def snapshot(self):
        with self.lock:
            return dict(self.data)


STATE = SharedState()


def synthesize_to_wav(kokoro, voice, text, wav, speed=1.0, volume=1.0):
    audio, sr = kokoro.create(text, voice=voice, speed=speed, lang="en-us")
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        raise RuntimeError("Kokoro returned an empty audio buffer")
    source_peak = float(np.max(np.abs(audio)))
    if not np.isfinite(source_peak) or source_peak <= 0.00001:
        raise RuntimeError(f"Kokoro returned silent/invalid audio (peak={source_peak})")
    volume = max(0.0, min(1.0, float(volume)))
    audio = audio * volume
    sf.write(wav, audio, sr, subtype="PCM_16")
    return sr, audio.size, source_peak * volume


@dataclass(frozen=True)
class SpeechJob:
    epoch: int
    kind: str
    npc_guid: str
    npc_name: str
    text: str
    enqueued_at: float


class SpeechController:
    def __init__(self, kokoro, available, state):
        self.kokoro = kokoro
        self.registry = engine.VoiceRegistry(available)
        self.jobs = queue.Queue()
        self.epoch = 0
        self.lock = threading.Lock()
        self.state = state
        self.shutdown_event = threading.Event()
        self.thread = threading.Thread(target=self._worker, name="WSV-Speech", daemon=True)
        self.thread.start()

    def enqueue(self, kind, npc_guid, npc_name, text):
        cleaned = engine.clean_dialogue_text(text)
        if not cleaned:
            return
        with self.lock:
            epoch = self.epoch
        self.jobs.put(SpeechJob(epoch, kind, npc_guid, npc_name, cleaned, time.perf_counter()))
        self.state.set(queue_size=self.jobs.qsize(), last_message=f"{npc_name}: {cleaned[:90]}")
        print(f"Speech queued: [{kind}] {npc_name} ({len(cleaned.encode('utf-8'))} bytes)")

    def stop_and_clear(self):
        with self.lock:
            self.epoch += 1
        while True:
            try:
                self.jobs.get_nowait()
                self.jobs.task_done()
            except queue.Empty:
                break
        engine.stop_audio()
        self.state.set(speaking=False, queue_size=0)
        print("Speech queue cleared.")

    def _is_current(self, epoch):
        with self.lock:
            return epoch == self.epoch

    def shutdown(self, timeout=2.0):
        """Stop playback and terminate the speech worker deterministically."""
        self.shutdown_event.set()
        self.stop_and_clear()
        # Wake queue.get() so the worker can observe shutdown_event.
        self.jobs.put(None)
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=max(0.0, float(timeout)))

    def _worker(self):
        while not self.shutdown_event.is_set():
            job = self.jobs.get()
            if job is None:
                self.jobs.task_done()
                break
            try:
                if not self._is_current(job.epoch):
                    continue
                queue_wait_ms = (time.perf_counter() - job.enqueued_at) * 1000.0
                voice = self.registry.voice_for(job.npc_guid, job.npc_name)
                # Shorter chunks materially reduce time-to-first-audio because
                # Kokoro can begin with a compact phrase instead of waiting for
                # a very long sentence/paragraph to finish inference.
                segments = engine.split_dialogue(job.text, limit=FAST_TTS_SEGMENT_CHARS)
                if not segments:
                    continue

                baseline = emotion_profiles.infer_emotion(job.kind, job.text, job.npc_guid)
                self.state.set(
                    speaking=True,
                    queue_size=self.jobs.qsize(),
                    last_delivery=baseline.name,
                )
                print(
                    f"Speaking: [{job.kind}] {job.npc_name}, voice={voice}, "
                    f"segments={len(segments)}, queue_wait={queue_wait_ms:.0f}ms, "
                    f"context_emotion={baseline.name}, score={baseline.score}"
                )

                first_audio = True
                for index, segment in enumerate(segments, start=1):
                    if not self._is_current(job.epoch):
                        break

                    base_speed, base_pause = engine.prosody_for_segment(job.kind, segment)
                    delivery = emotion_profiles.delivery_for_segment(
                        job.kind,
                        segment,
                        job.npc_guid,
                        base_speed,
                        base_pause,
                        baseline=baseline,
                    )
                    master_volume = SETTINGS.get("volume", 1.0)
                    volume = max(0.0, min(1.0, float(master_volume) * delivery.gain))
                    speed = delivery.speed
                    pause = delivery.pause
                    self.state.set(last_delivery=delivery.emotion)

                    engine_namespace = getattr(self.kokoro, "cache_namespace", "tts")
                    key_material = (
                        f"{engine_namespace}\0{voice}\0{delivery.emotion}\0{speed:.3f}\0{volume:.3f}\0{segment}"
                    ).encode("utf-8")
                    key = hashlib.sha256(key_material).hexdigest()
                    wav = CACHE / f"{key}.wav"
                    synth_ms = 0.0
                    if not wav.exists():
                        synth_started = time.perf_counter()
                        sr, frames, peak = synthesize_to_wav(
                            self.kokoro, voice, segment, wav, speed, volume
                        )
                        synth_ms = (time.perf_counter() - synth_started) * 1000.0
                        cue_text = ",".join(delivery.cues[:4]) or "none"
                        print(
                            f"TTS generated: [{job.kind}] {job.npc_name}, segment={index}/{len(segments)}, "
                            f"emotion={delivery.emotion}, score={delivery.score}, cues={cue_text}, "
                            f"speed={speed:.2f}, gain={delivery.gain:.2f}, volume={volume:.2f}, "
                            f"tts={synth_ms:.0f}ms, {frames} frames @ {sr} Hz, peak {peak:.3f}"
                        )
                    else:
                        print(
                            f"TTS cache: [{job.kind}] {job.npc_name}, segment={index}/{len(segments)}, "
                            f"emotion={delivery.emotion}, speed={speed:.2f}, gain={delivery.gain:.2f}"
                        )

                    if not self._is_current(job.epoch):
                        break
                    if first_audio:
                        total_ms = (time.perf_counter() - job.enqueued_at) * 1000.0
                        self.state.set(last_latency_ms=round(total_ms))
                        print(
                            f"AUDIO START: {job.npc_name} total_from_queue={total_ms:.0f}ms "
                            f"(queue={queue_wait_ms:.0f}ms, first_tts={synth_ms:.0f}ms)"
                        )
                        first_audio = False
                    engine.play_wav(wav, blocking=True)
                    if pause > 0 and self._is_current(job.epoch):
                        time.sleep(pause)
            except Exception as e:
                self.state.set(last_error=f"Speech: {type(e).__name__}: {e}")
                print(f"Speech worker error: {type(e).__name__}: {e}")
            finally:
                self.state.set(speaking=False, queue_size=self.jobs.qsize())
                self.jobs.task_done()


def handle_control(text, speech, state):
    normalized = text.strip()
    low = normalized.casefold()
    if low == "stop":
        speech.stop_and_clear()
    elif low.startswith("hello|"):
        addon_version = normalized.split("|", 1)[1].strip() or "unknown"
        state.set(addon_version=addon_version, version_match=(addon_version == VERSION))
        if addon_version != VERSION:
            print(f"VERSION MISMATCH: addon={addon_version}, companion={VERSION}")
    else:
        print(f"Unknown control message: {normalized}")


def capture_loop(speech, state, stop_event, listening_event):
    reassembler = engine.Reassembler()
    bridge_pos = None
    bridge_failures = 0
    last_search = 0.0
    last_diag = 0.0
    best_diag = None
    last_chunk_signature = None
    last_valid = 0.0

    with engine.mss.MSS() as sct:
        while not stop_event.is_set():
            if not listening_event.is_set():
                bridge_pos = None
                state.set(listening=False, bridge_connected=False, bridge_position="—")
                time.sleep(0.25)
                continue
            state.set(listening=True)
            try:
                raw = None
                decoded = None
                if bridge_pos:
                    left, top, cell_size = bridge_pos
                    try:
                        raw = engine.sample_packet_at(sct, left, top, cell_size)
                        decoded = engine.decode_chunk(raw)
                    except Exception:
                        decoded = None
                    if decoded:
                        bridge_failures = 0
                    else:
                        bridge_failures += 1
                        if bridge_failures >= 8:
                            bridge_pos = None
                            bridge_failures = 0

                now = time.time()
                if not bridge_pos and now - last_search >= engine.SEARCH_INTERVAL:
                    last_search = now
                    bridge_pos, raw, best_diag = engine.find_bridge(sct)
                    if bridge_pos:
                        decoded = engine.decode_chunk(raw)
                        print(f"Bridge detected at {bridge_pos[:2]}, cell size {bridge_pos[2]} px.")
                if not bridge_pos and now - last_diag >= engine.DIAG_INTERVAL:
                    last_diag = now
                    print("Waiting for bridge.", engine.diagnostic_bridge(best_diag))

                if decoded:
                    last_valid = now
                    state.set(
                        bridge_connected=True,
                        bridge_position=f"{bridge_pos[:2]} / {bridge_pos[2]} px" if bridge_pos else "detected",
                    )
                    signature = decoded[:3] + (hashlib.sha1(decoded[3]).digest()[:4],)
                    if signature != last_chunk_signature:
                        last_chunk_signature = signature
                        message = reassembler.accept(decoded)
                        if message:
                            kind, npc_guid, npc_name, text = message
                            if kind == "control":
                                handle_control(text, speech, state)
                            elif text.strip():
                                speech.enqueue(kind, npc_guid, npc_name, text)
                elif last_valid and now - last_valid > BRIDGE_STALE_SEC:
                    state.set(bridge_connected=False, bridge_position="—")
            except Exception as e:
                state.set(last_error=f"Bridge: {type(e).__name__}: {e}")
                print(f"Bridge read error: {type(e).__name__}: {e}")
                time.sleep(0.5)
            time.sleep(CAPTURE_POLL_SEC)


def version_tuple(value):
    nums = re.findall(r"\d+", str(value))
    return tuple(int(x) for x in (nums[:3] + ["0", "0", "0"])[:3])


def check_for_updates_async(state):
    def worker():
        state.set(update_status="Checking…")
        try:
            req = urllib.request.Request(UPDATE_MANIFEST_URL, headers={"User-Agent": f"WoWStoryVoice/{VERSION}"})
            with urllib.request.urlopen(req, timeout=6) as response:
                manifest = json.loads(response.read().decode("utf-8"))
            latest = str(manifest.get("version", "0"))
            page = str(manifest.get("download_page", WORKFLOW_URL))
            if version_tuple(latest) > version_tuple(VERSION):
                state.set(update_status=f"Update available: v{latest}", update_url=page)
                print(f"Update available: v{latest}")
            else:
                state.set(update_status=f"Up to date (v{VERSION})", update_url=page)
        except Exception as e:
            state.set(update_status="Update check failed", last_error=f"Update: {type(e).__name__}: {e}")
            print(f"Update check failed: {type(e).__name__}: {e}")
    threading.Thread(target=worker, name="WSV-UpdateCheck", daemon=True).start()


def app_dir():
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def locate_addon_zip():
    root = app_dir()
    for path in (root / "WoWStoryVoice-Addon.zip", root.parent / "WoWStoryVoice-Addon.zip", Path.cwd() / "WoWStoryVoice-Addon.zip"):
        if path.exists():
            return path
    return None


def candidate_addons_dirs():
    roots = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            roots.append(Path(base) / "World of Warcraft" / "_retail_" / "Interface" / "AddOns")
    roots.extend([
        Path("C:/Games/World of Warcraft/_retail_/Interface/AddOns"),
        Path("D:/Games/World of Warcraft/_retail_/Interface/AddOns"),
    ])
    return [p for p in roots if p.exists()]


def normalize_addons_dir(selected):
    p = Path(selected)
    name = p.name.casefold()
    if name == "addons":
        return p
    if name == "interface":
        return p / "AddOns"
    if name == "_retail_":
        return p / "Interface" / "AddOns"
    if (p / "_retail_").exists():
        return p / "_retail_" / "Interface" / "AddOns"
    return p


def install_addon_to(addons_dir):
    addon_zip = locate_addon_zip()
    if addon_zip is None:
        raise FileNotFoundError("WoWStoryVoice-Addon.zip was not found beside the companion.")
    addons_dir = Path(addons_dir)
    addons_dir.mkdir(parents=True, exist_ok=True)
    temp_root = addons_dir / ".WoWStoryVoice-installing"
    target = addons_dir / "WoWStoryVoice"
    backup = addons_dir / ".WoWStoryVoice-backup"
    shutil.rmtree(temp_root, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    with zipfile.ZipFile(addon_zip, "r") as zf:
        members = zf.infolist()
        names = [info.filename.replace("\\", "/") for info in members]
        if not any(name.startswith("WoWStoryVoice/") for name in names):
            raise RuntimeError("Addon ZIP has an unexpected structure.")
        root = temp_root.resolve()
        for info, name in zip(members, names):
            parts = [part for part in name.split("/") if part not in ("", ".")]
            if (
                not parts
                or parts[0] != "WoWStoryVoice"
                or ".." in parts
                or name.startswith("/")
                or (len(name) >= 2 and name[1] == ":")
            ):
                raise RuntimeError(f"Unsafe addon ZIP entry rejected: {name}")
            destination = (temp_root / Path(*parts)).resolve()
            if os.path.commonpath((str(root), str(destination))) != str(root):
                raise RuntimeError(f"Unsafe addon ZIP path rejected: {name}")
        zf.extractall(temp_root)
    extracted = temp_root / "WoWStoryVoice"
    if not (extracted / "WoWStoryVoice.toc").exists():
        raise RuntimeError("Extracted addon is missing WoWStoryVoice.toc.")
    try:
        if target.exists():
            target.replace(backup)
        extracted.replace(target)
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    return target


def open_path(path):
    if sys.platform == "win32":
        os.startfile(str(path))
    else:
        webbrowser.open(Path(path).as_uri())


def initialize_tts():
    print(f"WoW Story Voice v{VERSION}")
    print("Transport: WSV6 binary RGB + chunk reassembly")
    print("Speech: persistent NPC identity + context-aware emotion delivery")
    print("Preparing local TTS...")
    engine.ensure_models()
    kokoro = engine.Kokoro(str(engine.MODEL), str(engine.VOICES))
    available = kokoro.get_voices()
    try:
        providers = kokoro.sess.get_providers()
    except Exception:
        providers = []
    print(f"Kokoro ready. {len(available)} voices available. Providers: {providers or ['unknown']}")

    # Warm the inference path before capture starts. The first ONNX invocation
    # is commonly slower than later calls; paying that cost on the splash screen
    # prevents the first NPC line from absorbing the cold-start penalty.
    if available:
        warm_voice = available[0]
        started = time.perf_counter()
        try:
            audio, _ = kokoro.create("Ready.", voice=warm_voice, speed=1.0, lang="en-us")
            frames = int(np.asarray(audio).size)
            print(f"TTS warm-up complete in {(time.perf_counter() - started) * 1000.0:.0f}ms ({frames} frames).")
        except Exception as e:
            print(f"TTS warm-up warning: {type(e).__name__}: {e}")
    return kokoro, available