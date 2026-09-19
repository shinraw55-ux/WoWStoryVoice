import tempfile
import unittest
from pathlib import Path

import chatterbox_backend
import chatterbox_patch


class ChatterboxBackendTests(unittest.TestCase):
    def test_engine_is_turbo(self):
        self.assertEqual(chatterbox_backend.ENGINE_NAME, "Chatterbox Turbo")
        self.assertEqual(chatterbox_backend.MODEL_VARIANT, "turbo")
        self.assertIn("chatterbox-turbo", chatterbox_backend.ENGINE_NAMESPACE)

    def test_profile_ids_preserve_gender_metadata_contract(self):
        voices = chatterbox_backend.profile_voice_ids()
        self.assertIn("am_michael", voices)
        self.assertIn("af_heart", voices)
        self.assertGreaterEqual(len(voices), 20)

    def test_builtin_profiles_have_audible_gender_separation(self):
        male = chatterbox_backend.profile_rate_factor("am_michael")
        female = chatterbox_backend.profile_rate_factor("af_heart")
        self.assertLess(male, 1.0)
        self.assertGreater(female, 1.0)
        self.assertGreater(female - male, 0.10)

    def test_builtin_profiles_vary_within_gender(self):
        values = {
            round(chatterbox_backend.profile_rate_factor(v), 4)
            for v in ("am_michael", "am_fenrir", "bm_george", "am_puck")
        }
        self.assertGreater(len(values), 1)

    def test_reference_lookup_never_generates_or_downloads_on_speech_path(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = chatterbox_backend.ChatterboxBackend.__new__(
                chatterbox_backend.ChatterboxBackend
            )
            backend.voice_dir = Path(temp) / "chatterbox-voices"
            backend.voice_dir.mkdir(parents=True)
            expected = backend.voice_dir / "am_michael.wav"
            self.assertEqual(backend.reference_path("am_michael"), expected)
            self.assertFalse(expected.exists())

    def test_emotion_tags_are_sparse_and_score_gated(self):
        self.assertEqual(
            chatterbox_backend.decorate_text("We lost them.", "sorrowful", 7),
            "[sigh] We lost them.",
        )
        self.assertEqual(
            chatterbox_backend.decorate_text("We lost them.", "sorrowful", 3),
            "We lost them.",
        )
        self.assertEqual(
            chatterbox_backend.decorate_text("Attack now!", "angry", 9),
            "Attack now!",
        )

    def test_runtime_patch_uses_separate_cache_and_short_segments(self):
        class State:
            def set(self, **kwargs):
                pass

        class Emotion:
            @staticmethod
            def infer_emotion(*args, **kwargs):
                class P:
                    name = "neutral"
                    score = 0
                return P()

        with tempfile.TemporaryDirectory() as temp:
            class Runtime:
                DATA = Path(temp)
                CACHE = Path(temp) / "cache"
                FAST_TTS_SEGMENT_CHARS = 360
                STATE = State()
                emotion_profiles = Emotion()
                _chatterbox_configured = False

            old_init = object()
            old_synth = object()
            Runtime.initialize_tts = old_init
            Runtime.synthesize_to_wav = old_synth
            Runtime.VERSION = "0.8.0"

            chatterbox_patch.configure_runtime(Runtime)
            self.assertTrue(Runtime._chatterbox_configured)
            self.assertEqual(Runtime.FAST_TTS_SEGMENT_CHARS, 90)
            self.assertEqual(Runtime.CACHE.name, "cache-chatterbox-turbo")
            self.assertNotEqual(Runtime.initialize_tts, old_init)
            self.assertNotEqual(Runtime.synthesize_to_wav, old_synth)


if __name__ == "__main__":
    unittest.main()
