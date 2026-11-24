# 🎬 Almighty Anand Studio (`almightyanand_studio`)

> **Autonomous 24/7 Multi-Agent Video Production, Monetization & Channel-Management Engine**  
> Operating on a strict **$250 / 2.5-month budget ($100/month cap)** across YouTube (English & Hindi Twin) and local/cloud compute.

---

## 🏛 Architecture Overview

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                      ALWAYS-ON CPU ORCHESTRATOR (24/7 Stack)                      │
│                e2-medium (2 vCPU, 4 GB RAM) · ~$25/month · Mumbai / US            │
├────────────────────┬────────────────────┬───────────────────┬─────────────────────┤
│     CEOPlanner     │     TrendScout     │  ScriptDirector   │  AnalyticsReviewer  │
│ (Goal & Pivots)    │ (Live RSS Scout)   │(Hinglish & Cards) │ (Retention & CTR    │
│                    │                    │                   │  Feedback Loop)     │
├────────────────────┴────────────────────┴───────────────────┴─────────────────────┤
│ • MemoryStore (SQLite: `memory/published_topics.db` — 100% Zero Topic Duplication)│
│ • Redis 7 (Job Queue & State)                                                     │
│ • OmniRoute Gateway (:8080) — Free-tier routing + token compression               │
│ • FreeLLMAPI (:8081) — Upstream zero-cost LLM aggregator (Groq 120B / Gemini)     │
└─────────────────────────────────────────┬─────────────────────────────────────────┘
                                          │ (Woken on-demand by gpu_controller.py)
                                          ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│                       ON-DEMAND SPOT GPU WORKER (Spot Stack)                      │
