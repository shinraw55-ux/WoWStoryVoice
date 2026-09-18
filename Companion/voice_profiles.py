import hashlib
import json
import re
from pathlib import Path

import companion as engine

PROFILE_MARKER = "#wsv#"

# These are stylistic groupings of existing Kokoro voices. They are not claims
# that the voices reproduce Blizzard's canonical performances. Missing voices
# are ignored at runtime so profiles survive Kokoro voice-set changes.
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

# Phase 11 adds a second layer: what kind of character this NPC is. The addon
# supplies only metadata Blizzard exposes directly (class, creature type,
# classification and interaction role). We deliberately do not guess from an
# NPC name or dialogue text.
ARCHETYPE_VOICES = {
    "warrior": {
        "m": ["am_fenrir", "am_onyx", "bm_george", "am_adam", "bm_lewis"],
        "f": ["af_kore", "af_nicole", "bf_lily", "af_bella", "bf_alice"],
    },
    "holy": {
        "m": ["am_michael", "bm_daniel", "am_liam", "bm_george", "am_echo"],
        "f": ["af_sarah", "af_heart", "bf_emma", "af_aoede", "bf_isabella"],
    },
    "mystic": {
        "m": ["am_echo", "bm_fable", "am_michael", "am_puck", "am_adam"],
        "f": ["af_river", "af_aoede", "af_heart", "af_sky", "af_bella"],
    },
    "arcane": {
        "m": ["am_echo", "bm_fable", "am_liam", "am_eric", "am_michael"],
        "f": ["af_aoede", "af_nova", "af_sarah", "bf_isabella", "af_alloy"],
    },
    "dark": {
        "m": ["am_onyx", "am_fenrir", "bm_lewis", "bm_fable", "am_adam"],
        "f": ["af_kore", "af_nicole", "bf_lily", "bf_alice", "af_bella"],
    },
    "ranger": {
        "m": ["am_eric", "am_liam", "am_puck", "bm_daniel", "am_echo"],
        "f": ["af_sky", "af_nova", "af_jessica", "af_river", "af_sarah"],
    },
    "rogue": {
        "m": ["am_puck", "am_eric", "am_echo", "bm_fable", "am_liam"],
        "f": ["af_nova", "af_sky", "af_jessica", "af_alloy", "af_river"],
    },
    "merchant": {
        "m": ["am_eric", "am_puck", "bm_daniel", "am_michael", "am_liam"],
        "f": ["af_jessica", "af_nova", "bf_emma", "af_sarah", "af_sky"],
    },
    "scholar": {
        "m": ["bm_daniel", "am_michael", "am_echo", "bm_fable", "am_liam"],
        "f": ["bf_emma", "af_sarah", "af_aoede", "bf_isabella", "af_heart"],
    },
    "formal": {
        "m": ["bm_george", "bm_daniel", "am_michael", "am_adam", "am_liam"],
        "f": ["bf_emma", "bf_alice", "af_sarah", "bf_isabella", "af_heart"],
    },
    "artisan": {
        "m": ["am_eric", "bm_daniel", "am_michael", "am_puck", "bm_george"],
        "f": ["af_jessica", "bf_emma", "af_sarah", "af_nova", "bf_alice"],
    },
    "mechanical": {
        "m": ["am_eric", "am_liam", "am_echo", "am_puck", "bm_fable"],
        "f": ["af_alloy", "af_nova", "af_sky", "af_jessica", "af_aoede"],
    },
    "dragon": {
        "m": ["am_onyx", "am_echo", "bm_fable", "am_michael", "am_fenrir"],
        "f": ["af_kore", "af_aoede", "bf_isabella", "af_heart", "af_nicole"],
    },
    "giant": {
        "m": ["bm_george", "am_onyx", "bm_lewis", "am_adam", "am_fenrir"],
        "f": ["bf_lily", "af_nicole", "bf_alice", "af_kore", "af_bella"],
    },
    "wild": {
        "m": ["am_fenrir", "am_puck", "am_eric", "am_onyx", "am_echo"],
        "f": ["af_river", "af_sky", "af_nicole", "af_nova", "af_kore"],
    },
    "boss": {
        "m": ["am_onyx", "am_fenrir", "bm_lewis", "bm_george", "am_adam"],
        "f": ["af_kore", "af_nicole", "bf_lily", "bf_alice", "af_bella"],
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

CLASS_ARCHETYPE = {
    "warrior": "warrior",
    "paladin": "holy",
    "hunter": "ranger",
    "rogue": "rogue",
    "priest": "holy",
    "deathknight": "dark",
    "shaman": "mystic",
    "mage": "arcane",
    "warlock": "dark",
    "monk": "mystic",
    "druid": "mystic",
    "demonhunter": "dark",
    "evoker": "arcane",
}

CREATURE_ARCHETYPE = {
    "1": "wild",       # Beast
    "2": "dragon",     # Dragonkin
    "3": "dark",       # Demon
    "4": "mystic",     # Elemental
    "5": "giant",      # Giant
    "6": "dark",       # Undead
    "9": "mechanical", # Mechanical
    "15": "dark",      # Aberration
}

ROLE_ARCHETYPE = {
    "merchant": "merchant",
    "auctioneer": "merchant",
    "trainer": "scholar",
    "guide": "scholar",
    "banker": "formal",
    "spirithealer": "mystic",
    "stablemaster": "ranger",
    "battlemaster": "warrior",
    "transmogrifier": "artisan",
    "profession": "artisan",
    "forgemaster": "artisan",
}

BOSS_RANKS = {"worldboss", "rareelite", "elite"}


def _normalize_token(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _normalize_race(value):
    return _normalize_token(value)


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

    return (
        base,
        sex,
        metadata.get("race", ""),
        metadata.get("ctype", ""),
        metadata.get("class", ""),
        metadata.get("rank", ""),
        metadata.get("role", ""),
    )


def voice_gender(voice):
    name = str(voice or "")
    if len(name) >= 2 and name[1] in ("f", "m"):
        return name[1]
    return None


def profile_archetypes(creature_type="", class_name="", rank="", role=""):
    archetypes = []

    role_style = ROLE_ARCHETYPE.get(_normalize_token(role))
    if role_style:
        archetypes.append(role_style)

    class_style = CLASS_ARCHETYPE.get(_normalize_token(class_name))
    if class_style and class_style not in archetypes:
        archetypes.append(class_style)

    creature_style = CREATURE_ARCHETYPE.get(str(creature_type or "").strip())
    if creature_style and creature_style not in archetypes:
        archetypes.append(creature_style)

    if _normalize_token(rank) in BOSS_RANKS and "boss" not in archetypes:
        archetypes.append("boss")

    return archetypes


def _preference_scores(base, preferred, weight):
    scores = {voice: 0 for voice in base}
    for index, voice in enumerate(preferred):
        if voice in scores:
            scores[voice] += max(1, weight - index)
    return scores


def select_voice_pool(
    available,
    sex="unknown",
    race="",
    creature_type="",
    class_name="",
    rank="",
    role="",
):
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

    scores = {voice: 0 for voice in base}

    race_style = RACE_STYLE.get(_normalize_race(race))
    if race_style:
        config = STYLE_VOICES.get(race_style, {})
        preferred = config.get(wanted_gender, []) if wanted_gender else config.get("m", []) + config.get("f", [])
        for voice, score in _preference_scores(base, preferred, 9).items():
            scores[voice] += score

    for archetype_index, archetype in enumerate(
        profile_archetypes(creature_type, class_name, rank, role)
    ):
        config = ARCHETYPE_VOICES.get(archetype, {})
        preferred = config.get(wanted_gender, []) if wanted_gender else config.get("m", []) + config.get("f", [])
        weight = 9 if archetype_index == 0 else 7
        for voice, score in _preference_scores(base, preferred, weight).items():
            scores[voice] += score

    max_score = max(scores.values()) if scores else 0
    if max_score <= 0:
        return base

    ranked = sorted(base, key=lambda voice: (-scores[voice], voice))
    # Keep a small top tier so two NPCs with the same broad profile can still
    # receive different persistent voices while staying stylistically close.
    cutoff = max_score - 4
    top = [voice for voice in ranked if scores[voice] >= cutoff]
    return top[:5] or ranked[:1]


def stable_voice_identity(npc_guid, npc_name):
    base_guid, *_ = parse_profile_guid(npc_guid)
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
        (
            base_guid,
            sex,
            race,
            creature_type,
            class_name,
            rank,
            role,
        ) = parse_profile_guid(npc_guid)
        identity = engine._voice_identity(base_guid, npc_name)
        pool = select_voice_pool(
            self.available,
            sex=sex,
            race=race,
            creature_type=creature_type,
            class_name=class_name,
            rank=rank,
            role=role,
        )
        if not pool:
            pool = self.available

        current = self.mapping.get(identity)
        if current in pool:
            return current

        archetypes = profile_archetypes(creature_type, class_name, rank, role)
        profile_key = "\0".join(
            [
                identity,
                sex,
                _normalize_race(race),
                str(creature_type or ""),
                _normalize_token(class_name),
                _normalize_token(rank),
                _normalize_token(role),
                ",".join(archetypes),
            ]
        )
        digest = hashlib.sha256(profile_key.encode("utf-8")).digest()
        voice = pool[int.from_bytes(digest[:8], "big") % len(pool)]
        self.mapping[identity] = voice
        try:
            self._save()
        except Exception as e:
            print(f"Voice map save warning: {type(e).__name__}: {e}")

        labels = ",".join(archetypes) if archetypes else "generic"
        print(
            f"Voice profile assigned: {npc_name}, sex={sex}, race={race or 'unknown'}, "
            f"class={class_name or 'unknown'}, creature_type={creature_type or 'unknown'}, "
            f"rank={rank or 'unknown'}, role={role or 'none'}, archetypes={labels}, voice={voice}"
        )
        return voice
