# 🎬 Almighty Anand Studio (`almightyanand_studio`)

> **Autonomous 24/7 Multi-Agent Video Production, Monetization & Channel-Management Engine**  
> Operating on a strict **$250 / 2.5-month budget ($100/month cap)** across YouTube (English & Hindi Twin) and local/cloud compute.

## ✅ Implementation Status

What runs today is a CPU-only Shorts pipeline:

`RSS feeds → article fetch → Gemini/Groq script → code fact-check → Pollinations FLUX images → Sarvam or Edge-TTS voice → host + music + effects → FFmpeg → vision QA → YouTube upload`

| Area | Status |
| :--- | :--- |
| Shorts pipeline (scout, script, render, QA, publish, analytics) | **Working** |
| Source grounding and fact-check gate | **Working** (see below) |
| Voice: Sarvam Bulbul from a Devanagari voice track, Edge-TTS fallback, per-scene pace and pauses | **Built, not yet run against the live Sarvam API** |
| Host character overlay (still, or mouth and pose changes from PNG poses) | **Working** with PNG poses; see `assets/character/README.md` |
| Music ducking and sound effects | **Working**; add music to `assets/bgm/` |
| Character-led tech and AI series with four rotating formats, creator take, retention-curve learning, review hold | **Working** |
| Burst mode (VM on about an hour a day) | **Built, not yet run on a VM**; see `infra/burst_mode.md` |
| `OmniRoute` (:8080) and `FreeLLMAPI` (:8081) | In-repo gateways in `services/`, not the upstream projects |
| GPU stack (VoiceStudio, Open-Gen-AI, Maestro) | **Placeholders.** `docker-compose.gpu.yml` starts stub servers; keep `GPU_CONTROLLER_ENABLED=false` |
| Voice cloning, Hindi twin channel, long-form 16:9, Instagram | **Not implemented** |
| `gpu_controller.py` | Present but not called by the pipeline |

The architecture diagram and budget table below describe the target design, including the parts that are not built yet.

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
- **Persistent Memory & De-duplication (`memory/published_topics.db`)**: Every live candidate is checked against SQLite using Jaccard token similarity on the title. This catches repeats and near-repeats; the same story under a very different headline can still get through.
- **Source Grounding (no invented facts)**: A topic is scripted only if its article text can be fetched (`MIN_SOURCE_CHARS`). The LLM must return each claim with a verbatim quote from that text, and `verify_grounding()` checks in code that every quote exists in the source and every figure in the script appears in the source. A script that fails is retried once, then dropped. If no topic qualifies, the cycle is skipped. There is no fallback topic.
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
| **Total** | | | **~$100/mo** | **~$250.00** | **Budget alerts at 50/80/100% (alerts do not stop spend)** |

---

## 📈 Monetization Roadmap & Break-Even Timeline

```
Day 1 ──────────► Day 14 ──────────► Day 35 ──────────► Day 50 ──────────► Day 75
[Pipeline Launch]  [First Affil. $]  [YPP 500 Subs]    [AdSense Unlocked] [Self-Sustaining]
Spend: $0          Spend: ~$45       Spend: ~$115      Spend: ~$165       Spend: $250
Rev: $0            Rev: ~$15–$30     Rev: ~$80–$120    Rev: ~$200–$300    Rev: $450–$750+
```

1. **Days 1–14 (Affiliate Revenue)**: Links in Shorts descriptions and comments are **not clickable** on YouTube, so Shorts cannot carry affiliate links. Put the links (from the `AFFILIATE_*` values in `.env`) on the channel profile; each Short's description points viewers there. Clickable affiliate links need long-form videos, which are not implemented yet.
2. **Days 15–35 (Shorts Velocity & Fan Funding)**: Velocity of 3–4 Shorts/day triggers YouTube algorithmic reach; unlocks YPP Tier 1 (500 subs).
3. **Days 36–50 (Official YouTube AdSense Approved)**: Clears 1,000 subscribers and 4,000 watch hours; video ad revenue begins compounding ($2.50–$7.00 RPM in tech/finance).
4. **Days 50–65 (Full Investment Break-Even)**: Cumulative earnings cross $250. The channel self-funds its GCP cloud bills permanently.

---

## 🧩 Related Repositories

