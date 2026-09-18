import ctypes
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import mss
import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

if sys.platform == "win32":
    import winsound
else:
    winsound = None

VERSION = "0.7.0"
MAGIC = b"WSV6"

# WSV6 keeps the live-verified binary RGB transport from v0.5/v0.6.
CELLS_PER_BYTE = 3
MAX_PACKET = 103
CHUNK_DATA_MAX = 91
MAX_CELLS = MAX_PACKET * CELLS_PER_BYTE

CELL_SIZES = (3, 4, 5, 6, 7)
SEARCH_HEIGHT = 420
SEARCH_WIDTH = 2200
SEARCH_INTERVAL = 1.5
DIAG_INTERVAL = 8.0
BIT_THRESHOLD = 128
ASSEMBLY_TIMEOUT = 15.0
COMPLETED_ID_TTL = 30.0
CONTENT_DEDUPE_TTL = 8.0
MAX_TTS_SEGMENT_CHARS = 360

MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin"
PREFERRED = ["af_heart", "af_bella", "af_nicole", "af_sarah", "am_adam", "am_michael"]


def _byte_to_cells(value):
    bits = [((value >> shift) & 1) for shift in range(7, -1, -1)]
    bits.append(0)
    return [bits[i:i + 3] for i in range(0, 9, 3)]


HEADER_BITS = np.array(
    [cell for value in MAGIC for cell in _byte_to_cells(value)],
    dtype=np.uint8,
)


def data_dir():
    root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    p = root / "WoWStoryVoice"
    p.mkdir(parents=True, exist_ok=True)
    return p


DATA = data_dir()
CACHE = DATA / "cache"
CACHE.mkdir(exist_ok=True)
MODEL = Path(os.environ.get("WSV_MODEL", DATA / "kokoro-v1.0.onnx"))
VOICES = Path(os.environ.get("WSV_VOICES", DATA / "voices-v1.0.bin"))
VOICE_MAP = DATA / "voice-map.json"
SELF_TEST_MARKER = DATA / f"audio-self-test-ok-v{VERSION}.txt"


