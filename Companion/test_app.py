import json
import re
import unittest
from pathlib import Path

import runtime


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


if __name__ == "__main__":
    unittest.main()
