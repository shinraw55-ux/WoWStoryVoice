# WoW Story Voice

Prototype for giving World of Warcraft quest and gossip dialogue local AI-generated voices.

## Components
- `WoWStoryVoice/`: WoW addon. Captures quest/gossip text and exposes a small pixel bridge.
- `Companion/`: Windows companion. Reads the bridge and generates speech locally with Kokoro ONNX.
- GitHub Actions builds the companion into a standalone Windows executable with PyInstaller.

## Current status
Prototype. The Windows build is automated, but the complete WoW -> pixel bridge -> companion -> TTS chain still needs live in-game verification.

## Test
1. Install the addon folder in `World of Warcraft/_retail_/Interface/AddOns/`.
2. Download the Windows artifact from GitHub Actions and extract it.
3. Start `WoWStoryVoice.exe`.
4. In WoW run `/wsv test`.

Long dialogue chunking and production-grade bridge reliability are future work.
