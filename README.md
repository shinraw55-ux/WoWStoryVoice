# WoW Story Voice

WoW Story Voice gives World of Warcraft quest, gossip and NPC dialogue local AI-generated voices.

## Components
- `WoWStoryVoice/`: WoW addon. Captures supported dialogue and exposes the WSV6 pixel bridge.
- `Companion/companion.py`: proven transport/TTS engine used by the GUI runtime.
- `Companion/runtime.py`: v0.8 desktop runtime, speech control, update/version checks, emotion-aware delivery and addon installation helper.
- `Companion/app.py`: Windows GUI and system-tray application.
- `Companion/voice_profiles.py`: profile-aware Kokoro voice selection using NPC sex, race and character metadata.
- `Companion/emotion_profiles.py`: deterministic context/emotion analysis and bounded speech-delivery modulation.
- `installer/`: Inno Setup definition for the Windows installer.

## Current status — v0.8.0 development branch
The end-to-end chain was live-verified in game on v0.5.0. v0.6.0 added full dialogue chunking, queued playback and persistent NPC voices. v0.7.0 added pacing, broader dialogue capture and protection against subtitle/cinematic-marked Blizzard lines. v0.8.0 implements phases 8–10 without changing the WSV6 transport. The current development head also contains Phase 11 NPC character archetypes and Phase 12 context-aware emotional delivery; the release number has deliberately not been bumped for every incremental development change.

### Implemented phases
1. **Stable communication** — binary RGB bridge with transient-read tolerance.
2. **Full dialogue transport** — WSV6 chunks long UTF-8 dialogue into numbered packets and reassembles it before speech.
3. **Speech queue** — dialogue plays sequentially and `/wsv stop` clears current/queued speech.
4. **Persistent NPC voices** — stable NPC voice mapping stored under `%LOCALAPPDATA%\WoWStoryVoice`. When WoW exposes NPC metadata, known sex restricts selection to matching male/female Kokoro voices and race selects broad curated race-style voice pools.
5. **Pacing / prosody** — WoW markup cleanup, sentence segmentation and conservative pacing changes.
6. **Expanded dialogue coverage** — quest detail/progress/reward/greeting, gossip and ambient NPC say/yell/whisper/party.
7. **Blizzard presentation protection** — subtitle/cinematic-marked ambient NPC lines are skipped by default.
8. **Companion UX** — Windows GUI, bridge/addon/speech status, recent log view, pause listening, stop speech, local voice test, volume, system tray, optional Windows startup and update checking.
9. **Addon UX** — `/wsv config` opens an in-game options panel for quest, gossip, ambient NPC and Blizzard-line behavior. The existing slash commands remain available.
10. **Installation/update** — the build produces a Windows installer, companion/addon version heartbeat detects mismatches, the GUI can install/update the bundled addon, and `release.json` provides a lightweight update check.
11. **NPC character archetypes** — voice selection also uses class, creature type, unit classification and known interaction role. Examples include martial, holy, mystic, arcane, dark, ranger, rogue, merchant, scholar, formal/banker, artisan, mechanical, dragon, giant/wild and boss-oriented profiles. The profile is still tied to the stable NPC identity so a character does not randomly change voice every time it appears.
12. **Context-aware emotion delivery** — the companion combines dialogue event type, punctuation, strong lexical cues and Phase 11 NPC context into deterministic delivery profiles such as angry, urgent, threatening, afraid, sorrowful, joyful, warm, solemn, inquisitive and commanding. The NPC keeps the same actor/voice; only bounded speed, pauses and relative loudness change.

Generated WAV files and logs are stored under `%LOCALAPPDATA%\WoWStoryVoice`.

## NPC voice profiles
The addon attaches lightweight metadata to the existing NPC identity field; WSV6 framing itself is unchanged. Metadata can include:

- sex from `UnitSex`
- locale-independent race from `UnitRace`
- creature type from `UnitCreatureType`
- class from `UnitClass`
- classification such as normal/elite/worldboss from `UnitClassification`
- known interaction roles such as merchant, trainer, banker, auctioneer, stable master, battlemaster, transmogrifier, profession NPC or forge master

The companion combines race style and character archetype rather than replacing one with the other. For example, an Orc warrior and Orc shaman remain Orc-styled but are no longer drawn from exactly the same preferred voice set. Elite/worldboss classification adds a heavier boss profile. Mechanical, dragonkin, demon/undead, elemental and giant creature types also receive different broad voice preferences when that metadata is available.

