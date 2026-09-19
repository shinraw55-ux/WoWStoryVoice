import json
import zipfile
import re
import tempfile
import unittest
from pathlib import Path

import runtime
import voice_profiles
import app


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

    def test_gui_runtime_uses_profile_aware_registry(self):
        self.assertIs(runtime.engine.VoiceRegistry, voice_profiles.VoiceRegistry)

    def test_version_comparison(self):
        self.assertLess(runtime.version_tuple("0.8.0"), runtime.version_tuple("0.8.1"))
        self.assertEqual(runtime.version_tuple("v0.8"), (0, 8, 0))

    def test_addon_collects_character_profile_metadata(self):
        core = (ROOT / "WoWStoryVoice" / "Core.lua").read_text(encoding="utf-8")
        self.assertIn("UnitSex(unit)", core)
        self.assertIn("UnitRace(unit)", core)
        self.assertIn("UnitCreatureType(unit)", core)
        self.assertIn("UnitClass(unit)", core)
        self.assertIn("UnitClassification(unit)", core)
        self.assertIn("PLAYER_INTERACTION_MANAGER_FRAME_SHOW", core)
        self.assertIn("WoWStoryVoiceDB.npcProfiles", core)
        self.assertIn(";class=", core)
        self.assertIn(";rank=", core)
        self.assertIn(";role=", core)

    def test_profile_guid_parser(self):
        guid = (
            "Creature-0-1-2-3-448-AAAA#wsv#"
            "sex=3;race=Orc;ctype=7;class=SHAMAN;rank=elite;role=trainer"
        )
        base, sex, race, creature_type, class_name, rank, role = voice_profiles.parse_profile_guid(guid)
        self.assertEqual(base, "Creature-0-1-2-3-448-AAAA")
        self.assertEqual(sex, "female")
        self.assertEqual(race, "Orc")
        self.assertEqual(creature_type, "7")
        self.assertEqual(class_name, "SHAMAN")
        self.assertEqual(rank, "elite")
        self.assertEqual(role, "trainer")

    def test_old_profile_metadata_remains_compatible(self):
        guid = "Creature-0-1-2-3-448-AAAA#wsv#sex=2;race=Orc;ctype=7"
        _, sex, race, creature_type, class_name, rank, role = voice_profiles.parse_profile_guid(guid)
        self.assertEqual((sex, race, creature_type), ("male", "Orc", "7"))
        self.assertEqual((class_name, rank, role), ("", "", ""))

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

    def test_class_changes_style_inside_same_race(self):
        available = [
            "am_fenrir", "am_onyx", "am_adam", "am_echo", "am_michael", "bm_fable"
        ]
        warrior = voice_profiles.select_voice_pool(
            available, sex="male", race="Orc", creature_type="7", class_name="WARRIOR"
        )
        shaman = voice_profiles.select_voice_pool(
            available, sex="male", race="Orc", creature_type="7", class_name="SHAMAN"
        )
        self.assertIn("am_fenrir", warrior)
        self.assertNotIn("am_echo", warrior)
        self.assertIn("am_echo", shaman)

    def test_interaction_role_can_shape_character_profile(self):
        available = ["am_michael", "bm_daniel", "am_eric", "am_puck", "am_onyx"]
        merchant = voice_profiles.select_voice_pool(
            available, sex="male", race="Human", role="merchant"
        )
        self.assertIn("am_eric", merchant)
        self.assertNotIn("am_onyx", merchant)

    def test_creature_type_profiles_non_humanoids(self):
        available = ["am_eric", "am_liam", "am_onyx"]
        mechanical = voice_profiles.select_voice_pool(
            available, sex="male", creature_type="9"
        )
        self.assertIn("am_eric", mechanical)
        self.assertIn("am_liam", mechanical)
        self.assertNotIn("am_onyx", mechanical)

    def test_elite_rank_adds_boss_style(self):
        available = ["am_onyx", "am_fenrir", "am_eric"]
        elite = voice_profiles.select_voice_pool(
            available, sex="male", rank="worldboss"
        )
        self.assertIn("am_onyx", elite)
        self.assertIn("am_fenrir", elite)
        self.assertNotIn("am_eric", elite)

    def test_profile_metadata_does_not_change_npc_identity(self):
        a = voice_profiles.stable_voice_identity(
            "Creature-0-1465-0-2105-448-AAAA#wsv#sex=2;race=Orc;ctype=7;class=WARRIOR", "Hogger"
        )
        b = voice_profiles.stable_voice_identity(
            "Creature-0-9999-0-9999-448-BBBB#wsv#sex=3;race=Orc;ctype=7;class=SHAMAN", "Hogger"
        )
        self.assertEqual(a, "npc:448")
        self.assertEqual(a, b)

    def test_known_profile_replaces_incompatible_cached_voice(self):
        available = ["am_fenrir", "af_nicole"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "voice-map.json"
            path.write_text(json.dumps({"npc:448": "af_nicole"}), encoding="utf-8")
            registry = voice_profiles.VoiceRegistry(available, map_path=path)
            voice = registry.voice_for(
                "Creature-0-1-2-3-448-AAAA#wsv#sex=2;race=Orc;ctype=7;class=WARRIOR",
                "Hogger",
            )
            self.assertEqual(voice, "am_fenrir")

    def test_addon_installer_rejects_zip_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "bad.zip"
            addons = root / "AddOns"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("WoWStoryVoice/WoWStoryVoice.toc", "## Interface: 120000")
                zf.writestr("WoWStoryVoice/../../escaped.txt", "nope")
            original = runtime.locate_addon_zip
            runtime.locate_addon_zip = lambda: archive
            try:
                with self.assertRaisesRegex(RuntimeError, "Unsafe addon ZIP"):
                    runtime.install_addon_to(addons)
                self.assertFalse((root / "escaped.txt").exists())
            finally:
                runtime.locate_addon_zip = original


if __name__ == "__main__":
    unittest.main()
