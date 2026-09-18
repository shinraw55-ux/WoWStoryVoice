import hashlib
import os
import sys
import time
import urllib.request
from pathlib import Path

import mss
import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

if sys.platform == "win32":
    import winsound
else:
    winsound = None

VERSION = "0.4.2"
MAGIC = b"WSV4"
PIXEL_SIZE = 5
X0, Y0 = 20, 20
MAX_PACKET = 103
SEARCH_HEIGHT = 350
SEARCH_WIDTH = 1800
CELL_SIZES = (4, 5, 6, 7)
SEARCH_INTERVAL = 2.0
DIAG_INTERVAL = 5.0

MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin"
PREFERRED = ["af_heart", "af_bella", "af_nicole", "af_sarah", "am_adam", "am_michael"]

HEADER_LEVELS = []
for _b in MAGIC:
    HEADER_LEVELS.extend([((_b >> 4) & 0x0F) * 17, (_b & 0x0F) * 17])
HEADER_LEVELS = np.array(HEADER_LEVELS, dtype=np.float32)


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


def decode(raw):
    if raw is None or len(raw) < 7 or raw[:4] != MAGIC:
        return None
    seq, ln = raw[4], raw[5]
    if ln > 96 or 6 + ln >= len(raw):
        return None
    payload = raw[6:6 + ln]
    if sum(payload) % 256 != raw[6 + ln]:
        return None
    try:
        kind, npc, text = payload.decode("utf-8").split("\x1f", 2)
    except (ValueError, UnicodeDecodeError):
        return None
    return seq, kind, npc, text


def _cell_level(img, cell_index, cell_size):
    x0 = cell_index * cell_size
    margin = 1 if cell_size >= 4 else 0
    patch = img[
        margin:max(margin + 1, cell_size - margin),
        x0 + margin:max(x0 + margin + 1, x0 + cell_size - margin),
        :3,
    ]
    gray = float(np.median(patch))
    return max(0, min(15, int(round(gray / 17.0))))


def sample_packet_at(sct, left, top, cell_size=PIXEL_SIZE):
    cell_count = MAX_PACKET * 2
    width = cell_count * cell_size
    img = np.array(sct.grab({"left": int(left), "top": int(top), "width": int(width), "height": int(cell_size)}))
    out = bytearray()
    for i in range(MAX_PACKET):
        hi = _cell_level(img, i * 2, cell_size)
        lo = _cell_level(img, i * 2 + 1, cell_size)
        out.append((hi << 4) | lo)
    return bytes(out)


def _monitor_list(sct):
    monitors = list(sct.monitors[1:])
    if not monitors:
        monitors = [sct.monitors[0]]
    return monitors


def _try_expected_positions(sct):
    for mon in _monitor_list(sct):
        for cell_size in CELL_SIZES:
            left = mon["left"] + X0
            top = mon["top"] + Y0
            try:
                raw = sample_packet_at(sct, left, top, cell_size)
            except Exception:
                continue
            if decode(raw):
                return (left, top, cell_size), raw
    return None, None


def _scan_monitor(sct, mon):
    width = min(int(mon["width"]), SEARCH_WIDTH)
    height = min(int(mon["height"]), SEARCH_HEIGHT)
    if width < 100 or height < 20:
        return None, None

    shot = np.array(sct.grab({
        "left": int(mon["left"]),
        "top": int(mon["top"]),
        "width": width,
        "height": height,
    }))
    gray = shot[:, :, :3].mean(axis=2).astype(np.float32)

    for cell_size in CELL_SIZES:
        center = cell_size // 2
        offsets = np.arange(len(HEADER_LEVELS), dtype=np.int32) * cell_size + center
        max_start = width - int(offsets[-1]) - 1
        if max_start <= 0:
            continue

        score = np.zeros((height, max_start), dtype=np.float32)
        for off, target in zip(offsets, HEADER_LEVELS):
            score += np.abs(gray[:, off:off + max_start] - target)

        flat = score.ravel()
        candidate_count = min(16, flat.size)
        if candidate_count <= 0:
            continue
        idxs = np.argpartition(flat, candidate_count - 1)[:candidate_count]
        idxs = idxs[np.argsort(flat[idxs])]

        for idx in idxs:
            y, x = np.unravel_index(int(idx), score.shape)
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
                if decode(raw):
                    return (left, top, cell_size), raw

    return None, None


def find_bridge(sct):
    pos, raw = _try_expected_positions(sct)
    if raw:
        return pos, raw

    for mon in _monitor_list(sct):
        pos, raw = _scan_monitor(sct, mon)
        if raw:
            return pos, raw
    return None, None


