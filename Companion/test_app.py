import json
import re
import tempfile
import unittest
from pathlib import Path

import runtime
import voice_profiles


ROOT = Path(__file__).resolve().parents[1]


class AppPackageTests(unittest.TestCase):
    def test_versions_are_synchronized(self):
        core = (ROOT / "WoWStoryVoice" / "Core.lua").read_text(encoding="utf-8")
        toc = (ROOT / "WoWStoryVoice" / "WoWStoryVoice.toc").read_text(encoding="utf-8")
        release = json.loads((ROOT / "release.json").read_text(encoding="utf-8"))
        installer = (ROOT / "installer" / "WoWStoryVoice.iss").read_text(encoding="utf-8")

        core_version = re.search(r'local VERSION = "([^"]+)"', core).group(1)
        toc_version = re.search(r'^## Version:\s*(\S+)', toc, re.MULTILINE).group(1)
        installer_version = re.search(r'#define MyAppVersion "([^"]+)"', installer).group(1)

        self.assertEqual(runtime.VERSION, "0.8.0")
        self.assertEqual(core_version, runtime.VERSION)
        self.assertEqual(toc_version, runtime.VERSION)
        self.assertEqual(release["version"], runtime.VERSION)
        self.assertEqual(installer_version, runtime.VERSION)

    def test_transport_stays_wsv6(self):
        self.assertEqual(runtime.engine.MAGIC, b"WSV6")

    def test_version_comparison(self):
        self.assertLess(runtime.version_tuple("0.8.0"), runtime.version_tuple("0.8.1"))
        self.assertEqual(runtime.version_tuple("v0.8"), (0, 8, 0))

    def test_addon_collects_live_sex_and_race_metadata(self):
        core = (ROOT / "WoWStoryVoice" / "Core.lua").read_text(encoding="utf-8")
        self.assertIn("UnitSex(unit)", core)
        self.assertIn("UnitRace(unit)", core)
        self.assertIn("UnitCreatureType(unit)", core)
        self.assertIn("#wsv#sex=", core)

    def test_profile_guid_parser(self):
        guid = "Creature-0-1-2-3-448-AAAA#wsv#sex=3;race=Orc;ctype=7"
        base, sex, race, creature_type = voice_profiles.parse_profile_guid(guid)
        self.assertEqual(base, "Creature-0-1-2-3-448-AAAA")
        self.assertEqual(sex, "female")
        self.assertEqual(race, "Orc")
        self.assertEqual(creature_type, "7")

    def test_gender_pool_never_crosses_known_sex_when_matching_voices_exist(self):
        available = ["am_fenrir", "am_michael", "af_nicole", "af_heart"]
        male = voice_profiles.select_voice_pool(available, sex="male", race="Orc")
        female = voice_profiles.select_voice_pool(available, sex="female", race="Orc")
        self.assertTrue(male)
        self.assertTrue(female)
        self.assertTrue(all(voice_profiles.voice_gender(v) == "m" for v in male))
        self.assertTrue(all(voice_profiles.voice_gender(v) == "f" for v in female))

    def test_orc_profile_prefers_curated_orc_style(self):
        available = ["am_fenrir", "am_onyx", "am_michael", "af_nicole"]
        pool = voice_profiles.select_voice_pool(available, sex="male", race="Orc")
        self.assertTrue(pool)
        self.assertTrue(set(pool).issubset(set(voice_profiles.STYLE_VOICES["orc"]["m"])))

    def test_profile_metadata_does_not_change_npc_identity(self):
        a = voice_profiles.stable_voice_identity(
            "Creature-0-1465-0-2105-448-AAAA#wsv#sex=2;race=Orc;ctype=7", "Hogger"
        )
        b = voice_profiles.stable_voice_identity(
            "Creature-0-9999-0-9999-448-BBBB#wsv#sex=3;race=Orc;ctype=7", "Hogger"
        )
        self.assertEqual(a, "npc:448")
        self.assertEqual(a, b)

    def test_known_sex_replaces_incompatible_cached_voice(self):
        available = ["am_fenrir", "af_nicole"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "voice-map.json"
            path.write_text(json.dumps({"npc:448": "af_nicole"}), encoding="utf-8")
            registry = voice_profiles.VoiceRegistry(available, map_path=path)
            voice = registry.voice_for(
                "Creature-0-1-2-3-448-AAAA#wsv#sex=2;race=Orc;ctype=7", "Hogger"
            )
            self.assertEqual(voice, "am_fenrir")


if __name__ == "__main__":
    unittest.main()
