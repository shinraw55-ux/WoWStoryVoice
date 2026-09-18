# WoW Story Voice

WoW Story Voice gives World of Warcraft quest, gossip and NPC dialogue local AI-generated voices.

## Components
- `WoWStoryVoice/`: WoW addon. Captures supported dialogue and exposes the WSV6 pixel bridge.
- `Companion/companion.py`: proven transport/TTS engine used by the GUI runtime.
- `Companion/runtime.py`: v0.8 desktop runtime, speech control, update/version checks and addon installation helper.
- `Companion/app.py`: Windows GUI and system-tray application.
- `installer/`: Inno Setup definition for the Windows installer.

## Current status — v0.8.0
The end-to-end chain was live-verified in game on v0.5.0. v0.6.0 added full dialogue chunking, queued playback and persistent NPC voices. v0.7.0 added pacing, broader dialogue capture and protection against subtitle/cinematic-marked Blizzard lines. v0.8.0 implements phases 8–10 without changing the WSV6 transport.

### Implemented phases
1. **Stable communication** — binary RGB bridge with transient-read tolerance.
2. **Full dialogue transport** — WSV6 chunks long UTF-8 dialogue into numbered packets and reassembles it before speech.
3. **Speech queue** — dialogue plays sequentially and `/wsv stop` clears current/queued speech.
4. **Persistent NPC voices** — stable NPC voice mapping stored under `%LOCALAPPDATA%\WoWStoryVoice`.
5. **Pacing / prosody** — WoW markup cleanup, sentence segmentation and conservative pacing changes.
6. **Expanded dialogue coverage** — quest detail/progress/reward/greeting, gossip and ambient NPC say/yell/whisper/party.
7. **Blizzard presentation protection** — subtitle/cinematic-marked ambient NPC lines are skipped by default.
8. **Companion UX** — Windows GUI, bridge/addon/speech status, recent log view, pause listening, stop speech, local voice test, volume, system tray, optional Windows startup and update checking.
9. **Addon UX** — `/wsv config` opens an in-game options panel for quest, gossip, ambient NPC and Blizzard-line behavior. The existing slash commands remain available.
10. **Installation/update** — the build produces a Windows installer, companion/addon version heartbeat detects mismatches, the GUI can install/update the bundled addon, and `release.json` provides a lightweight update check.

Generated WAV files and logs are stored under `%LOCALAPPDATA%\WoWStoryVoice`.

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
v0.8.0 intentionally keeps WSV6 unchanged. A small periodic `hello|<version>` control message lets the companion verify the addon version without changing the dialogue packet format.

## Regression/build checks
The Windows build runs protocol tests plus v0.8 package/version synchronization tests, compiles all Python modules, builds the GUI companion as a PyInstaller `--windowed --onedir` app, packages the addon, builds the Inno Setup installer and emits SHA-256 checksums for the companion EXE and installer.

## Verification state
- v0.5.0 end-to-end transport/audio: live-verified in game.
- v0.6.0/v0.7.0/v0.8.0 code: CI/regression-tested when the corresponding build is green.
- New v0.8 GUI, in-game options panel, heartbeat/version warning, addon installer helper and Windows installer still require live testing on the target Windows/WoW installation before being called fully verified.
