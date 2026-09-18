import re
import unittest
from pathlib import Path

import speaker_performance


ROOT = Path(__file__).resolve().parents[1]
CORE = (ROOT / "WoWStoryVoice" / "Core.lua").read_text(encoding="utf-8")


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

    def test_packet_hold_time_remains_longer_than_companion_poll_period(self):
        match = re.search(r"local TX_HOLD_SEC = ([0-9.]+)", CORE)
        self.assertIsNotNone(match)
        hold = float(match.group(1))
        # Companion currently polls at 40 ms. Keep at least ~3 samples/chunk.
        self.assertGreaterEqual(hold, 0.12)

    def test_audible_speaker_preroll_is_opt_in(self):
        class Settings(dict):
            def setdefault(self, key, default=None):
                return super().setdefault(key, default)

        class State:
            def set(self, **kwargs):
                pass

        class Runtime:
            _speaker_performance_configured = False
            SETTINGS = Settings()
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
        self.assertFalse(Runtime.SETTINGS["announce_speaker"])


if __name__ == "__main__":
    unittest.main()
