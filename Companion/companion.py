import ctypes
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

VERSION = "0.5.0"
MAGIC = b"WSV5"

# The addon draws one RGB cell per 3 bits. Each byte therefore uses 3 cells.
# Only full-off/full-on channel values are used so gamma/color management
# cannot change the encoded bit identity.
CELLS_PER_BYTE = 3
MAX_PAYLOAD = 96
MAX_PACKET = 4 + 1 + 1 + MAX_PAYLOAD + 1
MAX_CELLS = MAX_PACKET * CELLS_PER_BYTE

# Physical cell size after WoW UI scaling can vary, so detection checks several
# plausible rendered sizes. The addon itself asks for 5 physical px.
CELL_SIZES = (3, 4, 5, 6, 7)
SEARCH_HEIGHT = 420
SEARCH_WIDTH = 2200
SEARCH_INTERVAL = 1.5
DIAG_INTERVAL = 5.0
BIT_THRESHOLD = 128

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
    if ln > MAX_PAYLOAD or 6 + ln >= len(raw):
        return None
    payload = raw[6:6 + ln]
    if sum(payload) % 256 != raw[6 + ln]:
        return None
    try:
        kind, npc, text = payload.decode("utf-8").split("\x1f", 2)
    except (ValueError, UnicodeDecodeError):
        return None
    return seq, kind, npc, text


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

    # mss returns BGRA. Median over the interior makes edge interpolation
    # irrelevant; convert BGR -> RGB before thresholding.
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

    # BGRA -> RGB and then binary channel values. We intentionally compare
    # only black/fully-lit channel states, not intermediate grayscale.
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

            # The four-byte header contains 36 RGB bits. More than four
            # mismatches is not a credible WSV5 candidate.
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

                if decode(raw):
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
    return f"best WSV5 header score={score}/36 near {hint}"


def minimize_console():
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            # SW_MINIMIZE = 6. This prevents the companion window from
            # covering the pixels it is trying to screen-capture.
            ctypes.windll.user32.ShowWindow(hwnd, 6)
    except Exception as e:
        print(f"Console auto-minimize failed: {type(e).__name__}: {e}")


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
    wav = CACHE / f"audio-self-test-v{VERSION}.wav"
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
    print("Transport: WSV5 binary RGB")
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

    print("Ready. The console will minimize so it cannot cover the WoW bridge.")
    print("Use /wsv test in WoW. Restore this window later to read diagnostics.")
    time.sleep(1.0)
    minimize_console()

    last = None
    bridge_pos = None
    last_search = 0.0
    last_diag = 0.0
    best_diag = None

    with mss.MSS() as sct:
        while True:
            try:
                raw = None

                if bridge_pos:
                    left, top, cell_size = bridge_pos
                    try:
                        raw = sample_packet_at(sct, left, top, cell_size)
                    except Exception:
                        raw = None

                    if not decode(raw):
                        bridge_pos = None
                        raw = None

                now = time.time()
                if not bridge_pos and now - last_search >= SEARCH_INTERVAL:
                    last_search = now
                    bridge_pos, raw, best_diag = find_bridge(sct)
                    if bridge_pos:
                        print(
                            f"Bridge detected at screen position {bridge_pos[:2]}, "
                            f"cell size {bridge_pos[2]} px."
                        )

                if not bridge_pos and now - last_diag >= DIAG_INTERVAL:
                    last_diag = now
                    print("Waiting for bridge.", diagnostic_bridge(best_diag))

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
