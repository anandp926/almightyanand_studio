# Background music

Put licensed loops here. One is picked per video and ducked under the voice.

```
assets/bgm/urgent/    finance pillar   (tense, fast)
assets/bgm/curious/   technology pillar (light, upbeat)
assets/bgm/calm/      mythic pillar    (slow, warm)
```

- A pillar's mood is `bgm_mood` in `configs/viral_playbook.json`; its level is `bgm_volume`.
- Files placed directly in `assets/bgm/` are used for any mood that has no folder of its own.
- Formats: mp3, wav, m4a, ogg, flac. Loops of 20 seconds or more work best.
- Use music you are allowed to monetise, for example from the YouTube Audio Library. Keep a note of each track's licence.
- With no files, videos have no music. `python -m agents.audio_design --make-placeholder-beds` writes a plain synthetic pad per mood as a stopgap.

Sound effects: a whoosh on cuts and a hit on the reveal are synthesized automatically. To use your own, add `assets/sfx/whoosh.wav` and `assets/sfx/hit.wav`. `SFX_ENABLED=false` turns them off.
