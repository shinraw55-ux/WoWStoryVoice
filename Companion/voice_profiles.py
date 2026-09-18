import hashlib
import json
import re
from pathlib import Path

import companion as engine

PROFILE_MARKER = "#wsv#"

# These are stylistic groupings of existing Kokoro voices, not claims that the
# voices reproduce Blizzard's canonical race performances. Missing voices are
# simply ignored at runtime, so the profiles survive Kokoro voice-set changes.
STYLE_VOICES = {
    "human": {
        "m": ["am_michael", "bm_daniel", "am_eric", "bm_george"],
        "f": ["af_sarah", "bf_emma", "af_heart", "bf_isabella"],
    },
    "orc": {
        "m": ["am_fenrir", "am_onyx", "am_adam", "bm_lewis"],
        "f": ["af_nicole", "af_kore", "af_bella", "bf_lily"],
    },
    "dwarf": {
        "m": ["bm_george", "bm_lewis", "bm_daniel", "am_adam"],
        "f": ["bf_alice", "bf_emma", "bf_lily", "af_bella"],
    },
    "elf": {
        "m": ["am_echo", "bm_fable", "am_liam", "bm_daniel"],
        "f": ["af_aoede", "af_heart", "bf_isabella", "af_sarah"],
    },
    "tauren": {
        "m": ["am_adam", "am_michael", "bm_george", "am_onyx"],
        "f": ["af_bella", "af_nicole", "bf_lily", "af_kore"],
    },
    "small": {
        "m": ["am_puck", "am_eric", "am_echo", "am_liam"],
        "f": ["af_sky", "af_nova", "af_jessica", "af_alloy"],
    },
    "draenei": {
        "m": ["bm_daniel", "am_michael", "am_echo", "bm_george"],
        "f": ["bf_isabella", "af_heart", "af_aoede", "bf_emma"],
    },
    "worgen": {
        "m": ["bm_lewis", "am_fenrir", "bm_george", "am_onyx"],
        "f": ["bf_lily", "bf_alice", "af_nicole", "af_kore"],
    },
    "troll": {
        "m": ["am_fenrir", "am_puck", "bm_fable", "am_eric"],
        "f": ["af_river", "af_nicole", "af_kore", "af_nova"],
    },
    "pandaren": {
        "m": ["am_michael", "bm_daniel", "am_eric", "bm_george"],
        "f": ["af_river", "af_sarah", "bf_emma", "af_heart"],
    },
    "dracthyr": {
        "m": ["am_echo", "am_onyx", "bm_fable", "am_michael"],
        "f": ["af_aoede", "af_kore", "bf_isabella", "af_heart"],
    },
    "undead": {
        "m": ["am_onyx", "bm_lewis", "am_fenrir", "bm_fable"],
        "f": ["af_nicole", "af_kore", "bf_lily", "bf_alice"],
    },
}

RACE_STYLE = {
    "human": "human",
    "kultiran": "human",
    "orc": "orc",
    "magharorc": "orc",
    "dwarf": "dwarf",
    "darkirondwarf": "dwarf",
    "earthen": "dwarf",
    "nightelf": "elf",
    "bloodelf": "elf",
    "voidelf": "elf",
    "nightborne": "elf",
    "tauren": "tauren",
    "highmountaintauren": "tauren",
    "gnome": "small",
    "mechagnome": "small",
    "goblin": "small",
    "vulpera": "small",
    "draenei": "draenei",
    "lightforgeddraenei": "draenei",
    "worgen": "worgen",
    "troll": "troll",
    "zandalaritroll": "troll",
    "pandaren": "pandaren",
    "dracthyr": "dracthyr",
    "undead": "undead",
    "scourge": "undead",
}


def _normalize_race(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def parse_profile_guid(npc_guid):
    raw = str(npc_guid or "")
    base, marker, payload = raw.partition(PROFILE_MARKER)
    metadata = {}
    if marker:
        for item in payload.split(";"):
            key, sep, value = item.partition("=")
            if sep and key:
                metadata[key.strip().casefold()] = value.strip()

    sex_code = metadata.get("sex", "1")
    if sex_code == "2":
        sex = "male"
    elif sex_code == "3":
        sex = "female"
    else:
        sex = "unknown"

    race = metadata.get("race", "")
    creature_type = metadata.get("ctype", "")
    return base, sex, race, creature_type


def voice_gender(voice):
    name = str(voice or "")
    if len(name) >= 2 and name[1] in ("f", "m"):
        return name[1]
    return None


def select_voice_pool(available, sex="unknown", race=""):
    available = list(dict.fromkeys(str(v) for v in available))
    if not available:
        return []

    english = [v for v in available if len(v) >= 2 and v[0] in ("a", "b")]
    base = english or available

    wanted_gender = {"male": "m", "female": "f"}.get(str(sex).casefold())
    if wanted_gender:
        gendered = [v for v in base if voice_gender(v) == wanted_gender]
        if not gendered:
            gendered = [v for v in available if voice_gender(v) == wanted_gender]
        if gendered:
            base = gendered

    style = RACE_STYLE.get(_normalize_race(race))
    if style:
        config = STYLE_VOICES.get(style, {})
        if wanted_gender:
            candidates = config.get(wanted_gender, [])
        else:
            candidates = config.get("m", []) + config.get("f", [])
        styled = [v for v in candidates if v in base]
        if styled:
            return styled

    return base


def stable_voice_identity(npc_guid, npc_name):
    base_guid, _, _, _ = parse_profile_guid(npc_guid)
    return engine._voice_identity(base_guid, npc_name)


class VoiceRegistry:
    def __init__(self, available, map_path=None):
        self.available = list(available)
        if not self.available:
            raise RuntimeError("No Kokoro voices available")
        self.map_path = Path(map_path) if map_path is not None else engine.VOICE_MAP
        self.mapping = {}
        self._load()

    def _load(self):
        if not self.map_path.exists():
            return
        try:
            raw = json.loads(self.map_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.mapping = {
                    str(k): str(v)
                    for k, v in raw.items()
                    if isinstance(v, str) and v in self.available
                }
        except Exception as e:
            print(f"Voice map load warning: {type(e).__name__}: {e}")

    def _save(self):
        self.map_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.map_path.with_suffix(self.map_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.mapping, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.map_path)

    def voice_for(self, npc_guid, npc_name):
        base_guid, sex, race, creature_type = parse_profile_guid(npc_guid)
        identity = engine._voice_identity(base_guid, npc_name)
        pool = select_voice_pool(self.available, sex=sex, race=race)
        if not pool:
            pool = self.available

        current = self.mapping.get(identity)
        if current in pool:
            return current

        profile_key = f"{identity}\0{sex}\0{_normalize_race(race)}"
        digest = hashlib.sha256(profile_key.encode("utf-8")).digest()
        voice = pool[int.from_bytes(digest[:8], "big") % len(pool)]
        self.mapping[identity] = voice
        try:
            self._save()
        except Exception as e:
            print(f"Voice map save warning: {type(e).__name__}: {e}")

        race_label = race or "unknown"
        type_label = creature_type or "unknown"
        print(
            f"Voice profile assigned: {npc_name}, sex={sex}, race={race_label}, "
            f"creature_type={type_label}, voice={voice}"
        )
        return voice