The addon caches learned NPC template profiles in `WoWStoryVoiceDB.npcProfiles`, so ambient lines can reuse metadata learned from a prior target, focus, mouseover or direct NPC interaction. Existing cached voice assignments are kept when they remain compatible; an incompatible assignment can be replaced when better metadata becomes available.

These are broad stylistic selections among existing Kokoro voices, not attempts to clone Blizzard voice actors. If WoW does not expose enough metadata, the system falls back safely to the general deterministic voice pool.

## Phase 12 emotion/context system
Phase 12 is companion-side only and does not alter WSV6 or the persistent NPC identity. Each complete dialogue and each spoken segment is analyzed locally.

Reliable context is weighted first. A monster yell contributes urgent/projected delivery, a whisper adds a hushed acoustic overlay, quest rewards can lean warm, and Phase 11 metadata such as world-boss rank, spirit-healer role, martial archetype or dark archetype acts only as a weak prior.

Strong dialogue cues can override those priors. The current deterministic rules recognize explicit threats, anger/insults, panic/help language, grief/farewell language, celebration, greetings/thanks, commands, questions, repeated exclamation marks, ellipses and all-caps emphasis. This is intentionally explainable and local rather than pretending to be a full semantic emotion model.

The current Kokoro path used by WoW Story Voice has no dedicated emotion control in this implementation. Phase 12 therefore keeps the chosen NPC voice unchanged and modifies only safe bounded parameters:

- speech speed
- pause length between segments
- relative loudness/gain
- whisper/yell acoustic delivery overlays

Dialogue text is never rewritten. Strong whole-dialogue context can carry into otherwise neutral short segments so a multi-sentence urgent or sorrowful speech does not jump back to neutral every sentence. The Windows GUI shows the currently detected emotion to make live verification and tuning easier.

## Install
The Windows artifact contains both the portable folder and `WoWStoryVoice-Setup-v0.8.0.exe`.

1. Run the setup EXE, or extract the portable companion folder.
2. Start WoW Story Voice.
3. Use **Install/update addon** in the Windows app. It auto-detects the standard Retail WoW path; if WoW is elsewhere, select `_retail_` or the `AddOns` folder.
4. If WoW is already running, use `/reload`.
5. Run `/wsv test`.

The Windows app shows the detected addon version. If addon and companion versions differ, it displays a version-mismatch warning.

### Addon commands
- `/wsv config` — open the in-game options panel.
- `/wsv test`
- `/wsv stop`
- `/wsv status`
- `/wsv quests on|off`
- `/wsv gossip on|off`
- `/wsv blizzard on|off`
- `/wsv monsters on|off`
- `/wsv show` / `/wsv hide`

## Transport notes
v0.8.0 intentionally keeps WSV6 unchanged. A small periodic `hello|<version>` control message lets the companion verify the addon version without changing the dialogue packet format. NPC voice-profile metadata is appended inside the existing NPC GUID application field and therefore does not change WSV6 framing or decoding. Phase 12 adds no transport fields.

## Regression/build checks
The Windows build runs protocol tests plus package/version, voice-profile and emotion-delivery tests. Phase 11 regression coverage includes profile parsing, gender-pool enforcement, race styling, class-specific archetypes, interaction-role styling, non-humanoid creature profiles, boss classification, stable NPC identity and replacement of incompatible cached voices.

Phase 12 regression coverage includes neutral dialogue, angry yells, explicit boss threats, sorrowful delivery, whisper attenuation, whole-dialogue context inheritance and hard bounds on speed/pause/gain. The workflow also compiles the new emotion module, builds the GUI companion as a PyInstaller `--windowed --onedir` app, packages the addon, builds the Inno Setup installer and emits SHA-256 checksums for the companion EXE and installer.

## Verification state
- v0.5.0 end-to-end transport/audio: live-verified in game.
- Later development code: CI/regression-tested when the corresponding build is green.
- Phase 11 uses Blizzard-exposed unit/interaction metadata rather than name guessing, but the exact amount of class/race/role metadata available varies between NPCs and still requires live in-game observation.
- Phase 12 logic is regression-tested when CI is green; how natural each emotional profile sounds remains a live tuning question and should not be called fully verified until tested during real questing.
- New v0.8 GUI, in-game options panel, heartbeat/version warning, addon installer helper and Windows installer still require live testing on the target Windows/WoW installation before being called fully verified.
