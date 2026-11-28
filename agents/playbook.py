#!/usr/bin/env python3
"""
playbook.py — Viral playbook loader with a runtime overlay.

`configs/viral_playbook.json` is the hand-edited, git-tracked baseline and is
never written at runtime. What the analytics loop learns (hook weights, hook
cut speed, script length, voice rate and pace) is stored in `memory/playbook_state.json` and merged
over the baseline on load. Delete that file to reset the learning.
"""

import copy
import json
import logging
import os
from pathlib import Path
from typing import Optional

from agents.settings import STUDIO_ROOT

log = logging.getLogger("playbook")

BASE_PATH = STUDIO_ROOT / "configs" / "viral_playbook.json"
STATE_PATH = STUDIO_ROOT / "memory" / "playbook_state.json"

LEARNED_PILLAR_FIELDS = ("hook_priors", "hook_cut_seconds", "script_words_target")
LEARNED_VOICE_FIELDS = ("rate", "pace")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning(f"Could not read {path.name}: {e}")
        return {}


def load_playbook(base_path: Optional[Path] = None, state_path: Optional[Path] = None) -> dict:
    """Baseline playbook with learned values merged on top."""
    base = _read_json(base_path or BASE_PATH)
    playbook = copy.deepcopy(base)
    playbook.setdefault("pillars", {})
    playbook.setdefault("character_voices", {})
    state = _read_json(state_path or STATE_PATH)

    for name, learned in state.get("pillars", {}).items():
        pillar = playbook["pillars"].get(name)
        if pillar is None:
            continue
        for key in LEARNED_PILLAR_FIELDS:
            if key in learned:
                pillar[key] = copy.deepcopy(learned[key])

    for name, learned in state.get("character_voices", {}).items():
        voice = playbook["character_voices"].get(name)
        if voice is None:
            continue
        for key in LEARNED_VOICE_FIELDS:
            if key in learned:
                voice[key] = learned[key]

    return playbook


def save_learned_state(playbook: dict, state_path: Optional[Path] = None) -> Path:
    """Persist only the learned fields of `playbook` to the runtime overlay."""
    path = state_path or STATE_PATH
    state = {"pillars": {}, "character_voices": {}}
    for name, pillar in playbook.get("pillars", {}).items():
        state["pillars"][name] = {k: pillar[k] for k in LEARNED_PILLAR_FIELDS if k in pillar}
    for name, voice in playbook.get("character_voices", {}).items():
        state["character_voices"][name] = {k: voice[k] for k in LEARNED_VOICE_FIELDS if k in voice}

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path