def diagnostic_header(sct):
    lines = []
    for index, mon in enumerate(_monitor_list(sct), start=1):
        left = mon["left"] + X0
        top = mon["top"] + Y0
        try:
            raw = sample_packet_at(sct, left, top, PIXEL_SIZE)
            lines.append(f"monitor {index} @ ({left},{top}): {raw[:8].hex(' ')}")
        except Exception as e:
            lines.append(f"monitor {index}: capture failed: {e}")
    return " | ".join(lines)


def choose_voice(npc, available):
    pool = [v for v in PREFERRED if v in available] or list(available)
    if not pool:
        raise RuntimeError("No Kokoro voices available")
    return pool[int(hashlib.sha256(npc.encode("utf-8")).hexdigest(), 16) % len(pool)]


def play_wav(path, blocking=False):
    if winsound is None:
        raise RuntimeError("Windows native audio backend is unavailable")
    flags = winsound.SND_FILENAME | winsound.SND_NODEFAULT
    if not blocking:
        flags |= winsound.SND_ASYNC
    winsound.PlaySound(str(path), flags)


def synthesize_to_wav(kokoro, voice, text, wav):
    audio, sr = kokoro.create(text, voice=voice, speed=1.0, lang="en-us")
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        raise RuntimeError("Kokoro returned an empty audio buffer")
    peak = float(np.max(np.abs(audio)))
    if not np.isfinite(peak) or peak <= 0.00001:
        raise RuntimeError(f"Kokoro returned silent/invalid audio (peak={peak})")
    sf.write(wav, audio, sr, subtype="PCM_16")
    return sr, audio.size, peak


def audio_self_test(kokoro, available):
    voice = choose_voice("Narrator", available)
    wav = CACHE / "audio-self-test-v0.4.2.wav"
    print("Audio self-test: generating speech...")
    if not wav.exists():
        sr, frames, peak = synthesize_to_wav(
            kokoro,
            voice,
            "WoW Story Voice audio test. If you can hear this, local speech and Windows audio are working.",
            wav,
        )
        print(f"Audio self-test generated: {frames} frames @ {sr} Hz, peak {peak:.3f}")
    else:
        data, sr = sf.read(wav, dtype="float32")
        peak = float(np.max(np.abs(data))) if len(data) else 0.0
        print(f"Audio self-test cache: {len(data)} frames @ {sr} Hz, peak {peak:.3f}")
    print("Audio self-test: playing through Windows default output...")
    play_wav(wav, blocking=True)
    print("Audio self-test finished.")


def speak(kokoro, available, kind, npc, text):
    voice = choose_voice(npc, available)
    key = hashlib.sha256((voice + "\0" + text).encode()).hexdigest()
    wav = CACHE / f"{key}.wav"
    print(f"Packet decoded: [{kind}] {npc}: {text}")
    if not wav.exists():
        sr, frames, peak = synthesize_to_wav(kokoro, voice, text, wav)
        print(f"TTS generated: {frames} frames @ {sr} Hz, peak {peak:.3f}")
    print(f"Playing: {wav}")
    play_wav(wav, blocking=False)


def main():
    print(f"WoW Story Voice v{VERSION}")
    print("First launch downloads the local Kokoro model. No Python installation is required.")
    ensure_models()
    print("Loading local TTS...")
    kokoro = Kokoro(str(MODEL), str(VOICES))
    available = kokoro.get_voices()
    print(f"Kokoro ready. {len(available)} voices available.")

    try:
        audio_self_test(kokoro, available)
    except Exception as e:
        print(f"AUDIO SELF-TEST FAILED: {type(e).__name__}: {e}")

    print("Ready. Start WoW and use /wsv test.")

    last = None
    bridge_pos = None
    last_search = 0.0
    last_diag = 0.0

    with mss.MSS() as sct:
        while True:
            try:
                raw = None

                if bridge_pos:
                    left, top, cell_size = bridge_pos
                    raw = sample_packet_at(sct, left, top, cell_size)
                    if not decode(raw):
                        bridge_pos = None
                        raw = None

                now = time.time()
                if not bridge_pos and now - last_search >= SEARCH_INTERVAL:
                    last_search = now
                    bridge_pos, raw = find_bridge(sct)
                    if bridge_pos:
                        print(f"Bridge detected at screen position {bridge_pos[:2]}, cell size {bridge_pos[2]} px.")

                if not bridge_pos and now - last_diag >= DIAG_INTERVAL:
                    last_diag = now
                    print("Waiting for bridge. Raw header sample:", diagnostic_header(sct))

                msg = decode(raw) if raw else None
                if msg and msg[0] != last:
                    seq, kind, npc, text = msg
                    last = seq
                    try:
                        speak(kokoro, available, kind, npc, text)
                    except Exception as e:
                        print(f"TTS/AUDIO error for packet {seq}: {type(e).__name__}: {e}")

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
