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

MAGIC = b"WSV2"
PIXEL_SIZE = 5
X0, Y0 = 20, 20
MAX_PACKET = 103

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

def sample_packet(sct):
    width = MAX_PACKET * PIXEL_SIZE
    img = np.array(sct.grab({"left": X0, "top": Y0, "width": width, "height": PIXEL_SIZE}))
    out = bytearray()
    for i in range(MAX_PACKET):
        x = i * PIXEL_SIZE + PIXEL_SIZE // 2
        b, g, r = img[PIXEL_SIZE // 2, x, :3]
        out.append(int(round((int(r)+int(g)+int(b))/3)))
    return bytes(out)

def decode(raw):
    if raw[:4] != MAGIC:
        return None
    seq, ln = raw[4], raw[5]
    if ln > 96 or 6 + ln >= len(raw):
        return None
    payload = raw[6:6+ln]
    if sum(payload) % 256 != raw[6+ln]:
        return None
    try:
        kind, npc, text = payload.decode("utf-8", errors="ignore").split("\x1f", 2)
    except ValueError:
        return None
    return seq, kind, npc, text

def choose_voice(npc, available):
    pool = [v for v in PREFERRED if v in available] or list(available)
    if not pool:
        raise RuntimeError("No Kokoro voices available")
    idx = int(hashlib.sha256(npc.encode("utf-8")).hexdigest(), 16) % len(pool)
    return pool[idx]

def main():
    print("WoW Story Voice v0.3.1")
    print("First launch downloads the local Kokoro model. No Python installation is required.")
    ensure_models()
    print("Loading local TTS...")
    kokoro = Kokoro(str(MODEL), str(VOICES))
    available = kokoro.get_voices()
    print("Ready. Start WoW and use /wsv test.")
    last = None
    with mss.mss() as sct:
        while True:
            msg = decode(sample_packet(sct))
            if msg and msg[0] != last:
                seq, kind, npc, text = msg
                last = seq
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
            time.sleep(0.04)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\nWoW Story Voice stopped with an error:")
        print(e)
        input("\nPress Enter to close...")
        sys.exit(1)
