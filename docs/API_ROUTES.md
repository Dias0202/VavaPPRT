# API Routes — VavaPPRT

Base URL (local): `http://localhost:8000`

All routes except `/`, `/health`, `/auth/*` require `Authorization: Bearer <token>`.

---

## Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Service status |
| GET | `/health` | Health check |

---

## Auth

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/auth/register` | `{username, password, riot_id, tagline, region}` | Create account, returns JWT |
| POST | `/auth/login` | `{username, password}` | Login, returns JWT |
| GET | `/me` | — | Current user profile |

---

## Players

| Method | Path | Description |
|--------|------|-------------|
| GET | `/players` | List all tracked players |
| GET | `/players/{riot_id}/{tagline}/overview` | Aggregate stats (ACS, KD, WR, KAST…) |
| GET | `/players/{riot_id}/{tagline}/recent` | Last 20 matches |
| GET | `/players/{riot_id}/{tagline}/maps` | Win rate and KD per map |
| GET | `/players/{riot_id}/{tagline}/agents` | Win rate and ACS per agent |
| GET | `/players/{riot_id}/{tagline}/weapons` | Kill distribution by weapon |
| GET | `/players/{riot_id}/{tagline}/heatmap?map=X` | Kill/death coordinates |
| GET | `/players/{riot_id}/{tagline}/spatial?map=X` | Kill-frame position snapshots |
| GET | `/players/{riot_id}/{tagline}/insights` | Natural-language strengths/gaps |

---

## Roster

| Method | Path | Body | Description |
|--------|------|------|-------------|
| GET | `/roster` | — | Full tracked roster |
| POST | `/roster` | `{riot_id, tagline, region}` | Add player (triggers background ingest) |

---

## Group Analytics

| Method | Path | Body | Description |
|--------|------|------|-------------|
| GET | `/group/synergy` | — | Duo win rates for all roster pairs |
| GET | `/group/squads` | — | All unique group combinations that played together |
| POST | `/group/detail` | `{members: [{riot_id, tagline}]}` | Deep analysis of a specific group |
| GET | `/group/duo/{ra}/{ta}/{rb}/{tb}` | — | Duo detail (KD, maps, agent combos, lift) |
| GET | `/group/correlation` | — | Map × agent composition win rates for the full roster |
| GET | `/group/round-analysis` | — | Survivor count vs match win rate correlation |

---

## Leaderboard

| Method | Path | Description |
|--------|------|-------------|
| GET | `/leaderboard` | Roster ranked by WR + KD |

---

## Validation / Debug

| Method | Path | Query | Description |
|--------|------|-------|-------------|
| GET | `/validate/metrics` | `limit=5` (max 20) | Recomputes metrics from raw and surfaces discrepancies |

---

## Response Shapes

### Player Overview
```json
{
  "matches": 42,
  "deep_matches": 18,
  "confidence": "high",
  "win_rate": 54.8,
  "kd": 1.23,
  "acs": 218,
  "adr": 145.2,
  "hs_pct": 21.3,
  "kast": 71.4,
  "kills": 430, "deaths": 349, "assists": 87,
  "first_bloods": 24, "first_deaths": 19,
  "fb_per_match": 1.33, "fd_per_match": 1.06,
  "trade_pct": 38.2,
  "multikills": {"2k": 41, "3k": 12, "4k": 2, "5k": 0},
  "clutch": {"attempted": 18, "won": 6, "win_rate": 33.3, "by_size": {"1": {...}, ...}},
  "economy": {"eco": {"n": 38, "wr": 28.9}, "force": {...}, "full": {...}, "pistol": {...}},
  "sides": {"attack": {"n": 198, "wr": 52.0}, "defense": {"n": 204, "wr": 57.8}}
}
```

### Group Correlation
```json
{
  "total_together": 14,
  "map_agent_combos": [
    {"map": "Ascent", "agent_combo": "Jett + Sova + Viper", "games": 4, "win_rate": 75.0, "confidence": "high"}
  ],
  "first_blood_impact": {
    "fb_secured": {"games": 9, "win_rate": 66.7},
    "fb_lost":    {"games": 5, "win_rate": 40.0}
  }
}
```

### Validate Metrics
```json
[
  {
    "match_id": "abc123...",
    "players_count": 3,
    "issues_found": 0,
    "validations": [
      {"puuid": "abc12345", "acs": 214, "hs_pct": 22.1, "rounds": 24, "issues": {}, "clean": true}
    ]
  }
]
```
