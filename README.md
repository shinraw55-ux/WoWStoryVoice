# WoW Story Voice

WoW Story Voice gives World of Warcraft quest and gossip dialogue local AI-generated voices.

## Components
- `WoWStoryVoice/`: WoW addon. Captures quest/gossip text and exposes the WSV pixel bridge.
- `Companion/`: Windows companion. Reads the bridge, reassembles dialogue, queues speech and generates voices locally with Kokoro ONNX.
- GitHub Actions builds a standalone Windows companion folder with PyInstaller.

## Current status — v0.6.0
The end-to-end chain has been live-verified in game: WoW addon -> pixel bridge -> Windows companion -> Kokoro -> Windows audio.

v0.6.0 combines phases 1–4:

1. **Stable communication** — keeps the live-verified binary RGB bridge and adds transient-read tolerance.
2. **Full dialogue transport** — WSV6 chunks long UTF-8 dialogue into numbered packets, repeats them for loss recovery and reassembles before decoding.
3. **Speech queue** — capture continues while TTS runs on a worker thread; dialogue plays sequentially instead of interrupting itself. `/wsv stop` stops audio and clears the queue.
4. **Persistent NPC voices** — NPCs receive a deterministic voice that is stored in `%LOCALAPPDATA%\WoWStoryVoice\voice-map.json`. Creature GUIDs are normalized to NPC template IDs so respawns keep the same voice.

Generated WAV files are cached under `%LOCALAPPDATA%\WoWStoryVoice\cache`.

## Install / test
1. Install or replace the `WoWStoryVoice` addon folder in `World of Warcraft/_retail_/Interface/AddOns/`.
2. Download the latest `WoWStoryVoice-Windows` artifact from GitHub Actions and extract the whole companion folder.
3. Start `WoWStoryVoice.exe` from that folder.
4. In WoW run `/reload` after replacing addon files, then run `/wsv test`.
5. `/wsv stop` stops current playback and clears queued speech.

Addon and companion must be from the same WSV6/v0.6.0 package.

## Transport notes
WSV6 retains the binary RGB physical-pixel encoding that was verified working in v0.5.0. The application packet now contains a 16-bit message ID, chunk index/total, data length and checksum. Raw UTF-8 bytes are chunked and only decoded after complete reassembly, so a multi-byte character can span chunks safely.

## Regression checks
The Windows build runs protocol tests before packaging. Tests cover multi-chunk UTF-8 reassembly, checksum rejection, duplicate message suppression and stable Creature/NPC voice identity.