│           g2-standard-4 (1x NVIDIA L4 24GB) · ~$0.39–0.42/hr · Max 4.5h/day       │
├─────────────────────────┬─────────────────────────┬───────────────────────────────┤
│       VoiceStudio       │       Open-Gen-AI       │            Maestro            │
│         (:8090)         │         (:8091)         │            (:8092)            │
│  Anand Voice Clone &    │  Diffusion B-Roll,      │   Dynamic Soundtrack &        │
│  Hindi Auto-Dubbing     │  Lip-Sync & Visuals     │   Audio Scoring Sequences     │
└─────────────────────────┴─────────────────────────┴───────────────────────────────┘
```

---

## 👑 Autonomous Goal-Driven Control Loop (`ceo_planner.py`)

Almighty Anand Studio runs autonomously with **zero human babysitting**:
- **North Star Goal**: Maximize views, subscriber growth, average view duration (**AVD > 75% on Shorts**, **> 45% on long-form**), and monetization (YPP + affiliate revenue) within the $250 / 2.5-month GCP budget ($100/mo cap).
- **Persistent Memory & Zero Duplication (`memory/published_topics.db`)**: Every live candidate is hashed and checked against SQLite using Jaccard token similarity. Old topics are never recycled.
- **Strict Hinglish Mandate**: High-energy conversational Indian Hinglish (e.g., *"Bhai, kya aapko pata hai..."*, *"Agar aapka bank account freeze ho gaya to..."*). No corporate English or textbook translations.
- **Safe-Zone Burnt-In Subtitles**: Lower-middle frame height (`y = 78%` to `81%`, `MarginV=360`), avoiding collisions with character faces, top headers, or bottom platform UI buttons.
- **Zero Tofu Box (`□`) Fix**: Automatic sanitization of `₹` to `Rs. ` and stripping unsupported emojis before FFmpeg font rendering.
- **Autonomous 3-Day Pivoting**: If a visual style underperforms (<65% AVD) for 3 consecutive days, `CEOPlanner` automatically pivots between styles (`commentary_broll` ➔ `origami_breakdown` ➔ `retro_pixel_art` ➔ `anime_epic`).

---

## 💰 $250 / 2.5-Month Budget Guardrails ($100/mo Cap)

| Component | Resource | Schedule | Est. Monthly Cost | 2.5-Month Total | Guardrail Mechanism |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **24/7 Orchestrator** | `e2-medium` (2 vCPU, 4GB RAM) | Always-on (730 hrs/mo) | ~$25 – $29 | ~$62.50 – $72.50 | Fixed CPU tier, zero GPU idle waste |
| **GPU Render Worker** | `g2-standard-4 Spot` (NVIDIA L4) | Batched ~4.5 hrs/day | ~$55 | ~$137.50 | Hard kill-switch at 4.5 hrs/day; auto-stops on 5m idle |
| **Storage & Egress** | 100 GB PD + GCS Bucket | 24/7 | ~$10 | ~$25.00 | 30-day lifecycle auto-cleanup on raw intermediate clips |
| **LLM Inference** | OmniRoute + FreeLLMAPI | Real-time | **$0.00** | **$0.00** | Free Groq 120B (`openai/gpt-oss-120b`) + Gemini Flash fallbacks |
| **Emergency Buffer** | Billing Alert + Soft Pause | On-demand | ~$10 | ~$15.00 – $25.00 | Soft-pause non-essential jobs if burn rate > $3.33/day |
| **Total** | | | **~$100/mo** | **~$250.00** | **Strict Hard Cap** |

---

## 📈 Monetization Roadmap & Break-Even Timeline

```
Day 1 ──────────► Day 14 ──────────► Day 35 ──────────► Day 50 ──────────► Day 75
[Pipeline Launch]  [First Affil. $]  [YPP 500 Subs]    [AdSense Unlocked] [Self-Sustaining]
Spend: $0          Spend: ~$45       Spend: ~$115      Spend: ~$165       Spend: $250
Rev: $0            Rev: ~$15–$30     Rev: ~$80–$120    Rev: ~$200–$300    Rev: $450–$750+
```

1. **Days 1–14 (Immediate Affiliate Revenue)**: Every video injects tracked affiliate links (AI tools, SaaS, finance apps) and pinned comments. Converts view #1 into revenue without waiting for 1,000 subscribers.
2. **Days 15–35 (Shorts Velocity & Fan Funding)**: Velocity of 3–4 Shorts/day triggers YouTube algorithmic reach; unlocks YPP Tier 1 (500 subs).
3. **Days 36–50 (Official YouTube AdSense Approved)**: Clears 1,000 subscribers and 4,000 watch hours; video ad revenue begins compounding ($2.50–$7.00 RPM in tech/finance).
4. **Days 50–65 (Full Investment Break-Even)**: Cumulative earnings cross $250. The channel self-funds its GCP cloud bills permanently.

---

## 🧩 Integrated GitHub Repositories

| Repository | Purpose in Studio | Integration Details |
| :--- | :--- | :--- |
| **[cheaper-inference/OmniRoute](https://github.com/cheaper-inference/OmniRoute)** | Primary LLM Router | Wired as `omniroute` service (:8080) in `docker-compose.yml`. Configured with token compression rules and zero-cost free-tier routing. |
| **[tashfeenahmed/freellmapi](https://github.com/tashfeenahmed/freellmapi)** | Free LLM Aggregator | Wired as `freellm` service (:8081) in `docker-compose.yml`. Acts as OmniRoute's upstream fallback before any paid endpoints. |
| **[smixs/visual-skills](https://github.com/smixs/visual-skills)** | Cinematography Prompt Guides | Loaded in `skills/visual-skills/` + custom templates in `skills/templates/` (`anime_epic.yaml`, `retro_pixel_art.yaml`, `origami_breakdown.yaml`, `commentary_broll.yaml`). |
| **[debpalash/voicestudio](https://github.com/debpalash/voicestudio)** | Voice Cloning & Dubbing | Wired as `voicestudio` (:8090) in `docker-compose.gpu.yml`. Clones Anand's voice profile and generates auto-Hindi dubs. |
| **[anil-matcha/open-generative-ai](https://github.com/anil-matcha/open-generative-ai)** | Visual & Lip-Sync B-Roll | Wired as `opengenai` (:8091) in `docker-compose.gpu.yml`. Synthesizes custom B-roll replacing >75% of trending video visuals. |
| **[Blizaine/Maestro](https://github.com/Blizaine/Maestro)** | AI Background Music | Wired as `maestro` (:8092) in `docker-compose.gpu.yml`. Composes royalty-free background soundtracks ducked at -16.0 LUFS. |

---

## 🚀 24/7 Autonomous Daemon & GCP Production Deployment

### 1. Single Command GCP VM Deployment (100% Autonomous 24/7 Stack)
Run this single command on your GCP CPU VM (`e2-medium`) to clone the repository, boot all background services (`OmniRoute`, `FreeLLMAPI`, `Redis`), and launch the self-healing production orchestrator:

```bash
git clone https://github.com/anandp926/almightyanand_studio.git ~/almightyanand_studio && \
cd ~/almightyanand_studio && \
cp .env.example .env && \
docker compose up -d --build && \
echo "✅ Almighty Anand Studio 24/7 autonomous stack is live!"
```

---

### 2. Autonomous 24/7 Daily Schedule (IST)

The daemon runs continuously with `restart: unless-stopped` self-healing:

| Time (IST) | Agent Action | Autonomous Output |
| :--- | :--- | :--- |
| **06:00 IST** | `TrendScout` + `CEOPlanner` | Scrapes live India/Global trends; verifies against `memory/published_topics.db` to guarantee 100% fresh topics. |
| **07:00 IST** | `ScriptDirector` + `MediaDispatcher` + `EditorFFmpeg` | Formulates fact-checked Hinglish script package, generates 100% AI/royalty-free visuals (Pollinations FLUX / Open-Gen-AI / Pexels), renders vertical Short with zoompan & safe-zone captions, and executes `QAJudge` (auto-retries up to 2x on watermark/relevance failure). |
| **12:00 IST** | `Publisher` | Releases **Lunch Peak** vertical Short with tracked affiliate links & pinned comments. |
| **18:00 IST** | `Publisher` | Releases **Commute Peak** long-form (16:9) deep dive. |
| **21:00 IST** | `Publisher` | Releases **Prime Time** Short #2 / Hindi Twin Dubbed release. |
| **23:30 IST** | `AnalyticsReviewer` | Audits view duration, CTR & retention metrics; updates tomorrow's prompt weights and records budget burn. |

---

### 3. CLI Commands & Local Testing

```bash
# 1. Run 1 live production cycle immediately (generates video + runs QA Judge)
python3 -m agents.orchestrator --run-once

# 2. Run the 24/7 self-healing scheduler daemon
python3 -m agents.orchestrator --daemon

# 3. Test OmniRoute endpoints
curl http://localhost:8080/health
curl http://localhost:8080/v1/models

# 4. Plan and render today's short manually
python3 -m agents.ceo_planner --render-short
```
