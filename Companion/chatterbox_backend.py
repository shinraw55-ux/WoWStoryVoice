import os
import hashlib
from collections import OrderedDict
from pathlib import Path

import numpy as np
import soundfile as sf


ENGINE_NAME = "Chatterbox Turbo"
ENGINE_NAMESPACE = "chatterbox-turbo-v1"
MODEL_VARIANT = "turbo"
MIN_REFERENCE_SECONDS = 5.1
MAX_CONDITION_CACHE = 8

# Keep the existing profile IDs so Phase 11 gender/race/archetype selection
# remains stable. Chatterbox itself does not ship a bank of named voices; a
# matching WAV in chatterbox-voices/<profile>.wav activates zero-shot cloning
# for that profile. Without a reference WAV the built-in Chatterbox voice is
# used, so the experimental engine remains usable immediately.
PROFILE_VOICE_IDS = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica",
    "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
)

# Turbo officially supports these paralinguistic events. Keep insertion
# deliberately sparse: the goal is more life in strongly signalled moments,
# not a gasp/chuckle on every line.
EMOTION_TAGS = {
    "sorrowful": "[sigh]",
    "afraid": "[gasp]",
    "joyful": "[chuckle]",
}


def profile_voice_ids():
    return list(PROFILE_VOICE_IDS)


def profile_rate_factor(voice):
    """Give built-in Chatterbox profiles audible, deterministic separation.

    Chatterbox Turbo ships one built-in speaker. Without a >=5s reference clip,
    changing the Kokoro-style profile ID alone cannot change the speaker. Keep
    the model voice, but vary playback rate/pitch by profile so gender/profile
    selection is audible without adding another inference pass.
    """
    voice = str(voice or "").strip()
    if len(voice) < 2:
        return 1.0
    gender = voice[1]
    digest = hashlib.sha256(voice.encode("utf-8")).digest()
    variation = (digest[0] / 255.0) * 0.06
    if gender == "m":
        return 0.88 + variation
    if gender == "f":
        return 1.06 + variation
    return 0.97 + variation


def decorate_text(text, emotion="neutral", score=0):
    text = str(text or "").strip()
    if not text:
        return text
    try:
        score = int(score)
    except Exception:
        score = 0
    tag = EMOTION_TAGS.get(str(emotion or "").casefold())
    if tag and score >= 6 and tag.casefold() not in text.casefold():
        return f"{tag} {text}"
    return text


class ChatterboxBackend:
    def __init__(self, data_dir, *, variant=MODEL_VARIANT):
        if variant != "turbo":
            raise ValueError(f"Unsupported Chatterbox variant: {variant}")

        self.data_dir = Path(data_dir)
        self.voice_dir = self.data_dir / "chatterbox-voices"
        self.voice_dir.mkdir(parents=True, exist_ok=True)
        self.variant = variant
        self.cache_namespace = ENGINE_NAMESPACE
        self.engine_name = ENGINE_NAME
        self._condition_cache = OrderedDict()
        self._missing_refs_logged = set()

        # Import the heavy stack lazily. This keeps protocol/unit tests fast and
        # lets packaging tests import the module without downloading a model.
        import torch
        import perth
        if not callable(getattr(perth, "PerthImplicitWatermarker", None)):
            raise RuntimeError(
                "Chatterbox dependency Perth is incomplete: PerthImplicitWatermarker "
                "could not be imported. The packaged runtime must include pkg_resources."
            )
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        self.torch = torch
        if torch.cuda.is_available():
            self.device = "cuda"
            try:
                torch.set_float32_matmul_precision("high")
                torch.backends.cuda.matmul.allow_tf32 = True
            except Exception:
                pass
        else:
            self.device = "cpu"

        print(f"Loading {ENGINE_NAME} on {self.device.upper()}...")
        self.model = ChatterboxTurboTTS.from_pretrained(device=self.device)
        self.sample_rate = int(self.model.sr)

        # Turbo may load without precomputed voice conditionals. Older builds
        # assumed model.conds was always populated and aborted with a confusing
        # NoneType/conditional error after the model download completed.
        # Keep None as a valid built-in state; generate() can use the model's
        # default path without forcing a cloned reference voice.
        self._builtin_conds = getattr(self.model, "conds", None)
        if self._builtin_conds is None:
            print("Chatterbox Turbo loaded without built-in voice conditionals; using model defaults.")

    def profile_voice_ids(self):
        return profile_voice_ids()

    def reference_path(self, voice):
        custom = self.voice_dir / f"{str(voice or '').strip()}.wav"
        if self._valid_reference(custom):
            return custom
        try:
            from voice_reference import ensure_reference
            return ensure_reference(self.data_dir, voice)
        except Exception as e:
            print(f"Reference generation warning for {voice}: {type(e).__name__}: {e}")
            return custom

    def _valid_reference(self, path):
        try:
            info = sf.info(str(path))
            return info.frames / float(info.samplerate) >= MIN_REFERENCE_SECONDS
        except Exception:
            return False

    def _activate_voice(self, voice):
        voice = str(voice or "").strip()
        ref = self.reference_path(voice)
        if not voice or not self._valid_reference(ref):
            # Preserve the model's default unconditional state when Turbo does
            # not ship precomputed conditionals.
            self.model.conds = self._builtin_conds
            if voice and voice not in self._missing_refs_logged:
                self._missing_refs_logged.add(voice)
                print(
                    f"Chatterbox voice profile {voice}: no >=5s reference WAV; "
                    "using built-in voice."
                )
            return "builtin"

        cached = self._condition_cache.pop(voice, None)
        if cached is None:
            self.model.prepare_conditionals(str(ref), exaggeration=0.0)
            cached = self.model.conds
        self._condition_cache[voice] = cached
        while len(self._condition_cache) > MAX_CONDITION_CACHE:
            self._condition_cache.popitem(last=False)
        self.model.conds = cached
        return ref.name

    def warmup(self):
        # First CUDA inference is usually slower. Pay that cost while the splash
        # screen is visible rather than on the first NPC line.
        with self.torch.inference_mode():
            _ = self.model.generate(
                "The local voice system is warmed up and ready now.",
                temperature=0.75,
                top_p=0.92,
                top_k=700,
            )
        if self.device == "cuda":
            try:
                self.torch.cuda.synchronize()
            except Exception:
                pass

    def generate(self, text, *, voice="", temperature=0.78, top_p=0.94, top_k=850, speed=1.0, **_):
        source = self._activate_voice(voice)
        with self.torch.inference_mode():
            wav = self.model.generate(
                str(text),
                temperature=float(temperature),
                top_p=float(top_p),
                top_k=int(top_k),
            )
        audio = wav.squeeze().detach().float().cpu().numpy()
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            raise RuntimeError("Chatterbox returned an empty audio buffer")
        # Turbo's bundled model contains one built-in speaker. When no cloned
        # reference exists, make the selected NPC profile audibly distinct at
        # essentially zero compute cost by changing playback sample rate. This
        # changes pitch and pace together; real per-speaker timbre still requires
        # reference WAVs and is handled when such a reference is available.
        output_rate = self.sample_rate
        if source == "builtin":
            output_rate = max(8000, int(round(self.sample_rate * profile_rate_factor(voice))))
        return audio, output_rate, source
