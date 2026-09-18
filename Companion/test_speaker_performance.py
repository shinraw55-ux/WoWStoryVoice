import unittest
from dataclasses import dataclass

import speaker_performance
import voice_profiles


@dataclass(frozen=True)
class DummyProfile:
    emotion: str
    speed: float
    pause: float
    gain: float
    score: int
    cues: tuple


class DummyState:
    def __init__(self):
        self.data = {}

    def set(self, **kwargs):
        self.data.update(kwargs)


class DummyBaseController:
    def __init__(self):
        self.items = []
        self.state = DummyState()
        self.stopped = False

    def enqueue(self, kind, npc_guid, npc_name, text):
        item = (kind, npc_guid, npc_name, text)
        self.items.append(item)
        return item

    def stop_and_clear(self):
        self.stopped = True


class SpeakerPerformanceTests(unittest.TestCase):
    def test_new_speaker_is_announced_but_repeat_is_not(self):
        settings = {"spoken_speaker_name": True}
        Controller = speaker_performance.make_speaker_controller(
            DummyBaseController, voice_profiles, settings
        )
        c = Controller()
        guid = "Creature-0-1-2-3-448-AAAA#wsv#sex=2;race=Orc"
        c.enqueue("gossip", guid, "Hogger", "First line.")
        c.enqueue("gossip", guid, "Hogger", "Second line.")

        self.assertEqual(c.items[0], ("speaker_cue", "", "Narrator", "Hogger."))
        self.assertEqual(c.items[1][0], "gossip")
        self.assertEqual(c.items[2][0], "gossip")
        self.assertEqual(len(c.items), 3)
        self.assertEqual(c.state.data["current_speaker"], "Hogger")

    def test_different_speaker_gets_new_cue(self):
        settings = {"spoken_speaker_name": True}
        Controller = speaker_performance.make_speaker_controller(
            DummyBaseController, voice_profiles, settings
        )
        c = Controller()
        c.enqueue("gossip", "Creature-0-1-2-3-100-AAAA", "NPC One", "Hello.")
        c.enqueue("gossip", "Creature-0-1-2-3-101-BBBB", "NPC Two", "Hello.")
        cues = [item for item in c.items if item[0] == "speaker_cue"]
        self.assertEqual([item[3] for item in cues], ["NPC One.", "NPC Two."])

    def test_announcement_can_be_disabled(self):
        settings = {"spoken_speaker_name": False}
        Controller = speaker_performance.make_speaker_controller(
            DummyBaseController, voice_profiles, settings
        )
        c = Controller()
        c.enqueue("gossip", "Creature-0-1-2-3-100-AAAA", "NPC One", "Hello.")
        self.assertEqual(len(c.items), 1)
        self.assertEqual(c.items[0][0], "gossip")

    def test_narrator_is_never_announced(self):
        self.assertFalse(
            speaker_performance.should_announce_speaker(
                None, None, "name:narrator", "Narrator", now=100.0
            )
        )

    def test_same_speaker_can_be_reannounced_after_timeout(self):
        self.assertTrue(
            speaker_performance.should_announce_speaker(
                "npc:448", 10.0, "npc:448", "Hogger", now=41.0, repeat_sec=30.0
            )
        )

    def test_neutral_dialogue_gets_conversational_lift(self):
        profile = DummyProfile("neutral", 1.0, 0.10, 1.0, 0, ())
        shaped = speaker_performance.shape_delivery(
            profile, "quest", 1.0, 0.10, intensity=1.35
        )
        self.assertEqual(shaped.emotion, "conversational")
        self.assertGreater(shaped.speed, 1.0)
        self.assertLess(shaped.pause, 0.10)
        self.assertGreater(shaped.gain, 1.0)

    def test_strong_emotion_is_more_dynamic_but_bounded(self):
        profile = DummyProfile("angry", 1.10, 0.07, 1.08, 8, ("test",))
        shaped = speaker_performance.shape_delivery(
            profile, "monster_yell", 1.0, 0.10, intensity=1.60
        )
        self.assertGreater(shaped.speed, profile.speed)
        self.assertLess(shaped.pause, profile.pause)
        self.assertGreater(shaped.gain, profile.gain)
        self.assertLessEqual(shaped.speed, 1.22)
        self.assertGreaterEqual(shaped.pause, 0.025)
        self.assertLessEqual(shaped.gain, 1.12)

    def test_speaker_cue_is_quieter_and_neutral(self):
        profile = DummyProfile("neutral", 1.0, 0.10, 1.0, 0, ())
        shaped = speaker_performance.shape_delivery(
            profile, "speaker_cue", 1.0, 0.10, intensity=1.35
        )
        self.assertEqual(shaped.emotion, "speaker")
        self.assertLess(shaped.gain, 0.8)
        self.assertLess(shaped.speed, 1.0)


if __name__ == "__main__":
    unittest.main()
