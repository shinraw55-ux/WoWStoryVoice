import unittest

import emotion_profiles


BOSS_GUID = (
    "Creature-0-1-2-3-9001-AAAA#wsv#"
    "sex=2;race=Orc;ctype=7;class=WARRIOR;rank=worldboss;role="
)
WHISPER_GUID = (
    "Creature-0-1-2-3-9002-BBBB#wsv#"
    "sex=3;race=NightElf;ctype=7;class=PRIEST;rank=normal;role="
)


class EmotionProfileTests(unittest.TestCase):
    def test_neutral_dialogue_stays_neutral(self):
        decision = emotion_profiles.infer_emotion(
            "quest",
            "The road continues north toward the old bridge.",
            "",
        )
        self.assertEqual(decision.name, "neutral")
        self.assertEqual(decision.score, 0)

    def test_angry_yell_is_detected(self):
        decision = emotion_profiles.infer_emotion(
            "monster_yell",
            "YOU FOOL! HOW DARE YOU!",
            BOSS_GUID,
        )
        self.assertEqual(decision.name, "angry")
        self.assertGreaterEqual(decision.score, 4)

    def test_explicit_threat_beats_weak_character_prior(self):
        decision = emotion_profiles.infer_emotion(
            "monster_say",
            "Kneel before me. You will die here.",
            BOSS_GUID,
        )
        self.assertEqual(decision.name, "threatening")
        self.assertGreaterEqual(decision.score, 6)

    def test_sorrow_is_slower_and_more_spacious(self):
        decision = emotion_profiles.infer_emotion(
            "gossip",
            "I'm sorry. We lost him... Farewell, my friend.",
            WHISPER_GUID,
        )
        self.assertEqual(decision.name, "sorrowful")
        delivery = emotion_profiles.delivery_for_segment(
            "gossip",
            "I'm sorry. We lost him... Farewell, my friend.",
            WHISPER_GUID,
            1.0,
            0.10,
            baseline=decision,
        )
        self.assertLess(delivery.speed, 1.0)
        self.assertGreater(delivery.pause, 0.10)
        self.assertLess(delivery.gain, 1.0)

    def test_whisper_is_hushed_without_changing_voice_identity(self):
        baseline = emotion_profiles.infer_emotion(
            "monster_whisper", "Come closer, my friend.", WHISPER_GUID
        )
        delivery = emotion_profiles.delivery_for_segment(
            "monster_whisper",
            "Come closer, my friend.",
            WHISPER_GUID,
            1.0,
            0.10,
            baseline=baseline,
        )
        self.assertLessEqual(delivery.gain, 0.75)
        self.assertLess(delivery.speed, 1.0)
        self.assertIn("hushed", delivery.cues)

    def test_strong_dialogue_context_carries_across_neutral_segment(self):
        baseline = emotion_profiles.EmotionDecision(
            name="urgent", score=6, cues=("test",)
        )
        delivery = emotion_profiles.delivery_for_segment(
            "quest",
            "Take the eastern road.",
            "",
            1.0,
            0.10,
            baseline=baseline,
        )
        self.assertEqual(delivery.emotion, "urgent")
        self.assertGreater(delivery.speed, 1.0)

    def test_delivery_parameters_are_safely_bounded(self):
        delivery = emotion_profiles.delivery_for_segment(
            "monster_yell",
            "RUN! RUN! RUN! NOW!",
            BOSS_GUID,
            1.10,
            0.02,
        )
        self.assertGreaterEqual(delivery.speed, 0.84)
        self.assertLessEqual(delivery.speed, 1.16)
        self.assertGreaterEqual(delivery.pause, 0.03)
        self.assertLessEqual(delivery.pause, 0.40)
        self.assertGreaterEqual(delivery.gain, 0.60)
        self.assertLessEqual(delivery.gain, 1.10)


if __name__ == "__main__":
    unittest.main()