def download(url, dest, label):
    if dest.exists() and dest.stat().st_size > 1024 * 1024:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"{label} is missing. Downloading it once...")
    print(f"Destination: {dest}")
    try:
        def progress(blocks, block_size, total):
            if total > 0:
                pct = min(100, blocks * block_size * 100 // total)
                print(f"\r{label}: {pct:3d}%", end="", flush=True)

        urllib.request.urlretrieve(url, tmp, reporthook=progress)
        print()
        if tmp.stat().st_size <= 1024 * 1024:
            raise RuntimeError(f"{label} download is unexpectedly small")
        tmp.replace(dest)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def ensure_models():
    download(MODEL_URL, MODEL, "Kokoro voice model")
    download(VOICES_URL, VOICES, "Kokoro voice library")


def _u16(raw, offset):
    return (raw[offset] << 8) | raw[offset + 1]


def decode_chunk(raw):
    if raw is None or len(raw) < 12 or raw[:4] != MAGIC:
        return None

    msg_id = _u16(raw, 4)
    chunk_index = _u16(raw, 6)
    chunk_total = _u16(raw, 8)
    chunk_len = raw[10]

    if chunk_total < 1 or chunk_index < 1 or chunk_index > chunk_total:
        return None
    if chunk_len > CHUNK_DATA_MAX:
        return None

    data_end = 11 + chunk_len
    checksum_index = data_end
    if checksum_index >= len(raw):
        return None

    body = raw[4:data_end]
    if sum(body) % 256 != raw[checksum_index]:
        return None

    return msg_id, chunk_index, chunk_total, raw[11:data_end]


def _decode_cell_bits(img, cell_index, cell_size):
    x0 = cell_index * cell_size
    margin = 1 if cell_size >= 4 else 0
    patch = img[
        margin:max(margin + 1, cell_size - margin),
        x0 + margin:max(x0 + margin + 1, x0 + cell_size - margin),
        :3,
    ]
    if patch.size == 0:
        raise ValueError("empty bridge cell")

    med_bgr = np.median(patch, axis=(0, 1))
    r, g, b = float(med_bgr[2]), float(med_bgr[1]), float(med_bgr[0])
    return (
        1 if r >= BIT_THRESHOLD else 0,
        1 if g >= BIT_THRESHOLD else 0,
        1 if b >= BIT_THRESHOLD else 0,
    )


def sample_packet_at(sct, left, top, cell_size):
    width = MAX_CELLS * cell_size
    img = np.array(sct.grab({
        "left": int(left),
        "top": int(top),
        "width": int(width),
        "height": int(cell_size),
    }))

    out = bytearray()
    for byte_index in range(MAX_PACKET):
        bits = []
        base = byte_index * CELLS_PER_BYTE
        for cell_offset in range(CELLS_PER_BYTE):
            bits.extend(_decode_cell_bits(img, base + cell_offset, cell_size))

        value = 0
        for bit in bits[:8]:
            value = (value << 1) | bit
        out.append(value)

    return bytes(out)


def _monitor_list(sct):
    monitors = list(sct.monitors[1:])
    if not monitors:
        monitors = [sct.monitors[0]]
    return monitors


def _scan_monitor(sct, mon):
    width = min(int(mon["width"]), SEARCH_WIDTH)
    height = min(int(mon["height"]), SEARCH_HEIGHT)
    if width < 200 or height < 20:
        return None, None, None

    shot = np.array(sct.grab({
        "left": int(mon["left"]),
        "top": int(mon["top"]),
        "width": width,
        "height": height,
    }))

    rgb_bits = (shot[:, :, [2, 1, 0]] >= BIT_THRESHOLD).astype(np.uint8)
    best_score = None
    best_hint = None

    for cell_size in CELL_SIZES:
        center = cell_size // 2
        offsets = np.arange(len(HEADER_BITS), dtype=np.int32) * cell_size + center
        max_start = width - int(offsets[-1]) - 1
        if max_start <= 0:
            continue

        score = np.zeros((height, max_start), dtype=np.uint8)
        for off, target_bits in zip(offsets, HEADER_BITS):
            sample = rgb_bits[:, off:off + max_start, :]
            score += np.sum(sample != target_bits, axis=2).astype(np.uint8)

        flat = score.ravel()
        candidate_count = min(64, flat.size)
        if candidate_count <= 0:
            continue
        idxs = np.argpartition(flat, candidate_count - 1)[:candidate_count]
        idxs = idxs[np.argsort(flat[idxs])]

        for idx in idxs:
            y, x = np.unravel_index(int(idx), score.shape)
            this_score = int(score[y, x])
            if best_score is None or this_score < best_score:
                best_score = this_score
                best_hint = (
                    int(mon["left"]) + int(x),
                    int(mon["top"]) + int(y),
                    cell_size,
                )

            if this_score > 4:
                break

            for delta_y in range(cell_size):
                top_local = int(y) - delta_y
                if top_local < 0 or top_local + cell_size > height:
                    continue

                left = int(mon["left"]) + int(x)
                top = int(mon["top"]) + top_local
                try:
                    raw = sample_packet_at(sct, left, top, cell_size)
                except Exception:
                    continue

                if decode_chunk(raw):
                    return (left, top, cell_size), raw, (best_score, best_hint)

    return None, None, (best_score, best_hint)


def find_bridge(sct):
    best = None
    for mon in _monitor_list(sct):
        pos, raw, diag = _scan_monitor(sct, mon)
        if raw:
            return pos, raw, diag
        if diag and diag[0] is not None and (best is None or diag[0] < best[0]):
            best = diag
    return None, None, best


def diagnostic_bridge(best):
    if not best or best[0] is None:
        return "no bridge-like header found"
    score, hint = best
    return f"best WSV6 header score={score}/36 near {hint}"


def minimize_console():
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 6)
    except Exception as e:
        print(f"Console auto-minimize failed: {type(e).__name__}: {e}")


def play_wav(path, blocking=False):
    if winsound is None:
        raise RuntimeError("Windows native audio backend is unavailable")
    flags = winsound.SND_FILENAME | winsound.SND_NODEFAULT
    if not blocking:
        flags |= winsound.SND_ASYNC
    winsound.PlaySound(str(path), flags)


def stop_audio():
    if winsound is not None:
        winsound.PlaySound(None, 0)


