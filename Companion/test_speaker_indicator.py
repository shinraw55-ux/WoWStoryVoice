import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "WoWStoryVoice" / "Core.lua"
INDICATOR = ROOT / "WoWStoryVoice" / "SpeakerIndicator.lua"
TOC = ROOT / "WoWStoryVoice" / "WoWStoryVoice.toc"


class SpeakerIndicatorTests(unittest.TestCase):
    def test_indicator_is_loaded_after_core(self):
        lines = [line.strip() for line in TOC.read_text(encoding="utf-8").splitlines()]
        self.assertIn("Core.lua", lines)
        self.assertIn("SpeakerIndicator.lua", lines)
        self.assertLess(lines.index("Core.lua"), lines.index("SpeakerIndicator.lua"))

    def test_uses_current_nameplate_api(self):
        source = INDICATOR.read_text(encoding="utf-8")
        self.assertIn('RegisterEvent("NAME_PLATE_UNIT_ADDED")', source)
        self.assertIn('RegisterEvent("NAME_PLATE_UNIT_REMOVED")', source)
        self.assertIn("C_NamePlate.GetNamePlateForUnit", source)
        self.assertIn("UnitGUID(unit)", source)

    def test_tracks_same_dialogue_sources_as_voice_capture(self):
        source = INDICATOR.read_text(encoding="utf-8")
        for event in (
            "QUEST_DETAIL",
            "QUEST_PROGRESS",
            "QUEST_COMPLETE",
            "QUEST_GREETING",
            "GOSSIP_SHOW",
            "CHAT_MSG_MONSTER_SAY",
            "CHAT_MSG_MONSTER_YELL",
            "CHAT_MSG_MONSTER_WHISPER",
            "CHAT_MSG_MONSTER_PARTY",
        ):
            self.assertIn(f'RegisterEvent("{event}")', source)

    def test_honors_existing_dialogue_toggles_and_blizzard_skip(self):
        source = INDICATOR.read_text(encoding="utf-8")
        self.assertIn("WoWStoryVoiceDB.questDialogue", source)
        self.assertIn("WoWStoryVoiceDB.gossipDialogue", source)
        self.assertIn("WoWStoryVoiceDB.monsterDialogue", source)
        self.assertIn("WoWStoryVoiceDB.skipBlizzardVoiced", source)

    def test_has_fallback_when_nameplate_is_not_available(self):
        source = INDICATOR.read_text(encoding="utf-8")
        self.assertIn('"Speaking: " .. activeName', source)
        self.assertIn("showFallback", source)

    def test_transport_file_is_not_modified_by_indicator_module(self):
        source = INDICATOR.read_text(encoding="utf-8")
        self.assertNotIn("MAGIC", source)
        self.assertNotIn("emitBytes", source)
        self.assertIn('local MAGIC = "WSV6"', CORE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
