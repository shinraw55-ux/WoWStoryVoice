import hashlib
import os
import sys
import time
import urllib.request
from pathlib import Path

import mss
import numpy as np
import sounddevice as sd
import soundfile as sf
from kokoro_onnx import Kokoro

VERSION = "0.4.0"
MAGIC = b"WSV4"
PIXEL_SIZE = 5
X0, Y0 = 20, 20
MAX_PACKET = 103
CELL_COUNT = MAX_PACKET * 2
SEARCH_X = 1600
SEARCH_Y = 350

MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin"
PREFERRED = ["af_heart","af_bella","af_nicole","af_sarah","am_adam","am_michael"]

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
        try: tmp.unlink(missing_ok=True)
        except Exception: pass
        raise

def ensure_models():
    download(MODEL_URL, MODEL, "Kokoro voice model")
    download(VOICES_URL, VOICES, "Kokoro voice library")

def _cell_level(img, cell_index):
    x0 = cell_index * PIXEL_SIZE
    # Median over the cell interior suppresses edge scaling/antialiasing.
    patch = img[1:PIXEL_SIZE-1, x0+1:x0+PIXEL_SIZE-1, :3]
    gray = float(np.median(patch))
    # Sender uses 16 evenly spaced levels (0,17,...255).
    return max(0, min(15, int(round(gray / 17.0))))

def sample_packet_at(sct, left, top):
    width = CELL_COUNT * PIXEL_SIZE
    img = np.array(sct.grab({"left": left, "top": top, "width": width, "height": PIXEL_SIZE}))
    out = bytearray()
    for i in range(MAX_PACKET):
        hi = _cell_level(img, i * 2)
        lo = _cell_level(img, i * 2 + 1)
        out.append((hi << 4) | lo)
    return bytes(out)

def decode(raw):
    if raw is None or len(raw) < 7 or raw[:4] != MAGIC:
        return None
    seq, ln = raw[4], raw[5]
    if ln > 96 or 6 + ln >= len(raw):
        return None
    payload = raw[6:6+ln]
    if sum(payload) % 256 != raw[6+ln]:
        return None
    try:
        kind, npc, text = payload.decode("utf-8").split("\x1f", 2)
    except (ValueError, UnicodeDecodeError):
        return None
    return seq, kind, npc, text

def find_bridge(sct):
    # The screenshot from the live game verified that the addon renders at
    # approximately (20,20), so test there first.
    raw = sample_packet_at(sct, X0, Y0)
    if decode(raw):
        return X0, Y0, raw

    # UI scaling/window offsets can move the strip. Search likely offsets on
    # the same 5-pixel grid and only accept a packet that passes magic+checksum.
    for top in range(0, SEARCH_Y, PIXEL_SIZE):
        for left in range(0, SEARCH_X, PIXEL_SIZE):
            try:
                raw = sample_packet_at(sct, left, top)
            except Exception:
                continue
            if decode(raw):
                return left, top, raw
    return None, None, None

def choose_voice(npc, available):
    pool = [v for v in PREFERRED if v in available] or list(available)
    if not pool: raise RuntimeError("No Kokoro voices available")
    return pool[int(hashlib.sha256(npc.encode("utf-8")).hexdigest(), 16) % len(pool)]

def speak(kokoro, available, kind, npc, text):
    voice = choose_voice(npc, available)
    key = hashlib.sha256((voice+"\0"+text).encode()).hexdigest()
    wav = CACHE / f"{key}.wav"
    print(f"[{kind}] {npc}: {text}")
    if wav.exists():
        audio, sr = sf.read(wav, dtype="float32")
    else:
        audio, sr = kokoro.create(text, voice=voice, speed=1.0, lang="en-us")
        sf.write(wav, audio, sr)
    sd.stop()
    sd.play(audio, sr)

def main():
    print(f"WoW Story Voice v{VERSION}")
    print("First launch downloads the local Kokoro model. No Python installation is required.")
    ensure_models()
    print("Loading local TTS...")
    kokoro = Kokoro(str(MODEL), str(VOICES))
    available = kokoro.get_voices()
    print("Ready. Start WoW and use /wsv test.")
    last = None
    bridge_pos = None
    last_search = 0.0
    with mss.mss() as sct:
        while True:
            try:
                raw = None
                if bridge_pos:
                    raw = sample_packet_at(sct, *bridge_pos)
                    if not decode(raw):
                        bridge_pos = None
                if not bridge_pos and time.time() - last_search >= 1.0:
                    last_search = time.time()
                    bx, by, raw = find_bridge(sct)
                    if raw:
                        bridge_pos = (bx, by)
                        print(f"Bridge detected at screen position {bridge_pos}.")
                msg = decode(raw) if raw else None
                if msg and msg[0] != last:
                    seq, kind, npc, text = msg
                    last = seq
                    try:
                        speak(kokoro, available, kind, npc, text)
                    except Exception as e:
                        print(f"TTS error for packet {seq}: {e}")
            except Exception as e:
                print(f"Bridge read error: {e}")
                time.sleep(0.5)
            time.sleep(0.04)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\nWoW Story Voice stopped with an error:")
        print(e)
        input("\nPress Enter to close...")
        sys.exit(1)
