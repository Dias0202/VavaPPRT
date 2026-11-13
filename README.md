# VavaPPRT — Valorant Performance Analytics Platform

**English** · [Português](README.pt-BR.md)

A private web platform for a group of friends to analyze their Valorant performance:
**where each player wins and loses, how groups play together, on which maps, and which player
combinations work** — updated automatically after every match. It ships with **X9**, a Discord
bot that answers questions about the group's matches by text and by voice, in Brazilian
Portuguese, on free tiers only.

Match data comes from the [unofficial HenrikDev API](https://docs.henrikdev.xyz/) (the official
Riot API is not viable for personal projects — see [docs/PESQUISA.MD](docs/PESQUISA.MD)), and
icons and minimaps from [Valorant-API.com](https://valorant-api.com/).

| Document (Portuguese) | |
|---|---|
| Product design | [docs/PDD.md](docs/PDD.md) |
| Technical design | [docs/SDD.md](docs/SDD.md) |
| Free deployment guide | [docs/DEPLOY_WEB.md](docs/DEPLOY_WEB.md) |
| Analytics plan and metric catalog | [docs/PLANO_ANALYTICS.md](docs/PLANO_ANALYTICS.md) |
| X9 Discord bot (design and setup) | [docs/BOT_X9.md](docs/BOT_X9.md) |
| X9 v2: diagnosis and improvement plan | [docs/PLANO_X9_V2.md](docs/PLANO_X9_V2.md) |

---

## Engineering highlights

- **Multi-provider LLM client over plain HTTP** (`bot/x9/llm.py`): Gemini as primary and Groq
  as fallback, with tool calling translated between both schemas. The history stays
  provider-neutral, so a conversation that exhausts the Gemini quota mid-way continues on Groq.
- **Streaming (SSE)** for both providers. The client only switches provider if the error
  arrives before the first text chunk, because after that the user is already reading.
- **Rate-limit aware retries**: on HTTP 429 the client honors `Retry-After`, Groq's
  "try again in Xs" message and Gemini's `retryDelay`, and tells the user it is waiting instead
  of going silent.
- **Bounded tool loop**: `run_tools_loop` stops after `max_steps=6` and then forces a final
  answer without tools; each tool result is truncated to 8,000 characters
  (`MAX_TOOL_RESULT_CHARS`). Every step records latency, provider and the tools called.
- **Typed tool contract** for the bot (`backend/app/botapi.py`), served at `/bot/v1` and
  authenticated by a service token compared in constant time (`hmac.compare_digest`).
- **Local speech-to-text** with faster-whisper (`bot/x9/stt.py`), with a domain vocabulary
  prompt and automatic CUDA/CPU selection; TTS with edge-tts/Piper.
- **Rate-limit-safe ingestion**: pages read only from the database; a separate poller is the
  only component that calls the HenrikDev API (30 requests/minute).
- **CI on two databases**: the backend and bot test suites run against SQLite **and**
  PostgreSQL 17 in GitHub Actions, and the frontend is built; GitHub Pages only publishes after
  CI passes.
- **Authentication**: bcrypt password hashing and JWT sessions; new sign-ups stay pending until
  the admin approves them.

## Architecture

```
   Browser
       │
       ▼
  React frontend (GitHub Pages) ──► FastAPI backend (Render) ──► Postgres (Supabase)
   "Telemetry" SPA                  serves ONLY from the DB          ▲      │
                                                                     │      │ pg_cron every 5 min
                                                          writes     │      ▼
                                              Poller (GitHub Actions, workflow_dispatch)
                                                                     │
                                                                     ▼
                                                      HenrikDev API (v4, 30 req/min)
```

**X9 bot (Discord):** the poller also posts each new match scoreboard to a Discord webhook.
The X9 bot (running on a group member's PC) reads the same database through `botapi`, teases
whoever played badly in the scoreboard thread, and answers questions by text (`@X9`) and by
voice ("X9, conta o round 13"), in pt-BR and entirely on free tiers (Gemini → Groq, local
Whisper, edge-tts/Piper). Details in [docs/BOT_X9.md](docs/BOT_X9.md).

**Core principle:** the HenrikDev Basic API allows only **30 requests/minute**. Pages are served
**exclusively from the database** and never call HenrikDev on page load. The database is fed by
the **poller** (cron), isolated and rate-limited.

| Layer | Technology | Where it runs |
|---|---|---|
| Frontend | React + Vite (SPA) | GitHub Pages (publishes on green CI) |
| Backend | FastAPI (Python 3.12) | Render (free, Oregon) |
| Database | PostgreSQL (SQLite fallback in dev) | Supabase (free, us-west-2) |
| Ingestion | Python script | GitHub Actions, triggered by Supabase `pg_cron` |
| Tests | pytest on SQLite **and** Postgres | GitHub Actions (CI) |

## Features

- **Closed group**: new sign-ups stay pending until the admin approves them (`ADMIN_RIOT_ID`
  defines the admin). Members can add **tracked** players (no login, with a nickname); only the
  admin removes them.
- **Pre-queue**: mark who is online → maps to accept/avoid and an agent line-up with no repeated
  agent or role, with the evidence base made explicit (exact stack > pairs > individual).
- **Pairs**: together vs. apart, **who carries**, **who trades whom** (mutual trade within 4 s),
  form, maps and combos — plus an N×N **synergy matrix**.
- **Match view**: scoreboard for all 10 players, round timeline with economy and who opened the
  round, match heatmap.
- **You vs. lobby / vs. your rank**: average scoreboard position, % of team top-1,
  ACS/K/D/ADR/HS% against your own team, the opponents and your tier's average.
- **Stack correlation**: round win rate when the group gets the opening kill vs. when it
  concedes it, and by how many players survive.
- **Tabbed profile**: Overview · Combat · Economy · Maps · Agents · Weapons · Insights · Heatmap.
- **Combat**: K/D, ACS, ADR, HS%, **KAST**, multikills.
- **Entry / Clutch / Trade**: first bloods/deaths, 1vX clutches reconstructed from the kill
  timeline, % of deaths traded vs. isolated.
- **Economy**: win rate by buy type (pistol/eco/force/full) and by side (attack/defense).
- **Maps and agents** classified as strong/weak with a **confidence badge** (sample-size gating —
  noise is never sold as insight).
- **Heatmap** of kills/deaths over the minimap.
- **Groups (2–5)**: detection of who plays together, win rate, **together-vs-solo lift**, recent
  form (W/L), best/worst maps to queue, compositions, deep group metrics and a natural-language
  **recommendation**.
- **Progress over time**: **trend** of the last N matches vs. the previous N, **RR/rank curve**,
  **sessions** with performance-drop (tilt) detection, and a weekly group **report**.
- **Post-plant/retake/site**, **HS% per weapon** and **utility usage** per agent.
- **Training goals**: gaps found by the insight engine become tracked numbers.
- **Opt-out**: delete the account and all associated personal data.
- **Data honesty**: a "data X min old" chip (turns amber if the poller stops), a full-history
  coverage bar, and a "waking up the server" banner.

## Repository layout

```
backend/                 FastAPI — serves only from the database
  app/
    main.py                routes (auth, roster, profile, groups, leaderboard)
    db.py                  data access (Postgres + SQLite) and schema
    auth.py                password (bcrypt) + JWT
    valorant_api.py        HenrikDev v4 client (synchronous, throttled)
    metrics.py             metric engine (clutch/trade/KAST/side) over v4
    analytics.py           aggregations + groups + gating + insights
    squad.py               stacks, round correlation, pairs, pre-queue
    matchview.py           match detail, you vs. lobby/rank
    progress.py            trend, sessions, RR, goals, weekly report
    ingest.py              v4 ingestion + backfill + rotating deep backfill
    poll.py                poller entry point
    botapi.py              X9 bot tools (LLM contract) + /bot/v1 mirror
    announce.py            new-match scoreboard to the Discord webhook
  tests/                   pytest (anonymized v4 fixtures)
  requirements.txt · requirements-dev.txt · .env.example
bot/                     X9 Discord bot (discord.py + Gemini/Groq + Whisper/TTS)
  x9/                      llm · brain · announcer · voice · wake · stt · tts
  tests/ · requirements.txt · .env.example · run.ps1
frontend/                React SPA ("tactical HUD" theme)
  src/{pages,components,lib}
  package.json · .env.example · dev.ps1
.github/workflows/       ci.yml · pages.yml · poll.yml · backfill.yml · deep-backfill.yml
docs/                    PDD · SDD · DEPLOY_WEB · PLANO_ANALYTICS · PLANO_FINALIZACAO · BOT_X9
```

## Running locally

**Shortcut (Windows):** `.\dev.ps1` at the root creates the venv, installs everything, copies
the example `.env` files and starts the backend (`:8000`) and frontend (`:5173`) together.

Manually:

**Backend** (uses SQLite at `backend/data/app.db` when `DATABASE_URL` is not set):
```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
copy .env.example .env       # fill in HENRIKDEV_API_KEY
uvicorn app.main:app --reload --port 8000
```

**Frontend** (requires Node; without a system Node, use `nodeenv` — see dev.ps1):
```bash
cd frontend
npm install
copy .env.example .env       # VITE_API_URL=http://localhost:8000
npm run dev                  # http://localhost:5173
```

`GET /health` reports the configuration diagnosis — database in use, whether the HenrikDev key
is present and the roster size. `status: "degraded"` points at what is missing. **Without
`HENRIKDEV_API_KEY` the API rejects everything with 401**: nobody can sign up and no matches can
be ingested.

**Manual ingestion:**
```bash
cd backend
python -m app.poll             # fetch new matches (v4) + RR curve
python -m app.poll backfill 5  # history backfill (summary, cheap)
python -m app.poll deep 50     # DEEP backfill: promotes summary -> full match
python -m app.poll recompute   # recompute metrics from raw_gz (no API calls)
```

`deep` is what gives the deep metrics their sample — without it, clutch/KAST/trade/economy/
post-plant come only from the ~5 matches `/v4/matches` returns. It costs one request per match
(30 req/min); when it hits the rate limit it stops on its own, so just run it again.
`GET /players/.../coverage` shows how much history is already covered.

**Tests:**
```bash
cd backend && python -m pytest -q tests    # SQLite by default
cd bot && python -m pytest -q
# against Postgres: set TEST_DATABASE_URL=postgresql://...
```

## Deployment

Step by step (Supabase + Render + GitHub Pages + GitHub Actions, entirely free) and a
"the site does not load" runbook in **[docs/DEPLOY_WEB.md](docs/DEPLOY_WEB.md)**.

## Privacy

Analytics are shown only to (consenting) group members. Opponents' data is used only in
aggregate (rank context), never as a browsable profile — in line with Riot's policy against
non-consensual scouting.

## Known limitations

- **No offline evaluation of the bot's grounding.** The X9 answers are built from tool results,
  and the tool loop and providers are unit-tested, but there is no golden set measuring whether
  answers stay faithful to the data.
- **No rate limiting on login.** `POST /auth/login` has no per-IP or per-account attempt limit;
  the closed-group approval flow limits who can sign up, not brute-force attempts.
- Depends on the unofficial HenrikDev API and on free tiers (Render, Supabase, Gemini, Groq),
  whose limits and policies can change.
- Most design documents are in Portuguese.

## License

[MIT](LICENSE).