| Repository | Role | Actual state in this repo |
| :--- | :--- | :--- |
| **[cheaper-inference/OmniRoute](https://github.com/cheaper-inference/OmniRoute)** | LLM router | Name and port only. `services/omniroute_server.py` is a small in-repo gateway (Gemini → Groq → FreeLLMAPI; images go to Gemini only). |
| **[tashfeenahmed/freellmapi](https://github.com/tashfeenahmed/freellmapi)** | Free LLM aggregator | Name and port only. `services/freellm_server.py` is an in-repo, text-only aggregator. |
| **[smixs/visual-skills](https://github.com/smixs/visual-skills)** | Cinematography prompt guides | Vendored in `skills/visual-skills/` with custom templates in `skills/templates/`. |
| **[debpalash/voicestudio](https://github.com/debpalash/voicestudio)** | Voice cloning & dubbing | Not integrated. `voicestudio` in `docker-compose.gpu.yml` is a stub. |
| **[anil-matcha/open-generative-ai](https://github.com/anil-matcha/open-generative-ai)** | B-roll generation | Not integrated. `opengenai` is a stub. |
| **[Blizaine/Maestro](https://github.com/Blizaine/Maestro)** | Background music | Not integrated. `maestro` is a stub. |

---

## 🚀 24/7 Autonomous Daemon & GCP Production Deployment

### 1. Single Command GCP VM Deployment (100% Autonomous 24/7 Stack)
Run this single command on your GCP CPU VM (`e2-medium`) to clone the repository, build the image (pinned dependencies from `requirements.txt`), boot the background services (`OmniRoute`, `FreeLLMAPI`, `Redis`), and launch the orchestrator. Fill in `.env` before the first run:

```bash
git clone https://github.com/anandp926/almightyanand_studio.git ~/almightyanand_studio && \
cd ~/almightyanand_studio && \
cp .env.example .env && \
docker compose up -d --build && \
echo "✅ Almighty Anand Studio 24/7 autonomous stack is live!"
```

---

### 2. Daily Schedule (IST)

The daemon runs continuously with `restart: unless-stopped`:

| Time (IST) | Job | What happens |
| :--- | :--- | :--- |
| **06:30** | Morning batch | For each Short (`SHORTS_PER_DAY`, default 1): scout fresh topics, fetch the article, script it, fact-check it, render, run vision QA (up to 2 retries with new image seeds), and queue it. Stops early if no topic can be grounded. |
| **12:30** | Publish | Uploads the next queued Short (Lunch). |
| **18:30** | Publish | Uploads the next queued Short (Commute). |
| **21:00** | Publish | Uploads the next queued Short (Prime Time). |
| **23:30** | Analytics + housekeeping | Pulls per-video views and retention for videos published 2–10 days ago, updates style performance and the learned playbook state (`memory/playbook_state.json`), and prunes renders older than `RENDER_RETENTION_DAYS`. Does nothing if there is no data. |

Set `AUTO_PUBLISH=false` to render and queue without uploading, or `YOUTUBE_PRIVACY_STATUS=private` to upload for manual review.

---

### How a Short is put together

- **One look.** `configs/visual_style.json` sets a single style for every video and keeps the lower-left corner clear for the host. Pick the preset that matches your character.
- **Host.** Drop transparent PNG poses of your character into `assets/character/` (`idle.png`, `talk.png`, optional `hook`/`reveal`/`cta`). The mouth follows the voice and the pose follows the scene.
- **Two text tracks.** Each line has a Roman Hinglish caption and a `tts_text` version with Hindi in Devanagari for the voice engine. A voice line that adds a figure or an English word its caption lacks is discarded.
- **Delivery.** Each scene has a role (hook, build, reveal, cta). `delivery` in `configs/viral_playbook.json` sets the pace and the pause before each role; `configs/pronunciation.json` fixes names and acronyms.
- **Sound.** Music from `assets/bgm/<mood>/` is ducked under the voice; a whoosh marks cuts and a hit marks the reveal.
- **Subject.** `configs/series.json` limits the channel to its `pillars` (tech and AI by default). Finance, legal, health and politics are deliberately out: YouTube does not monetise AI personas giving advice on those topics.
- **Characters.** `configs/character.json` defines a fictional host and sidekick with personalities and humour rules. Rename them to match your character. They are never presented as human experts.
- **Formats.** The series rotates through four structures (comic dialogue, everyday-analogy story, myth vs sach, what changed), so consecutive Shorts do not look templated.
- **Your take.** `python -m agents.review take <n> "your line"` adds your own opinion as a scene before the closing question and re-renders. `--audio my.wav` uses your recorded voice for that line. This is what makes each Short yours.
- **Learning.** The nightly review reads each video's retention curve and fixes the weak zone: hook priors for an early drop, script length and pace for a middle drop.
- **Review.** `REVIEW_REQUIRED=true` holds each Short until `python -m agents.review approve <n>`.
- **Volume.** `SHORTS_PER_DAY` defaults to 1.

---

### 3. CLI Commands & Local Testing

```bash
# 1. Script, render and queue one Short from live feeds
python3 -m agents.orchestrator --run-once

# 2. Same, for a topic you choose (the article is what the script is checked against)
python3 -m agents.orchestrator --run-once --topic "Your headline" --source-url https://example.com/article

# 3. Run the IST scheduler daemon
python3 -m agents.orchestrator --daemon

# 4. See what the scout would pick, and what text a URL yields for grounding
python3 -m agents.trend_scout --top 5
python3 -m agents.source_fetcher https://example.com/article

# 5. Hear how the voice handles a line, list the review queue, check music and effects
python3 -m agents.voice --text "आज RBI ने नया rule जारी किया है" --speaker narrator --out /tmp/line.wav
python3 -m agents.review list
python3 -m agents.review take 1 "Mujhe lagta hai yeh students ke liye sabse kaam ka hai."
python3 -m agents.review approve 1
python3 -m agents.audio_design --list

# 6. One self-contained daily run with scheduled uploads (see infra/burst_mode.md)
python3 -m agents.orchestrator --burst

# 7. Test the gateway
curl http://localhost:8080/health
curl http://localhost:8080/v1/models

# 8. Run the unit tests
pip install -r requirements-dev.txt && python3 -m pytest
```
