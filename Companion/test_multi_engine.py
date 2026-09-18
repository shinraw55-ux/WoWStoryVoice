import tempfile
import unittest
from pathlib import Path
import tts_registry

class MultiEngineTests(unittest.TestCase):
    def test_all_three_registered(self):
        self.assertEqual(set(tts_registry.SPECS), {"kokoro", "chatterbox", "cosyvoice"})
    def test_unknown_falls_back_to_bundled_default(self):
        self.assertEqual(tts_registry.normalize_engine("wat"), "chatterbox")
    def test_only_installable_engine_is_exposed(self):
        self.assertEqual(tts_registry.bundled_engine_keys(), ["chatterbox"])
    def test_labels_are_unique(self):
        vals=list(tts_registry.labels().values())
        self.assertEqual(len(vals), len(set(vals)))
    def test_optional_cosyvoice_fails_loudly_when_missing(self):
        from cosyvoice_backend import CosyVoiceBackend
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                CosyVoiceBackend(Path(d))
if __name__ == "__main__":
    unittest.main()
