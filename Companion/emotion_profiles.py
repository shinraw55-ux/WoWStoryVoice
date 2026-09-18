import re
from dataclasses import dataclass

import voice_profiles


@dataclass(frozen=True)
class EmotionDecision:
    name: str
    score: int
    cues: tuple


@dataclass(frozen=True)
class DeliveryProfile:
    emotion: str
    speed: float
    pause: float
    gain: float
    score: int
    cues: tuple


# Kokoro currently exposes speed but not a dedicated emotion parameter in the
# path WoW Story Voice uses. Phase 12 therefore keeps each NPC's persistent
# voice identity and changes only safe delivery controls: speed, pause and
# relative loudness. Dialogue text itself is never rewritten.
EMOTION_DELIVERY = {
    "neutral": (1.00, 1.00, 1.00),
    "angry": (1.07, 0.78, 1.06),
    "urgent": (1.10, 0.68, 1.04),
    "threatening": (0.95, 1.18, 1.06),
    "afraid": (1.06, 0.88, 0.98),
    "sorrowful": (0.91, 1.55, 0.90),
    "joyful": (1.05, 0.84, 1.03),
    "warm": (1.02, 1.02, 1.01),
    "solemn": (0.92, 1.48, 0.94),
    "inquisitive": (0.98, 1.12, 1.00),
    "commanding": (0.98, 0.92, 1.05),
}

# Ordered only for deterministic tie-breaking. Strong text/context evidence is
# scored below; broad character archetypes are intentionally weak hints.
EMOTION_ORDER = [
    "angry",
    "urgent",
    "threatening",
    "afraid",
    "sorrowful",
    "joyful",
    "solemn",
    "commanding",
    "warm",
    "inquisitive",
    "neutral",
]

PHRASES = {
    "angry": (
        "you fool", "fool!", "coward", "traitor", "how dare", "damn you",
        "curse you", "enough!", "insolence", "pathetic", "idiot",
    ),
    "urgent": (
        "hurry", "quickly", "right now", "at once", "move!", "run!",
        "go!", "now!", "before it's too late", "before it is too late",
    ),
    "threatening": (
        "you will die", "prepare to die", "i will kill", "we will kill",
        "destroy you", "crush you", "tear you apart", "your doom",
        "your end", "kneel", "bow before", "you cannot escape",
    ),
    "afraid": (
        "help me", "help us", "save me", "save us", "please help",
        "we're doomed", "we are doomed", "i'm afraid", "i am afraid",
        "terrified", "don't leave me", "do not leave me", "no, no",
    ),
    "sorrowful": (
        "i'm sorry", "i am sorry", "forgive me", "we lost", "i lost",
        "has died", "is dead", "mourning", "farewell", "goodbye",
        "rest in peace", "i miss", "grief", "tragedy", "sacrifice",
    ),
    "joyful": (
        "wonderful", "excellent", "fantastic", "victory", "we did it",
        "you did it", "hooray", "rejoice", "celebrate", "ha ha",
        "hahaha", "glorious!",
    ),
    "warm": (
        "welcome", "thank you", "thanks", "good to see you", "my friend",
        "well met", "glad to see", "safe travels", "be well",
    ),
    "solemn": (
        "remember", "honor the", "honour the", "rest now", "rest easy",
        "sacred", "ancient oath", "may they", "in memory", "final rites",
    ),
    "commanding": (
        "listen to me", "you must", "you are to", "stand down", "hold!",
        "attack!", "defend", "follow me", "do as i say", "obey",
    ),
}

WORD_WEIGHTS = {
    "angry": {"fool": 2, "coward": 3, "traitor": 3, "hate": 2, "rage": 3, "insolent": 3},
    "urgent": {"hurry": 4, "quick": 2, "quickly": 3, "run": 2, "now": 1, "immediately": 3},
    "threatening": {"die": 2, "death": 1, "kill": 2, "destroy": 2, "crush": 3, "doom": 2, "kneel": 3},
    "afraid": {"help": 2, "afraid": 4, "fear": 2, "terrified": 5, "please": 1, "escape": 1},
    "sorrowful": {"sorry": 3, "lost": 2, "died": 3, "dead": 2, "grief": 4, "farewell": 3, "mourning": 4},
    "joyful": {"wonderful": 3, "excellent": 3, "victory": 3, "rejoice": 4, "celebrate": 3, "glorious": 2},
    "warm": {"welcome": 3, "thanks": 2, "friend": 1, "glad": 2},
    "solemn": {"honor": 2, "honour": 2, "sacred": 3, "oath": 2, "memory": 2},
    "commanding": {"obey": 4, "must": 1, "attack": 2, "defend": 2, "kneel": 1},
}


def _add(scores, cues, emotion, amount, cue):
    if amount <= 0:
        return
    scores[emotion] = scores.get(emotion, 0) + int(amount)
    cues.setdefault(emotion, []).append(str(cue))


def _text_features(text):
    raw = str(text or "")
    lowered = raw.casefold()
    words = re.findall(r"[a-zA-ZÀ-ÖØ-öø-ÿ']+", lowered)
    alpha_words = re.findall(r"\b[A-Za-z]{2,}\b", raw)
    caps = [w for w in alpha_words if len(w) >= 3 and w.isupper()]
    caps_ratio = (len(caps) / len(alpha_words)) if alpha_words else 0.0
    return raw, lowered, words, caps_ratio


