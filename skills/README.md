# Visual Skills — Almighty Anand Custom Templates

This directory contains cloned templates from `smixs/visual-skills` plus custom
Almighty Anand templates for katha, tech explainer, and finance video formats.

## Template Categories

### Standard Templates (from visual-skills)
- `explainer_16x9.yaml` — Tech explainer with screen recordings
- `storytelling_16x9.yaml` — Narrative arc with B-roll sequences
- `listicle_16x9.yaml` — Numbered list format with transitions
- `shorts_hook_first.yaml` — 9:16 hook-first vertical format

### Custom Almighty Anand Templates
- `katha_epic.yaml` — Mythological storytelling with animated backgrounds
- `tech_breakdown.yaml` — Product/tech deep-dive with spec overlays
- `finance_explained.yaml` — Financial concepts with chart animations
- `life_hack_quick.yaml` — Rapid-fire life hack format

## Usage

Templates are referenced by `script_director.py` when generating shot lists
and B-roll cues. Each template defines:

```yaml
name: template_name
format: "16:9" | "9:16"
sections:
  - name: hook
    duration: 30
    visual_type: face_cam | broll | text_overlay
    transitions: [cut, fade, zoom]
  - name: body
    # ...
```

## Setup

```bash
# Clone visual-skills templates
git clone https://github.com/smixs/visual-skills.git /tmp/vs
cp -r /tmp/vs/templates/* ./

# Custom templates are already included in this repo
```
