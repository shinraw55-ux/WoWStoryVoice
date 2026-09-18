# WoW Story Voice

WoW Story Voice gives World of Warcraft quest, gossip and NPC dialogue local AI-generated voices.

## Components
- `WoWStoryVoice/`: WoW addon. Captures supported dialogue and exposes the WSV6 pixel bridge.
- `Companion/companion.py`: proven transport/TTS engine used by the GUI runtime.
- `Companion/runtime.py`: v0.8 desktop runtime, speech control, update/version checks, emotion-aware delivery and addon installation helper.
- `Companion/app.py`: Windows GUI and system-tray application.
- `Companion/voice_profiles.py`: profile-aware Kokoro voice selection using NPC sex, race and character metadata.
- `Companion/emotion_profiles.py`: deterministic context/emotion analysis and bounded speech-delivery modulation.
- `Companion/beta_hardening.py`: Phase 13 resilience helpers for bounded queues, atomic audio cache writes, cache/log housekeeping and corrupt-WAV protection.
- `installer/`: Inno Setup definition for the Windows installer.

## Current status — v0.8.0 development branch
The end-to-end chain was live-verified in game on v0.5.0. v0.6.0 added full dialogue chunking, queued playback and persistent NPC voices. v0.7.0 added pacing, broader dialogue capture and protection against subtitle/cinematic-marked Blizzard lines. v0.8.0 implements phases 8–10 without changing the WSV6 transport. The current development head also contains Phase 11 NPC character archetypes, Phase 12 context-aware emotional delivery and Phase 13 beta hardening. The release number has deliberately not been bumped for every incremental development change.

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
13. **Beta hardening** — speech backlog is bounded, WAV generation is atomic, corrupt cached audio is detected and removed, old cache files are pruned, oversized logs are rotated, stale temporary audio is cleaned up and CI performs a multi-message WSV6 soak test with long Unicode dialogue, out-of-order chunks and duplicate packets.

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

## Phase 13 beta hardening
Phase 13 intentionally adds no large user-facing feature. It reduces failure modes before a stable release.

The desktop app now caps the pending speech backlog at 32 messages. During an abnormal event flood, the oldest waiting dialogue is dropped rather than allowing memory use and delay to grow without bound. The currently playing line is not removed by this backlog protection.

Generated WAV cache entries are written to a unique temporary WAV and validated before an atomic replace into the final cache path. If the process is interrupted during generation, the incomplete file is therefore not treated as a normal cache hit. Cached WAVs are validated before playback; an invalid file is removed so a later request can regenerate it.

Startup housekeeping also rotates the main log at 5 MiB, keeps a bounded set of backups, removes stale temporary audio and prunes the oldest WAV cache entries when the cache exceeds its file/size budget. These operations are best-effort and do not prevent startup if housekeeping itself encounters a filesystem error.

CI now includes a transport soak test that repeatedly reassembles hundreds of long UTF-8 messages while receiving chunks in reverse order and with duplicate packets. This is not a substitute for several hours of real WoW questing, but it exercises the reassembler and duplicate handling far more heavily than the normal unit cases.

## Speech engine packaging
The Windows v0.8 development build currently bundles **Chatterbox Turbo** as the supported speech engine. The transport is deliberately independent of the TTS backend, and adapters for Kokoro ONNX and CosyVoice remain in source for future isolated runtimes.

They are **not exposed as selectable engines in the packaged GUI**. This is intentional: current Chatterbox 0.1.7 requires NumPy 1.x on Python 3.11 while current kokoro-onnx 0.6.1 requires NumPy 2.x, so installing both into the same frozen Python environment is an upstream dependency conflict. CosyVoice likewise expects its own upstream runtime/model layout. WoW Story Voice falls back to the bundled Chatterbox engine if an old settings file names an unavailable external engine rather than failing at startup.

Chatterbox voice cloning uses reference WAV files under the local data directory when valid references are present. If no valid reference is available, Chatterbox uses its built-in conditionals and logs that fallback instead of silently claiming a distinct cloned NPC voice.

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
v0.8.0 intentionally keeps WSV6 unchanged. A small periodic `hello|<version>` control message lets the companion verify the addon version without changing the dialogue packet format. NPC voice-profile metadata is appended inside the existing NPC GUID application field and therefore does not change WSV6 framing or decoding. Phases 12 and 13 add no transport fields.

## Regression/build checks
The Windows build runs protocol tests plus package/version, voice-profile, emotion-delivery and beta-hardening tests. Phase 11 regression coverage includes profile parsing, gender-pool enforcement, race styling, class-specific archetypes, interaction-role styling, non-humanoid creature profiles, boss classification, stable NPC identity and replacement of incompatible cached voices.

Phase 12 regression coverage includes neutral dialogue, angry yells, explicit boss threats, sorrowful delivery, whisper attenuation, whole-dialogue context inheritance and hard bounds on speed/pause/gain.

Phase 13 regression coverage includes WAV validation, bounded speech queues, deterministic oldest-first cache pruning, log rotation and the long-message WSV6 soak test. The workflow compiles all companion modules, builds the GUI companion as a PyInstaller `--windowed --onedir` app, packages the addon, builds the Inno Setup installer and emits SHA-256 checksums for the companion EXE and installer.

## Verification state
- v0.5.0 end-to-end transport/audio: live-verified in game.
- Later development code: CI/regression-tested when the corresponding build is green.
- Phase 11 uses Blizzard-exposed unit/interaction metadata rather than name guessing, but the exact amount of class/race/role metadata available varies between NPCs and still requires live in-game observation.
- Phase 12 logic is regression-tested when CI is green; how natural each emotional profile sounds remains a live tuning question and should not be called fully verified until tested during real questing.
- Phase 13 automated soak/resilience checks reduce known failure modes, but the actual beta criterion still requires extended live WoW sessions across reloads, zone changes and ordinary questing.
- New v0.8 GUI, in-game options panel, heartbeat/version warning, addon installer helper and Windows installer still require live testing on the target Windows/WoW installation before being called fully verified.
