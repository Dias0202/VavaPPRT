# Metrics Specification — VavaPPRT

All metrics are computed over **standard modes only** (Competitive, Unrated, Premier).
Deathmatch, Spike Rush, and Escalation are excluded from all calculations.

---

## Data Sources

| Source | Description |
|--------|-------------|
| HenrikDev v4 `/matches` | Full match data with per-round stats, kills, economy |
| HenrikDev v1 `/lifetime/matches` | Summary-only backfill (no round detail) |
| `player_match_derived` table | Pre-aggregated per-player per-match metrics |
| `kills` table | All kills with coordinates, weapon, trade flags |
| `player_round_stats` table | Per-round data for tracked players |
| `spatial_events` table | Position snapshots at kill frames |

---

## Core Metrics

### ACS — Average Combat Score
```
ACS = sum(score) / sum(rounds_in_match)
```
- `score` is the combat score assigned by Riot per round (damage, kills, assists, objective)
- Matches Valorant Tracker's displayed ACS
- Rounds = total rounds played in the match (not capped to player's alive rounds)

### ADR — Average Damage per Round
```
ADR = sum(damage_made) / sum(rounds)
```
- `damage_made` is damage dealt to enemies (not self/teammates)
- Source: `stats.damage.dealt` in v4 player node

### K/D — Kill/Death Ratio
```
KD = total_kills / max(1, total_deaths)
```

### Win Rate
```
WR% = (matches_won / total_matches) × 100
```

### HS% — Headshot Percentage
```
HS% = headshots / (headshots + bodyshots + legshots) × 100
```
- Hit-based (counts only shots that connected), same methodology as tracker.gg
- Does NOT count shots that missed (total shots fired not available from API)
- Source: `stats.headshots/bodyshots/legshots` in v4

### KAST
```
KAST% = kast_rounds / total_full_data_rounds × 100
```
A round counts toward KAST if the player had **at least one** of:
- **K** — at least 1 kill
- **A** — at least 1 assist (appeared in `kill.assistants[]`)
- **S** — survived the round (not in victim list for any kill in that round)
- **T** — died but was traded (teammate killed the killer within `TRADE_WINDOW_MS = 4000ms`)

Only available for `full_data = 1` (v4 ingest), not backfill summaries.

---

## Advanced Metrics (full_data only)

### First Blood / First Death
- **First Blood**: player made the first kill of a round (`order_in_round == 0`, `killer_puuid == puuid`)
- **First Death**: player was the first death of a round (`order_in_round == 0`, `victim_puuid == puuid`)
- Reported per match and as average per game

### Trade Kill
A kill is a **trade** if:
1. The victim had previously killed a teammate of the killer in the same round
2. The kill occurred within `TRADE_WINDOW_MS = 4000ms` of that teammate's death

### Death Traded
A death is **traded** if:
1. A teammate killed the killer
2. Within `TRADE_WINDOW_MS = 4000ms` after the death

### Clutch (1vX)
A clutch attempt is recorded when a player becomes the **last surviving member** of their team
while at least 1 enemy is still alive. The clutch is **won** if that player's team wins the round.

### Economy Tiers (per round)
| Tier | Loadout Value |
|------|--------------|
| eco | < 1500 |
| force | 1500 – 3899 |
| full | ≥ 3900 |

### Side (Attack/Defense)
Inferred from plant data: the team that plants the spike in the **first 12 rounds** is the attacker.
- Rounds 0–11: first half (attacker = team with most plants)
- Rounds 12–23: sides swap
- Rounds 24+: overtime, alternating per pair

---

## Metric Validation

`validate_match_metrics(match, puuid)` checks for:
- Zero score with >5 rounds played
- Zero shot breakdown (HS/BS/LS) despite kills present
- Impossible kill counts (>5 × rounds)
- Zero damage with kills present
- HS% > 80% (implausible for hit-based calculation)

All anomalies are emitted as `WARNING` log lines and returned by `GET /validate/metrics`.