def clean_dialogue_text(text):
    """Remove WoW UI markup without rewriting the actual dialogue."""
    if not text:
        return ""
    text = str(text)
    text = re.sub(r"\|H[^|]*\|h(.*?)\|h", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\|T[^|]*\|t", " ", text)
    text = re.sub(r"\|A[^|]*\|a", " ", text)
    text = re.sub(r"\|c[0-9A-Fa-f]{8}", "", text)
    text = text.replace("|r", "")
    text = re.sub(r"\{(?:rt\d+|star|circle|diamond|triangle|moon|square|cross|skull)\}", " ", text, flags=re.IGNORECASE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\t\f\v]+", " ", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_long_piece(piece, limit):
    piece = piece.strip()
    if not piece:
        return []
    if len(piece) <= limit:
        return [piece]

    result = []
    remaining = piece
    while len(remaining) > limit:
        window = remaining[: limit + 1]
        cut = max(window.rfind("; "), window.rfind(": "), window.rfind(", "), window.rfind(" "))
        if cut < max(40, limit // 2):
            cut = limit
        else:
            cut += 1
        result.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        result.append(remaining)
    return result


def split_dialogue(text, limit=MAX_TTS_SEGMENT_CHARS):
    """Split long speech at paragraph/sentence boundaries for stable TTS pacing."""
    cleaned = clean_dialogue_text(text)
    if not cleaned:
        return []

    segments = []
    paragraphs = re.split(r"\n+", cleaned)
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        pieces = re.split(r"(?<=[.!?…])\s+(?=[\"'“‘(\[]?[A-Z0-9À-ÖØ-Þ])", paragraph)
        for piece in pieces:
            segments.extend(_split_long_piece(piece, limit))
    return segments


def prosody_for_segment(kind, text):
    """Conservative pacing hints; punctuation remains the main prosody signal."""
    kind = (kind or "").casefold()
    speed = 1.0
    pause = 0.08
    stripped = text.rstrip()

    if kind == "monster_yell":
        speed = 1.03
    elif kind in ("quest", "reward", "progress", "greeting"):
        speed = 0.98

    if stripped.endswith("...") or stripped.endswith("…"):
        pause = 0.20
        speed = min(speed, 0.96)
    elif stripped.endswith("?"):
        pause = 0.12
        speed = min(speed, 0.98)
    elif stripped.endswith("!"):
        pause = 0.10
    elif stripped.endswith((".", ":", ";")):
        pause = 0.10

    return round(speed, 3), pause


def synthesize_to_wav(kokoro, voice, text, wav, speed=1.0):
    audio, sr = kokoro.create(text, voice=voice, speed=speed, lang="en-us")
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        raise RuntimeError("Kokoro returned an empty audio buffer")
    peak = float(np.max(np.abs(audio)))
    if not np.isfinite(peak) or peak <= 0.00001:
        raise RuntimeError(f"Kokoro returned silent/invalid audio (peak={peak})")
    sf.write(wav, audio, sr, subtype="PCM_16")
    return sr, audio.size, peak


def _voice_identity(npc_guid, npc_name):
    if npc_guid:
        parts = npc_guid.split("-")
        if len(parts) >= 6 and parts[0] in ("Creature", "Vehicle"):
            return f"npc:{parts[5]}"
        return f"guid:{npc_guid}"
    return f"name:{(npc_name or 'Narrator').strip().casefold()}"


def dialogue_dedupe_key(npc_guid, npc_name, text):
    identity = _voice_identity(npc_guid, npc_name)
    normalized = re.sub(r"\s+", " ", clean_dialogue_text(text)).strip().casefold()
    return hashlib.sha256((identity + "\0" + normalized).encode("utf-8")).hexdigest()


class VoiceRegistry:
    def __init__(self, available):
        self.available = list(available)
        self.pool = [v for v in PREFERRED if v in self.available] or self.available
        if not self.pool:
            raise RuntimeError("No Kokoro voices available")
        self.mapping = {}
        self._load()

    def _load(self):
        if not VOICE_MAP.exists():
            return
        try:
            raw = json.loads(VOICE_MAP.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.mapping = {
                    str(k): str(v)
                    for k, v in raw.items()
                    if isinstance(v, str) and v in self.available
                }
        except Exception as e:
            print(f"Voice map load warning: {type(e).__name__}: {e}")

    def _save(self):
        tmp = VOICE_MAP.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.mapping, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(VOICE_MAP)

    def voice_for(self, npc_guid, npc_name):
        identity = _voice_identity(npc_guid, npc_name)
        voice = self.mapping.get(identity)
        if voice in self.available:
            return voice

        digest = hashlib.sha256(identity.encode("utf-8")).digest()
        voice = self.pool[int.from_bytes(digest[:8], "big") % len(self.pool)]
        self.mapping[identity] = voice
        try:
            self._save()
        except Exception as e:
            print(f"Voice map save warning: {type(e).__name__}: {e}")
        return voice


def audio_self_test(kokoro, available):
    registry = VoiceRegistry(available)
    voice = registry.voice_for("", "Narrator")
    wav = CACHE / f"audio-self-test-v{VERSION}.wav"
    print("Audio self-test: generating speech...")
    if not wav.exists():
        sr, frames, peak = synthesize_to_wav(
            kokoro,
            voice,
            "WoW Story Voice audio test. Local speech and Windows audio are working.",
            wav,
            speed=1.0,
        )
        print(f"Audio self-test generated: {frames} frames @ {sr} Hz, peak {peak:.3f}")
    else:
        data, sr = sf.read(wav, dtype="float32")
        peak = float(np.max(np.abs(data))) if len(data) else 0.0
        print(f"Audio self-test cache: {len(data)} frames @ {sr} Hz, peak {peak:.3f}")

    print("Audio self-test: playing through Windows default output...")
    play_wav(wav, blocking=True)
    SELF_TEST_MARKER.write_text("ok\n", encoding="ascii")
    print("Audio self-test finished.")


@dataclass(frozen=True)
class SpeechJob:
    epoch: int
    kind: str
    npc_guid: str
    npc_name: str
    text: str


class SpeechController:
    def __init__(self, kokoro, available):
        self.kokoro = kokoro
        self.registry = VoiceRegistry(available)
        self.jobs = queue.Queue()
        self.epoch = 0
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._worker, name="WSV-Speech", daemon=True)
        self.thread.start()

    def enqueue(self, kind, npc_guid, npc_name, text):
        cleaned = clean_dialogue_text(text)
        if not cleaned:
            return
        with self.lock:
            epoch = self.epoch
        self.jobs.put(SpeechJob(epoch, kind, npc_guid, npc_name, cleaned))
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
        stop_audio()
        print("Speech queue cleared.")

    def _is_current(self, epoch):
        with self.lock:
            return epoch == self.epoch

    def _worker(self):
        while True:
            job = self.jobs.get()
            try:
                if not self._is_current(job.epoch):
                    continue

                voice = self.registry.voice_for(job.npc_guid, job.npc_name)
                segments = split_dialogue(job.text)
                if not segments:
                    continue

                print(
                    f"Speaking: [{job.kind}] {job.npc_name}, voice={voice}, "
                    f"segments={len(segments)}"
                )

                for index, segment in enumerate(segments, start=1):
                    if not self._is_current(job.epoch):
                        break

                    speed, pause = prosody_for_segment(job.kind, segment)
                    key_material = f"{voice}\0{speed:.3f}\0{segment}".encode("utf-8")
                    wav = CACHE / f"{hashlib.sha256(key_material).hexdigest()}.wav"

                    if not wav.exists():
                        sr, frames, peak = synthesize_to_wav(
                            self.kokoro, voice, segment, wav, speed=speed
                        )
                        print(
                            f"TTS generated: [{job.kind}] {job.npc_name}, "
                            f"segment={index}/{len(segments)}, speed={speed:.2f}, "
                            f"{frames} frames @ {sr} Hz, peak {peak:.3f}"
                        )

                    if not self._is_current(job.epoch):
                        break

                    play_wav(wav, blocking=True)
                    if pause > 0 and self._is_current(job.epoch):
                        time.sleep(pause)
            except Exception as e:
                print(f"Speech worker error: {type(e).__name__}: {e}")
            finally:
                self.jobs.task_done()


class Reassembler:
    def __init__(self):
        self.messages = {}
        self.completed_ids = {}
        self.recent_content = {}

    def _cleanup(self, now):
        expired = [mid for mid, m in self.messages.items() if now - m["updated"] > ASSEMBLY_TIMEOUT]
        for mid in expired:
            missing = self.messages[mid]["total"] - len(self.messages[mid]["chunks"])
            print(f"Chunk assembly timeout: message {mid}, missing {missing} chunk(s).")
            del self.messages[mid]

        self.completed_ids = {
            mid: ts for mid, ts in self.completed_ids.items()
            if now - ts <= COMPLETED_ID_TTL
        }
        self.recent_content = {
            digest: ts for digest, ts in self.recent_content.items()
            if now - ts <= CONTENT_DEDUPE_TTL
        }

    def accept(self, decoded):
        if not decoded:
            return None

        now = time.time()
        self._cleanup(now)
        msg_id, chunk_index, chunk_total, data = decoded

        if msg_id in self.completed_ids:
            return None

        state = self.messages.get(msg_id)
        if state is None or state["total"] != chunk_total:
            state = {
                "total": chunk_total,
                "chunks": {},
                "created": now,
                "updated": now,
            }
            self.messages[msg_id] = state

        state["updated"] = now
        state["chunks"][chunk_index] = bytes(data)

        if len(state["chunks"]) != state["total"]:
            return None

        try:
            payload = b"".join(state["chunks"][i] for i in range(1, state["total"] + 1))
        except KeyError:
            return None

        del self.messages[msg_id]
        self.completed_ids[msg_id] = now

        try:
            kind, npc_guid, npc_name, text = payload.decode("utf-8").split("\x1f", 3)
        except (UnicodeDecodeError, ValueError):
            print(f"Invalid reassembled payload: message {msg_id}")
            return None

        cleaned = clean_dialogue_text(text)
        if not cleaned and kind != "control":
            return None

        digest = dialogue_dedupe_key(npc_guid, npc_name, cleaned)
        previous = self.recent_content.get(digest)
        self.recent_content[digest] = now
        if kind != "control" and previous is not None and now - previous <= CONTENT_DEDUPE_TTL:
            print(f"Duplicate dialogue suppressed: message {msg_id}")
            return None

        print(
            f"Message reassembled: id={msg_id}, chunks={chunk_total}, "
            f"kind={kind}, npc={npc_name}, text_bytes={len(cleaned.encode('utf-8'))}"
        )
        return kind, npc_guid, npc_name, cleaned


def main():
    print(f"WoW Story Voice v{VERSION}")
    print("Transport: WSV6 binary RGB + chunk reassembly")
    print("Speech: persistent NPC voices + queued segmented prosody")
    print("First launch downloads the local Kokoro model. No Python installation is required.")
    ensure_models()

    print("Loading local TTS...")
    kokoro = Kokoro(str(MODEL), str(VOICES))
    available = kokoro.get_voices()
    print(f"Kokoro ready. {len(available)} voices available.")

    if not SELF_TEST_MARKER.exists():
        try:
            audio_self_test(kokoro, available)
        except Exception as e:
            print(f"AUDIO SELF-TEST FAILED: {type(e).__name__}: {e}")
    else:
        print("Audio self-test already passed for this version; skipping startup playback.")

    speech = SpeechController(kokoro, available)
    reassembler = Reassembler()

    print("Ready. The console will minimize so it cannot cover the WoW bridge.")
    print("Use /wsv test in WoW. Use /wsv stop to stop and clear queued speech.")
    time.sleep(1.0)
    minimize_console()

    bridge_pos = None
    bridge_failures = 0
    last_search = 0.0
    last_diag = 0.0
    best_diag = None
    last_chunk_signature = None

    with mss.MSS() as sct:
        while True:
            try:
                raw = None
                decoded = None

                if bridge_pos:
                    left, top, cell_size = bridge_pos
                    try:
                        raw = sample_packet_at(sct, left, top, cell_size)
                        decoded = decode_chunk(raw)
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
                if not bridge_pos and now - last_search >= SEARCH_INTERVAL:
                    last_search = now
                    bridge_pos, raw, best_diag = find_bridge(sct)
                    if bridge_pos:
                        decoded = decode_chunk(raw)
                        print(
                            f"Bridge detected at screen position {bridge_pos[:2]}, "
                            f"cell size {bridge_pos[2]} px."
                        )

                if not bridge_pos and now - last_diag >= DIAG_INTERVAL:
                    last_diag = now
                    print("Waiting for bridge.", diagnostic_bridge(best_diag))

                if decoded:
                    signature = decoded[:3] + (hashlib.sha1(decoded[3]).digest()[:4],)
                    if signature != last_chunk_signature:
                        last_chunk_signature = signature
                        message = reassembler.accept(decoded)
                        if message:
                            kind, npc_guid, npc_name, text = message
                            if kind == "control" and text.strip().casefold() == "stop":
                                speech.stop_and_clear()
                            elif text.strip():
                                speech.enqueue(kind, npc_guid, npc_name, text)

            except Exception as e:
                print(f"Bridge read error: {type(e).__name__}: {e}")
                time.sleep(0.5)

            time.sleep(0.04)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\nWoW Story Voice stopped with an error:")
        print(f"{type(e).__name__}: {e}")
        input("\nPress Enter to close...")
        sys.exit(1)
