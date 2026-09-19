import re
import unittest
from pathlib import Path

import runtime
import speaker_performance


ROOT = Path(__file__).resolve().parents[1]
CORE = (ROOT / "WoWStoryVoice" / "Core.lua").read_text(encoding="utf-8")
REQS = (ROOT / "Companion" / "requirements.txt").read_text(encoding="utf-8")


class DialogueLatencyTests(unittest.TestCase):
    def test_wsv6_packet_format_is_unchanged(self):
        self.assertIn('local MAGIC = "WSV6"', CORE)
        self.assertIn('local CHUNK_DATA_MAX = 91', CORE)

    def test_fresh_and_retry_packets_use_separate_queues(self):
        self.assertIn("local retryQueue = {}", CORE)
        self.assertIn("local retryHead = 1", CORE)
        self.assertIn("for _ = 2, rounds do", CORE)
        self.assertIn("retryQueue[#retryQueue + 1] = packet", CORE)
        self.assertIn("if hasFreshPackets() then", CORE)
        self.assertIn("if hasRetryPackets() then", CORE)

        fresh_pos = CORE.index("if hasFreshPackets() then")
        retry_pos = CORE.index("if hasRetryPackets() then", fresh_pos)
        self.assertLess(fresh_pos, retry_pos)

    def test_first_pass_is_not_repeated_in_fresh_queue(self):
        block = re.search(
            r"local function enqueueMessage\(.*?\nend\n\nlocal function queueHeartbeat",
            CORE,
            re.S,
        )
        self.assertIsNotNone(block)
        text = block.group(0)
        self.assertIn("for _, packet in ipairs(packets) do txQueue[#txQueue + 1] = packet end", text)
        self.assertNotIn("for _ = 1, rounds do", text)

    def test_transport_and_capture_are_low_latency(self):
        match = re.search(r"local TX_HOLD_SEC = ([0-9.]+)", CORE)
        self.assertIsNotNone(match)
        hold = float(match.group(1))
        self.assertLessEqual(hold, 0.05)
        self.assertGreater(hold, runtime.CAPTURE_POLL_SEC * 2)
        self.assertLessEqual(runtime.CAPTURE_POLL_SEC, 0.015)

    def test_tts_fast_start_chunks_are_bounded(self):
        self.assertLessEqual(runtime.FAST_TTS_SEGMENT_CHARS, 48)
        sample = "This is a deliberately long line of dialogue " * 12
        chunks = runtime.engine.split_dialogue(sample, limit=runtime.FAST_TTS_SEGMENT_CHARS)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= runtime.FAST_TTS_SEGMENT_CHARS for chunk in chunks))

    def test_chatterbox_first_audio_chunk_is_aggressive(self):
        import multi_engine

        class Settings(dict):
            pass
        class State:
            def set(self, **kwargs):
                pass
        class Runtime:
            _multi_engine_configured = False
            DATA = Path(".")
            CACHE = Path(".")
            SETTINGS = Settings({"tts_engine": "chatterbox"})
            STATE = State()
            FAST_TTS_SEGMENT_CHARS = 120
            class emotion_profiles:
                pass
            @staticmethod
            def save_settings(_settings):
                pass

        multi_engine.configure_runtime(Runtime)
        self.assertLessEqual(Runtime.FAST_TTS_SEGMENT_CHARS, 48)

    def test_chatterbox_turbo_is_requested(self):
        self.assertIn("chatterbox-tts==0.1.7", REQS)
        self.assertNotIn("kokoro-onnx[gpu]", REQS)

    def test_audible_speaker_preroll_uses_new_opt_in_key(self):
        class Settings(dict):
            def setdefault(self, key, default=None):
                return super().setdefault(key, default)

        class State:
            def set(self, **kwargs):
                pass

        class Runtime:
            _speaker_performance_configured = False
            SETTINGS = Settings({"announce_speaker": True})
            STATE = State()

            class emotion_profiles:
                @staticmethod
                def delivery_for_segment(*args, **kwargs):
                    raise AssertionError("not called in configuration test")

            class SpeechController:
                pass

        class Voices:
            @staticmethod
            def stable_voice_identity(guid, name):
                return name

        speaker_performance.configure_runtime(Runtime, Voices)
        self.assertFalse(Runtime.SETTINGS["spoken_speaker_name"])
        self.assertTrue(Runtime.SETTINGS["announce_speaker"])


if __name__ == "__main__":
    unittest.main()