def infer_emotion(kind, text, npc_guid=""):
    scores = {name: 0 for name in EMOTION_DELIVERY}
    cues = {name: [] for name in EMOTION_DELIVERY}
    raw, lowered, words, caps_ratio = _text_features(text)
    kind = str(kind or "").casefold()

    # Event context is reliable and should matter before lexical guessing.
    if kind == "monster_yell":
        _add(scores, cues, "urgent", 3, "yell")
        _add(scores, cues, "angry", 2, "yell")
        _add(scores, cues, "commanding", 1, "yell")
    elif kind == "monster_whisper":
        # Hushed delivery is overlaid later; whisper alone is not an emotion.
        _add(scores, cues, "solemn", 1, "whisper")
    elif kind == "reward":
        _add(scores, cues, "warm", 1, "quest reward")
    elif kind in ("greeting", "gossip"):
        _add(scores, cues, "warm", 1, kind)

    # Character context from phase 11. These are weak priors so dialogue text
    # can override them rather than turning every boss into permanent rage.
    try:
        _, _, _, creature_type, class_name, rank, role = voice_profiles.parse_profile_guid(npc_guid)
        archetypes = voice_profiles.profile_archetypes(creature_type, class_name, rank, role)
    except Exception:
        rank, role, archetypes = "", "", []

    rank_token = voice_profiles._normalize_token(rank)
    role_token = voice_profiles._normalize_token(role)
    if rank_token == "worldboss":
        _add(scores, cues, "threatening", 2, "world boss")
        _add(scores, cues, "commanding", 1, "world boss")
    elif rank_token in ("elite", "rareelite"):
        _add(scores, cues, "commanding", 1, rank_token)

    if role_token == "spirithealer":
        _add(scores, cues, "solemn", 2, "spirit healer")
    elif role_token in ("merchant", "auctioneer"):
        _add(scores, cues, "warm", 1, role_token)
    elif role_token == "battlemaster":
        _add(scores, cues, "commanding", 1, "battlemaster")

    if "dark" in archetypes:
        _add(scores, cues, "threatening", 1, "dark archetype")
    if "holy" in archetypes or "mystic" in archetypes:
        _add(scores, cues, "solemn", 1, "spiritual archetype")
    if "warrior" in archetypes or "boss" in archetypes:
        _add(scores, cues, "commanding", 1, "martial archetype")

    # Lexical evidence.
    for emotion, phrases in PHRASES.items():
        for phrase in phrases:
            if phrase in lowered:
                _add(scores, cues, emotion, 4, f"phrase:{phrase}")

    word_counts = {}
    for word in words:
        word_counts[word] = word_counts.get(word, 0) + 1
    for emotion, weights in WORD_WEIGHTS.items():
        for word, weight in weights.items():
            count = word_counts.get(word, 0)
            if count:
                _add(scores, cues, emotion, min(6, weight * count), f"word:{word}")

    # Punctuation and typography provide language-agnostic delivery hints.
    exclamations = raw.count("!")
    questions = raw.count("?")
    if exclamations:
        _add(scores, cues, "urgent", min(3, exclamations), "exclamation")
        if caps_ratio >= 0.35:
            _add(scores, cues, "angry", 2, "caps+exclamation")
    if questions:
        _add(scores, cues, "inquisitive", min(3, questions), "question")
    if "..." in raw or "…" in raw:
        _add(scores, cues, "solemn", 1, "ellipsis")
        _add(scores, cues, "sorrowful", 1, "ellipsis")
    if caps_ratio >= 0.55 and len(raw) >= 8:
        _add(scores, cues, "urgent", 2, "all caps")
        _add(scores, cues, "angry", 1, "all caps")

    best = "neutral"
    best_score = 0
    for emotion in EMOTION_ORDER:
        score = scores.get(emotion, 0)
        if score > best_score:
            best = emotion
            best_score = score

    # Weak priors alone should not force a dramatic performance.
    if best_score < 2:
        best = "neutral"
        best_score = 0

    return EmotionDecision(best, best_score, tuple(cues.get(best, ())))


def delivery_for_segment(kind, text, npc_guid, base_speed, base_pause, baseline=None):
    decision = infer_emotion(kind, text, npc_guid)
    if baseline is not None and decision.score < 3 and baseline.score >= 3:
        decision = baseline

    speed_mul, pause_mul, gain = EMOTION_DELIVERY.get(
        decision.name, EMOTION_DELIVERY["neutral"]
    )

    kind_token = str(kind or "").casefold()
    cues = list(decision.cues)

    # Whisper/yell describe acoustic delivery independently from emotion.
    if kind_token == "monster_whisper":
        speed_mul *= 0.96
        pause_mul *= 1.15
        gain *= 0.72
        cues.append("hushed")
    elif kind_token == "monster_yell":
        speed_mul *= 1.02
        pause_mul *= 0.92
        gain *= 1.03
        cues.append("projected")

    speed = max(0.84, min(1.16, float(base_speed) * speed_mul))
    pause = max(0.03, min(0.40, float(base_pause) * pause_mul))
    gain = max(0.60, min(1.10, float(gain)))

    return DeliveryProfile(
        emotion=decision.name,
        speed=round(speed, 3),
        pause=round(pause, 3),
        gain=round(gain, 3),
        score=decision.score,
        cues=tuple(cues),
    )
