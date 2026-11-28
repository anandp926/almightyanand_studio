# Host character

The host appears in the lower-left of every Short, on top of the moving background.
What it does depends on which files are in this folder.

| Files here | Result |
| :--- | :--- |
| none | `assets/character_ref.png` is shown as a still with a gentle bob. |
| `idle.png`, `talk.png` | The mouth opens and closes with the voice while the main narrator speaks. |
| plus `hook.png`, `reveal.png`, `cta.png`, `take.png` | The pose changes with the scene (opening line, key fact, closing question, your own take). |
| plus `hook_talk.png`, `reveal_talk.png`, `cta_talk.png`, `take_talk.png` | Mouth movement in those poses too. |

## How to render the poses from a 3D model

- Transparent background (PNG with alpha), no shadow on the ground plane.
- Same camera, same framing and same image size in every file, so the character does not jump between poses.
- Waist-up or chest-up works best. The image is scaled to 520 px tall, so about 800 x 1000 px is plenty.
- `idle.png`: neutral, mouth closed. `talk.png`: the same pose with the mouth open. Nothing else should change between the two.
- Suggested extra poses: `hook` (leaning in, eyebrows up), `reveal` (pointing or surprised), `cta` (pointing down at the comments).

The host's name, personality and catchphrase, and the sidekick's, are in `configs/character.json`.
Only the host is drawn; the sidekick is a second voice.

Position and size are in `configs/visual_style.json` under `host_zone`. Every generated image is asked to keep that corner clear.
Set `HOST_ENABLED=false` in `.env` to turn the host off.

Animated clips and true lip-sync are not supported yet; they need the GPU stack.
