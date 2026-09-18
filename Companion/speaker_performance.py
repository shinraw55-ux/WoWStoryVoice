import time
from dataclasses import replace

import chatterbox_patch


SPEAKER_REPEAT_SEC = 30.0
DEFAULT_EXPRESSIVENESS = 1.35
MIN_EXPRESSIVENESS = 0.75
MAX_EXPRESSIVENESS = 1.60

SPOKEN_DIALOGUE_KINDS = {
    "quest",
    "progress",
    "reward",
    "greeting",
    "gossip",
    "monster_say",
    "monster_yell",
    "monster_whisper",
    "monster_party",
    "monster",
}

SKIP_SPEAKER_NAMES = {"", "unknown", "narrator"}


def _clamp(value, low, high):
    return max(low, min(high, value))


def normalized_expressiveness(value):
    try:
        value = float(value)
    except Exception:
        value = DEFAULT_EXPRESSIVENESS
    return _clamp(value, MIN_EXPRESSIVENESS, MAX_EXPRESSIVENESS)


def should_announce_speaker(
    last_identity,
    last_at,
    identity,
    npc_name,
    *,
    now=None,
    enabled=True,
    repeat_sec=SPEAKER_REPEAT_SEC,
):
    if not enabled:
        return False
    name = str(npc_name or "").strip()
    if name.casefold() in SKIP_SPEAKER_NAMES:
        return False
    if not identity:
        return False
    now = time.monotonic() if now is None else float(now)
    if identity != last_identity:
        return True
    if last_at is None:
        return True
    return now - float(last_at) >= float(repeat_sec)


def shape_delivery(profile, kind, base_speed, base_pause, intensity=DEFAULT_EXPRESSIVENESS):
    """Make dialogue more animated without changing the spoken words."""
    kind_token = str(kind or "").casefold()

    if kind_token == "speaker_cue":
        return replace(
            profile,
            emotion="speaker",
            speed=0.96,
            pause=0.10,
            gain=0.70,
            score=0,
            cues=tuple(profile.cues) + ("speaker cue",),
        )

    intensity = normalized_expressiveness(intensity)

    if profile.emotion == "neutral" and kind_token in SPOKEN_DIALOGUE_KINDS:
        lift = intensity / DEFAULT_EXPRESSIVENESS
        speed = float(base_speed) * (1.0 + 0.028 * lift)
        pause = float(base_pause) * (1.0 - 0.14 * lift)
        gain = 1.0 + 0.020 * lift
        return replace(
            profile,
            emotion="conversational",
            speed=round(_clamp(speed, 0.80, 1.22), 3),
            pause=round(_clamp(pause, 0.025, 0.50), 3),
            gain=round(_clamp(gain, 0.55, 1.12), 3),
            cues=tuple(profile.cues) + ("conversational lift",),
        )

    strength = intensity
    if getattr(profile, "score", 0) >= 7:
        strength *= 1.10
    elif getattr(profile, "score", 0) >= 4:
        strength *= 1.05

    speed = float(base_speed) + (float(profile.speed) - float(base_speed)) * strength
    pause = float(base_pause) + (float(profile.pause) - float(base_pause)) * strength
    gain = 1.0 + (float(profile.gain) - 1.0) * strength

    return replace(
        profile,
        speed=round(_clamp(speed, 0.80, 1.22), 3),
        pause=round(_clamp(pause, 0.025, 0.50), 3),
        gain=round(_clamp(gain, 0.55, 1.12), 3),
        cues=tuple(profile.cues) + (f"expressiveness:{intensity:.2f}",),
    )


def make_speaker_controller(base_cls, voice_profiles_module, settings):
    """Optionally add an audible NPC-name cue before dialogue.

    The visual in-world indicator is the normal identification path. Audible
    names are a separate opt-in accessibility feature because generating and
    playing them necessarily delays the actual NPC line.
    """

    class SpeakerAwareController(base_cls):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._last_speaker_identity = None
            self._last_speaker_at = None

        def enqueue(self, kind, npc_guid, npc_name, text):
            kind_token = str(kind or "").casefold()
            name = str(npc_name or "").strip() or "Unknown"

            if kind_token not in {"control", "test", "speaker_cue"}:
                try:
                    identity = voice_profiles_module.stable_voice_identity(npc_guid, name)
                except Exception:
                    identity = f"name:{name.casefold()}"

                now = time.monotonic()
                if should_announce_speaker(
                    self._last_speaker_identity,
                    self._last_speaker_at,
                    identity,
                    name,
                    now=now,
                    enabled=bool(settings.get("spoken_speaker_name", False)),
                ):
                    super().enqueue("speaker_cue", "", "Narrator", f"{name}.")

                self._last_speaker_identity = identity
                self._last_speaker_at = now
                try:
                    self.state.set(current_speaker=name)
                except Exception:
                    pass

            return super().enqueue(kind, npc_guid, npc_name, text)

        def stop_and_clear(self):
            self._last_speaker_identity = None
            self._last_speaker_at = None
            try:
                self.state.set(current_speaker="—")
            except Exception:
                pass
            return super().stop_and_clear()

    SpeakerAwareController.__name__ = "SpeakerAwareController"
    return SpeakerAwareController


def configure_runtime(runtime_module, voice_profiles_module):
    """Install Chatterbox plus speaker/engagement layers without changing WSV6 framing."""
    # Configure the experimental engine first so beta hardening later wraps the
    # Chatterbox synthesizer rather than the legacy Kokoro function.
    chatterbox_patch.configure_runtime(runtime_module)

    if getattr(runtime_module, "_speaker_performance_configured", False):
        return

    # New key intentionally ignores the old announce_speaker value so users who
    # tried the previous default do not unknowingly keep an extra TTS pre-roll.
    runtime_module.SETTINGS.setdefault("spoken_speaker_name", False)
    runtime_module.SETTINGS.setdefault("expressiveness", DEFAULT_EXPRESSIVENESS)
    runtime_module.SETTINGS["expressiveness"] = normalized_expressiveness(
        runtime_module.SETTINGS.get("expressiveness")
    )
    try:
        runtime_module.STATE.set(current_speaker="—")
    except Exception:
        pass

    original_delivery = runtime_module.emotion_profiles.delivery_for_segment

    def expressive_delivery(kind, text, npc_guid, base_speed, base_pause, baseline=None):
        profile = original_delivery(
            kind,
            text,
            npc_guid,
            base_speed,
            base_pause,
            baseline=baseline,
        )
        return shape_delivery(
            profile,
            kind,
            base_speed,
            base_pause,
            runtime_module.SETTINGS.get("expressiveness", DEFAULT_EXPRESSIVENESS),
        )

    expressive_delivery._wsv_speaker_performance = True
    runtime_module.emotion_profiles.delivery_for_segment = expressive_delivery
    runtime_module.SpeechController = make_speaker_controller(
        runtime_module.SpeechController,
        voice_profiles_module,
        runtime_module.SETTINGS,
    )
    runtime_module._speaker_performance_configured = True
