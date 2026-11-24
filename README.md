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
│    TrendScout      │   ScriptDirector   │     Publisher     │  AnalyticsReviewer  │
│  (yt-dlp & RSS)    │ (visual-skills +   │ (YouTube API v3   │ (Retention & CTR    │
│                    │  Anti-Demonetize)  │  + Affiliates)    │  Feedback Loop)     │
├────────────────────┴────────────────────┴───────────────────┴─────────────────────┤
│ • Redis 7 (Job Queue & State)                                                     │
│ • OmniRoute Gateway (:8080) — Free-tier routing + token compression               │
│ • FreeLLMAPI (:8081) — Upstream zero-cost LLM aggregator                          │
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

## 💰 $250 / 2.5-Month Budget Guardrails ($100/mo Cap)

| Component | Resource | Schedule | Est. Monthly Cost | 2.5-Month Total | Guardrail Mechanism |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **24/7 Orchestrator** | `e2-medium` (2 vCPU, 4GB RAM) | Always-on (730 hrs/mo) | ~$25 – $29 | ~$62.50 – $72.50 | Fixed CPU tier, zero GPU idle waste |
| **GPU Render Worker** | `g2-standard-4 Spot` (NVIDIA L4) | Batched ~4.5 hrs/day | ~$55 | ~$137.50 | Hard kill-switch at 4.5 hrs/day; auto-stops on 5m idle |
| **Storage & Egress** | 100 GB PD + GCS Bucket | 24/7 | ~$10 | ~$25.00 | 30-day lifecycle auto-cleanup on raw intermediate clips |
| **LLM Inference** | OmniRoute + FreeLLMAPI | Real-time | **$0.00** | **$0.00** | Free Gemini 2.0 Flash + Groq Llama 3.3 70B fallbacks |
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
| **[anil-matcha/open-generative-ai](https://github.com/anil-matcha/open-generative-ai)** | Visual & Lip-Sync B-Roll | Wired as `opengenai` (:8091) in `docker-compose.gpu.yml`. Synthesizes custom B-roll replacing >70% of trending video visuals. |
| **[Blizaine/Maestro](https://github.com/Blizaine/Maestro)** | AI Background Music | Wired as `maestro` (:8092) in `docker-compose.gpu.yml`. Composes royalty-free background soundtracks ducked at -16.0 LUFS. |

---

## 🛡 Hardcoded Anti-Demonetization Policy (Step 4)

Hardcoded directly in [`agents/script_director.py`](agents/script_director.py) and [`agents/editor_ffmpeg.py`](agents/editor_ffmpeg.py):

- **No Raw Re-uploads**: Prohibits raw cut-and-reupload of trending videos to prevent YouTube Reused/Inauthentic Content demonetization.
- **Mandatory Transformation (>70%)**: Enforces `MIN_VISUAL_TRANSFORMATION_RATIO = 0.75`. The assembly agent aborts if original B-roll / animations do not exceed 70% of the timeline.
- **Dual-Format Output**: Every long-form (16:9) topic automatically spawns 2–3 vertical (9:16) Shorts with suspense/cliffhanger endings.
- **Broadcast Audio Standard**: All video streams pass through FFmpeg `loudnorm=I=-16:TP=-1.5:LRA=11` with background audio ducked to -18 dB.

---

## 🚀 Local CLI Testing & Dry-Run Pipeline

Run any agent or the complete end-to-end pipeline locally without incurring any GPU or API expenses:

```bash
# 1. Test individual agents locally
python3 -m agents.trend_scout --dry-run
python3 -m agents.script_director --dry-run
python3 -m agents.media_dispatcher --dry-run
python3 -m agents.editor_ffmpeg --dry-run
python3 -m agents.publisher --dry-run
python3 -m agents.analytics_reviewer --dry-run

# 2. Run the complete Master Orchestrator Pipeline
python3 -m agents.orchestrator --dry-run
```

---

## ⚙️ Environment Configuration (`.env`)

Copy `.env.example` to `.env`. For local testing on free tokens, you only need `GOOGLE_API_KEY`:

```bash
# Required Free Tier LLM Key (Google AI Studio)
GOOGLE_API_KEY=your_gemini_api_key

# Optional Free Tier LLM Key (Groq)
GROQ_API_KEY=your_groq_api_key

# YouTube Verified Credentials (auto-linked)
YOUTUBE_CHANNEL_ID_EN=UCiyowMaCWGFMjPka0TDamVg
YOUTUBE_OAUTH_CREDS=configs/youtube_token.json
YOUTUBE_CLIENT_SECRETS=configs/client_secrets.json

# Monetization Affiliates (Optional)
AFFILIATE_TECH_URL=https://amzn.to/your-tech-tag
AFFILIATE_BOOKS_URL=https://amzn.to/your-books-tag
```

---

## ☁️ Single Command GCP Cloud Deployment

Once local testing passes, deploy to your GCP project (`almighty-anand-studio`) with one command:

```bash
gcloud compute ssh almightyanand-studio --zone=asia-south1-a --project=almighty-anand-studio --command="if [ ! -d ~/almightyanand_studio ]; then git clone https://github.com/anandp926/almightyanand_studio.git ~/almightyanand_studio; else cd ~/almightyanand_studio && git pull origin main; fi && cd ~/almightyanand_studio && docker compose up -d"
```
