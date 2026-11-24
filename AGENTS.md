# AGENTS.md — Almighty Anand Studio

> Multi-agent autonomous video production, monetization, and channel-management engine.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATOR (24/7 CPU VM)                      │
│  e2-medium · asia-south1-a / us-central1-a · ~$25/mo                │
├─────────────┬──────────────┬──────────────┬────────────────────────┤
│ TrendScout  │ ScriptDir.   │ Publisher    │ AnalyticsReviewer      │
│ (hourly)    │ (on-demand)  │ (scheduled)  │ (daily cron)           │
├─────────────┴──────────────┴──────────────┴────────────────────────┤
│ • Redis 7 (Queue & Cache)                                           │
│ • OmniRoute Gateway (:8080) — Free-tier routing + token compression │
│ • FreeLLMAPI (:8081) — Upstream zero-cost LLM aggregator           │
└──────┬──────────────────────────────────────────────────────────────┘
       │ (Woken on-demand by gpu_controller.py)
       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   GPU SPOT VM (on-demand)                           │
│  g2-standard-4 · L4 GPU · Spot · max 4.5 hr/day · ~$55/mo         │
├─────────────┬──────────────┬──────────────────────────────────────┤
│ VoiceStudio │ Open-Gen-AI  │ Maestro                              │
│ (:8090)     │ (:8091)      │ (:8092)                              │
│ Voice/Dubs  │ B-roll Gen   │ Music & Scoring                      │
└─────────────┴──────────────┴──────────────────────────────────────┘
```

## Agent Registry & CLI Entrypoints

| Agent | Module | CLI Entrypoint | Schedule | Primary Responsibilities |
| :--- | :--- | :--- | :--- | :--- |
| **TrendScout** | `agents.trend_scout` | `python3 -m agents.trend_scout --dry-run` | Every 2h | Scrapes trending videos via yt-dlp & RSS; scores by virality × feasibility × channel fit. |
| **ScriptDirector** | `agents.script_director` | `python3 -m agents.script_director --dry-run` | On trigger | Formulates transformative 16:9 + 2–3 vertical 9:16 scripts using `visual-skills` shot cards; enforces >70% visual transformation. |
| **MediaDispatcher** | `agents.media_dispatcher` | `python3 -m agents.media_dispatcher --dry-run` | On trigger | Dispatches jobs to VoiceStudio (XTTS/dubs), Open-Gen-AI (B-roll), and Maestro (BGM); manages GPU wake/sleep. |
| **EditorFFmpeg** | `agents.editor_ffmpeg` | `python3 -m agents.editor_ffmpeg --dry-run` | On trigger | Enforces anti-reused content policy; applies -16.0 LUFS audio normalization, burnt-in captions & suspense endings. |
| **Publisher** | `agents.publisher` | `python3 -m agents.publisher --dry-run` | Scheduled | Uploads 16:9 & 9:16 to YouTube; schedules Hindi twin channel; auto-injects tracked affiliate links & pinned comments. |
| **AnalyticsReviewer** | `agents.analytics_reviewer` | `python3 -m agents.analytics_reviewer --dry-run` | Daily 06:00 | Audits audience retention & CTR; updates prompt weights; enforces $100/mo budget guardrail. |
| **Orchestrator** | `agents.orchestrator` | `python3 -m agents.orchestrator --dry-run` | Master | Runs the complete 6-stage autonomous production cycle end-to-end. |

## Budget Guardrails ($250 / 2.5-Month Total | $100/mo Cap)

| Resource | Monthly Cap | Daily Soft Limit | Hard Kill Trigger |
| :--- | :--- | :--- | :--- |
| **CPU VM (e2-medium)** | $25 – $29 | — | Never (always-on orchestrator) |
| **GPU Spot (g2-std-4)** | $55 | 4.5 hrs/day | Auto-stop on 5m idle or 4.5h daily limit |
| **LLM APIs (OmniRoute)** | $0.00 | $0.50 | Free-tier fallback chains (Gemini Flash → Groq Llama 3.3) |
| **Storage (GCS + disks)** | $10 | — | 30-day lifecycle auto-delete on raw clips |
| **Safety Reserve** | $10 | — | Soft-pause non-essential jobs if burn rate > $3.33/day |
| **Total** | **~$100/mo** | **~$3.33/day** | **Hard Cap ($250 Total across 75 days)** |

## Anti-Demonetization Policy (Step 4 Rules)

1. **No Reused Content**: Never do raw cut-and-reupload of trending videos.
2. **Mandatory Visual Transformation**: Custom B-roll & animations must replace >70% of video duration (`MIN_VISUAL_TRANSFORMATION_RATIO = 0.75`).
3. **Dual-Format Output**: Every 16:9 breakdown automatically generates 2–3 vertical 9:16 Shorts/Reels with suspense endings.
4. **Day-1 Revenue**: Tracked affiliate links (AI tools, SaaS, finance) injected into descriptions and pinned comments.
