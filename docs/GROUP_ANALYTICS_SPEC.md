# Group Analytics Specification — VavaPPRT

The Squad Analytics engine is the core differentiator: it answers questions about
**group synergy** that per-player trackers cannot.

---

## Data Model

All group analysis is keyed by `puuid` sets. A "group" is any subset (2–5) of the
tracked roster that appeared on the **same team** in a competitive match.

```
roster_puuids → match_players JOIN matches
             → filter: same match_id AND same team AND puuid IN roster
```

---

## Algorithms

### 1. Group Synergy (`/group/synergy`)
Simple co-occurrence: for every pair of roster players who played on the same team,
compute win rate together.

```
for each match M:
  roster_members_in_match = {p | p in roster AND p in M.players AND same team}
  for each pair (a, b) in roster_members_in_match:
    duo[(a,b)].games += 1
    duo[(a,b)].wins  += 1 if M.won else 0

win_rate(a,b) = duo[(a,b)].wins / duo[(a,b)].games × 100
```

Confidence gate: `MIN_DUO = 4` games before result is marked "high confidence".

### 2. Squad Groups (`/group/squads`)
Groups are identified by the **exact frozenset of puuids** that played together.
Two matches with the same 3 players on the same team = same group entry.

```
key = frozenset({p₁, p₂, ..., pₙ})   where n ∈ {2,3,4,5}
groups[key].games += 1
groups[key].wins  += 1 if won
```

### 3. Group Detail (`POST /group/detail`)
For an exact group (specified by caller), computes:

#### Together Win Rate
```
together_wr = wins_when_all_present / games_when_all_present × 100
```
"All present" = **exact match**: the set of puuids in the match's team equals
the requested group, no more, no less.

#### Lift per Member
Measures how much a player's win rate changes when playing with the group:
```
lift(p) = together_wr − apart_wr(p)

apart_wr(p) = wins in matches NOT in together_ids / such matches × 100
```
Positive lift: player performs better with this group.
Negative lift (≥ −8pp with ≥10 apart games): candidate for role review.

#### Recent Form
Last 12 games together, ordered most-recent first. Displayed as V/D cells.

#### Best/Worst Maps
```
map_wr(m) = wins on map m (together) / games on map m (together) × 100
best  = argmax(map_wr)
worst = argmin(map_wr)
```
Only maps with ≥3 games together are included in recommendations.

#### Agent Compositions
```
comp = " + ".join(sorted(agent for each group member))
comp_wr(comp) = wins with that comp / games with that comp × 100
```

#### Deep Group Metrics (full_data only)
Aggregated over all full_data matches played together:
- `first_bloods`: sum of first_bloods across all members
- `first_deaths`: sum of first_deaths across all members
- `kast`: sum(kast_rounds) / sum(rounds) × 100
- `trade_pct`: sum(deaths_traded) / sum(deaths) × 100
- `clutch`: aggregated clutch win/attempt counts
- `multikills`: aggregated 2K/3K/4K/5K counts

### 4. Group Correlation Engine (`GET /group/correlation`)
Answers: "which map × agent combination wins most for this exact group?"

```
for each match M where exact group played together:
  agents_sorted = " + ".join(sorted(agent(p) for p in group))
  key = (M.map, agents_sorted)
  combo_map[key].games += 1
  combo_map[key].wins  += 1 if M.won

first_blood_impact:
  had_fb = any(derived[p][M.match_id].first_bloods > 0 for p in group)
  fb_key = "fb_secured" if had_fb else "fb_lost"
  fb_impact[fb_key].games += 1
  fb_impact[fb_key].wins  += 1 if M.won
```

Returns top 12 combinations sorted by game count descending.

### 5. Round Correlation (`GET /group/round-analysis`)
Answers: "does having more group members alive at round end predict match wins?"

```
for each round R in player_round_stats:
  n_alive = |{p in group | p.alive_end == 1 in round R}|

by_survivors[n_alive].rounds += 1
by_survivors[n_alive].total  += 1 if match_won[R.match_id]
```

Correlation is match-level (win/loss), not round-level, since rounds don't have
independent win/loss outcomes in this dataset.

### 6. Duo Detail (`GET /group/duo/{a}/{b}`)
Specialized 2-player analysis with combined K/D and agent combo breakdown.

---

## Confidence Gates

| Context | Minimum games | Label |
|---------|--------------|-------|
| General group | 3 | `MIN_GROUP` |
| Duo | 4 | `MIN_DUO` |
| Map breakdown | 5 | `MIN_MAP` |
| Agent breakdown | 3 | `MIN_AGENT` |
| Clutch reporting | 5 | `MIN_CLUTCH` |
| Insights | 3 | `MIN_MATCHES` |

Results below the floor are returned but labeled `confidence: "low"`.

---

## Recommendation Text Generation

`_group_reco(wr, games, lifts, best, worst)` generates natural-language advice:
1. Group strength: ≥55% WR → "Grupo forte", <45% → "abaixo do esperado"
2. Map to queue: best map with ≥3 games and ≥60% WR
3. Map to avoid: worst map with ≥3 games and ≤40% WR
4. Drag detection: lift ≤ −8pp with ≥10 apart games → suggests role review

---

## Spatial Analysis (`GET /players/{id}/{tag}/spatial`)

Kill-frame position snapshots are extracted during ingest from `kill.player_locations[]`.
For each kill event in a match, every tracked player's position is recorded in `spatial_events`.

```python
spatial_event = {
  match_id, round_id, puuid,
  event_type = "kill_snapshot",
  time_ms,          # time in round at kill moment
  x, y,             # game coordinate system
  z = 0,
  outcome = "kill_event"
}
```

These complement the standard heatmap (which uses the kill/death coordinate from `kills.vx/vy`)
by providing the **full team's positions** at the moment of each kill, enabling future analysis of:
- "Where was player X when player Y got a kill?"
- Zone control correlation with round outcomes
