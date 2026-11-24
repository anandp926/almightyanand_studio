# AGENTS.md — Almighty Anand Studio

> Multi-agent autonomous video production, monetization, and channel-management engine.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATOR (24/7 CPU VM)                      │
│  e2-medium · us-central1-a · always-on · ~$25/mo                   │
├─────────────┬──────────────┬──────────────┬────────────────────────┤
│ TrendScout  │ ScriptDir.   │ Publisher    │ AnalyticsReviewer      │
│ (hourly)    │ (on-demand)  │ (scheduled)  │ (daily cron)           │
└──────┬──────┴──────┬───────┴──────┬───────┴──────┬─────────────────┘
       │             │              │              │
       ▼             ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   GPU SPOT VM (on-demand)                           │
│  g2-standard-4 · L4 GPU · Spot · max 4.5 hr/day · ~$40/mo         │
├─────────────┬──────────────┬──────────────────────────────────────┤
│ VoiceStudio │ Open-Gen-AI  │ Maestro (music)                      │
│ (TTS/clone) │ (B-roll gen) │ (BGM generation)                     │
└─────────────┴──────────────┴──────────────────────────────────────┘
```

## Agent Registry

| Agent                | File                          | Schedule      | Runs On | Purpose                                                    |
| -------------------- | ----------------------------- | ------------- | ------- | ---------------------------------------------------------- |
| **TrendScout**       | `agents/trend_scout.py`       | Every 2 hours | CPU VM  | Scrapes trending topics via yt-dlp, RSS, Google Trends     |
| **ScriptDirector**   | `agents/script_director.py`   | On trigger    | CPU VM  | Writes transformative 16:9 + 9:16 scripts via LLM          |
| **MediaDispatcher**  | `agents/media_dispatcher.py`  | On trigger    | CPU VM  | Dispatches voice, B-roll, and music jobs to GPU VM          |
| **EditorFFmpeg**     | `agents/editor_ffmpeg.py`     | On trigger    | GPU VM  | Assembles final renders with captions, hooks, transitions   |
| **Publisher**        | `agents/publisher.py`         | Scheduled     | CPU VM  | Uploads to YouTube & Instagram, manages SEO & affiliates    |
| **AnalyticsReviewer**| `agents/analytics_reviewer.py`| Daily 06:00   | CPU VM  | Pulls retention/CTR, updates prompt weights, generates logs |

## Budget Guardrails

| Resource              | Monthly Cap | Daily Soft Limit | Hard Kill Trigger       |
| --------------------- | ----------- | ---------------- | ----------------------- |
| CPU VM (e2-medium)    | $25         | —                | Never (always-on)       |
| GPU Spot (g2-std-4)   | $40         | 4.5 hrs          | Auto-stop after idle 5m |
| LLM API (OmniRoute)   | $15         | $0.50            | Fallback to free tiers  |
| Storage (GCS + disks) | $10         | —                | Lifecycle 30d delete    |
| Networking / Misc     | $10         | —                | —                       |
| **Total**             | **$100**    |                  |                         |

## Skill Reference

This project uses the **Autonomous Director** skill registered at:
- [`.agy/skills/almighty_anand_director.md`](.agy/skills/almighty_anand_director.md)

## Quick Start

```bash
# 1. Copy and fill environment variables
cp .env.example .env && nano .env

# 2. Provision GCP infrastructure
bash infra/gcp_setup.sh

# 3. Launch CPU stack (always-on)
docker compose up -d

# 4. GPU stack starts automatically via gpu_controller.py when jobs are queued
```
