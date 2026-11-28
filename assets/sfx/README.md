# Sound effects

Optional overrides for the built-in effects:

- `whoosh.wav` (or .mp3): played just before each scene cut.
- `hit.wav` (or .mp3): played where the reveal line starts.

Without these files, simple effects are synthesized once into `data/sfx_generated/`.
Levels: `SFX_WHOOSH_VOLUME` (default 0.22) and `SFX_HIT_VOLUME` (default 0.55) in `.env`.
