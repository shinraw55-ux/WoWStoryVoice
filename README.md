# WoW Story Voice

WoW Story Voice gives World of Warcraft quest, gossip and NPC dialogue local AI-generated voices.

## Components
- `WoWStoryVoice/`: WoW addon. Captures supported dialogue and exposes the WSV pixel bridge.
- `Companion/`: Windows companion. Reads the bridge, reassembles dialogue, queues speech and generates voices locally with Kokoro ONNX.
- GitHub Actions builds a standalone Windows companion folder with PyInstaller.

## Current status — v0.7.0
The end-to-end chain was live-verified in game on v0.5.0. v0.6.0 added full dialogue chunking, queued playback and persistent NPC voices. v0.7.0 continues with phases 5–7 without changing the WSV6 transport.

### Implemented phases
1. **Stable communication** — binary RGB bridge with transient-read tolerance.
2. **Full dialogue transport** — WSV6 chunks long UTF-8 dialogue into numbered packets, repeats them for loss recovery and reassembles before decoding.
3. **Speech queue** — capture continues while TTS runs on a worker thread; dialogue plays sequentially. `/wsv stop` stops audio and clears the queue.
4. **Persistent NPC voices** — NPCs receive a deterministic voice stored in `%LOCALAPPDATA%\WoWStoryVoice\voice-map.json`. Creature GUIDs are normalized to NPC template IDs so respawns keep the same voice.
5. **Pacing / prosody pass** — WoW markup is removed before speech, long dialogue is split at sentence boundaries, and punctuation/kind apply conservative speed and pause adjustments instead of feeding huge blocks to Kokoro.
6. **Expanded WoW dialogue coverage** — captures quest detail, quest progress, quest reward, multi-quest greeting, gossip, and ambient NPC say/yell/whisper/party dialogue.
7. **Original Blizzard presentation protection** — ambient NPC chat lines marked by Blizzard as subtitles or letterbox/cinematic text are skipped by default so local TTS does not deliberately speak over those lines. This can be changed with `/wsv blizzard off`.

Generated WAV files are cached under `%LOCALAPPDATA%\WoWStoryVoice\cache`.

## Install / test
1. Install or replace the `WoWStoryVoice` addon folder in `World of Warcraft/_retail_/Interface/AddOns/`.
2. Download the latest `WoWStoryVoice-Windows` artifact from GitHub Actions and extract the whole companion folder.
3. Start `WoWStoryVoice.exe` from that folder.
4. In WoW run `/reload` after replacing addon files, then run `/wsv test`.
5. `/wsv stop` stops current playback and clears queued speech.
6. `/wsv status` shows addon settings.

### Addon commands
- `/wsv test`
- `/wsv stop`
- `/wsv status`
- `/wsv blizzard on|off` — skip Blizzard subtitle/cinematic-marked NPC lines when ON (default).
- `/wsv monsters on|off` — enable/disable ambient NPC say/yell/whisper/party capture.
- `/wsv show` / `/wsv hide`

## Transport notes
v0.7.0 intentionally keeps WSV6 unchanged. The packet contains a 16-bit message ID, chunk index/total, data length and checksum. Raw UTF-8 bytes are chunked and decoded only after complete reassembly.

## Regression checks
The Windows build runs protocol tests before packaging. Tests cover multi-chunk UTF-8 reassembly, checksum rejection, message-ID dedupe, cross-source dialogue dedupe, stable Creature/NPC voice identity, WoW markup cleanup, bounded sentence segmentation and conservative prosody values.

## Verification state
- v0.5.0 end-to-end transport/audio: live-verified in game.
- v0.6.0/v0.7.0 code: CI/regression-tested when the corresponding build is green.
- New v0.7.0 dialogue events and subtitle-skip behavior still require live in-game verification before being called fully verified.
