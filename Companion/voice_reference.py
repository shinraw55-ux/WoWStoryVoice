from pathlib import Path
import numpy as np
import soundfile as sf

REFERENCE_TEXT = "Greetings, traveler. The road ahead is long, but we will face whatever comes together."
MIN_SECONDS = 6.0

def ensure_reference(data_dir, voice):
    voice=str(voice or "").strip()
    if not voice:
        raise RuntimeError("A voice profile is required for reference generation")
    out=Path(data_dir)/"voice-references"/f"{voice}.wav"
    if out.exists():
        try:
            info=sf.info(str(out))
            if info.frames/float(info.samplerate)>=5.1:
                return out
        except Exception:
            pass
    out.parent.mkdir(parents=True,exist_ok=True)
    from kokoro_backend import KokoroBackend
    k=KokoroBackend(data_dir)
    audio,sr,_=k.generate(REFERENCE_TEXT,voice=voice,speed=0.92)
    audio=np.asarray(audio,dtype=np.float32).reshape(-1)
    target=int(float(sr)*MIN_SECONDS)
    if audio.size<target:
        # Silence padding preserves the speaker characteristics without
        # inventing transcript words that would make zero-shot conditioning lie.
        audio=np.pad(audio,(0,target-audio.size))
    tmp=out.with_suffix(".tmp.wav")
    sf.write(tmp,audio,int(sr),subtype="PCM_16")
    tmp.replace(out)
    return out
