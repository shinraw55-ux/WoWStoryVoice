import hashlib
import os
import time
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
CACHE = Path("cache")
CACHE.mkdir(exist_ok=True)

MODEL = os.environ.get("WSV_MODEL", "kokoro-v1.0.onnx")
VOICES = os.environ.get("WSV_VOICES", "voices-v1.0.bin")
PREFERRED = ["af_heart","af_bella","af_nicole","af_sarah","am_adam","am_michael"]

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
    if raw[:4] != MAGIC: return None
    seq, ln = raw[4], raw[5]
    if ln > 96 or 6 + ln >= len(raw): return None
    payload = raw[6:6+ln]
    if sum(payload) % 256 != raw[6+ln]: return None
    try:
        kind, npc, text = payload.decode("utf-8", errors="ignore").split("\x1f", 2)
    except ValueError:
        return None
    return seq, kind, npc, text

def choose_voice(npc, available):
    pool = [v for v in PREFERRED if v in available] or list(available)
    if not pool: raise RuntimeError("No Kokoro voices available")
    idx = int(hashlib.sha256(npc.encode("utf-8")).hexdigest(), 16) % len(pool)
    return pool[idx]

def main():
    print("WoW Story Voice companion")
    print("Loading local TTS...")
    kokoro = Kokoro(MODEL, VOICES)
    available = kokoro.get_voices()
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
    main()
