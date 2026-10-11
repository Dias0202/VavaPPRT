"""Motor de métricas — derivações sobre uma partida no schema HenrikDev v4.

Funções puras (entrada = dict da partida v4). Calculam tudo que alimenta a
plataforma: resumo por jogador, métricas por round, first blood/death, clutch
(1vX), trade/KAST e lado ataque/defesa.

Toda chave de jogador é o `puuid` (estável); nomes são só rótulo.
"""
import json
from datetime import datetime
from collections import defaultdict

# Janela de trade: um inimigo morto até X ms depois de matar seu colega "vinga"
# (troca) a morte. Calibrável; 4s é o padrão usado pela maioria dos trackers.
TRADE_WINDOW_MS = 4000


# ---------------------------------------------------------------------------
# Helpers de indexação
# ---------------------------------------------------------------------------
def index_players(match):
    """puuid -> player dict (v4)."""
    return {p["puuid"]: p for p in match.get("players", [])}


def team_rosters(match):
    """team_id -> set(puuid)."""
    rosters = defaultdict(set)
    for p in match.get("players", []):
        rosters[p["team_id"]].add(p["puuid"])
    return rosters


def teams_won(match):
    """team_id -> bool (venceu a partida)."""
    return {t["team_id"]: bool(t.get("won")) for t in match.get("teams", [])}


def kills_by_round(match):
    """round_id -> lista de kills ordenada por tempo no round."""
    by = defaultdict(list)
    for k in match.get("kills", []):
        by[k["round"]].append(k)
    for r in by.values():
        r.sort(key=lambda k: k["time_in_round_in_ms"])
    return by


# ---------------------------------------------------------------------------
# Lado ataque/defesa (inferido — Valorant não entrega direto)
# ---------------------------------------------------------------------------
def infer_attacker_by_round(match):
    """round_id -> team_id que estava ATACANDO. None se indeterminado.

    Heurística: o time que planta é o atacante. Determinamos o atacante da 1ª
    metade (rounds 0-11) pelo time que mais planta nesses rounds; a 2ª metade
    inverte; OT alterna. Sólido para competitivo padrão.
    """
    rounds = match.get("rounds", [])
    team_ids = [t["team_id"] for t in match.get("teams", [])]
    if len(team_ids) != 2:
        return {}
    a, b = team_ids

    plant_count = defaultdict(int)
    for rd in rounds:
        if rd["id"] < 12 and rd.get("plant") and rd["plant"].get("player"):
            plant_count[rd["plant"]["player"]["team"]] += 1
    if not plant_count:
        return {}  # sem plant na 1ª metade: lado indeterminado
    first_half_attacker = max(plant_count, key=plant_count.get)
    other = b if first_half_attacker == a else a

    out = {}
    for rd in rounds:
        r = rd["id"]
        if r < 12:
            out[r] = first_half_attacker
        elif r < 24:
            out[r] = other
        else:  # OT: alterna a cada round a partir do atacante da 2ª metade
            out[r] = other if (r - 24) % 2 == 0 else first_half_attacker
    return out


# ---------------------------------------------------------------------------
# Eventos: first blood/death, trade flags
# ---------------------------------------------------------------------------
def annotate_kills(match, trade_window_ms=TRADE_WINDOW_MS):
    """Lista de kills enriquecida com is_first, is_trade e order_in_round.

    is_trade: este kill vingou (dentro da janela, antes) uma morte de colega do
    killer causada justamente pela vítima.
    """
    by_round = kills_by_round(match)
    enriched = []
    for round_id, kills in by_round.items():
        for i, k in enumerate(kills):
            is_first = i == 0
            killer_team = k["killer"]["team"]
            victim_puuid = k["victim"]["puuid"]
            t = k["time_in_round_in_ms"]
            # trade: a vítima atual matou um colega do killer pouco antes?
            is_trade = False
            for prev in kills:
                if prev["time_in_round_in_ms"] >= t:
                    break
                if (
                    prev["killer"]["puuid"] == victim_puuid
                    and prev["victim"]["team"] == killer_team
                    and t - prev["time_in_round_in_ms"] <= trade_window_ms
                ):
                    is_trade = True
                    break
            enriched.append({
                "round": round_id,
                "order_in_round": i,
                "time_in_round_in_ms": t,
                "killer_puuid": k["killer"]["puuid"],
                "killer_team": killer_team,
                "victim_puuid": victim_puuid,
                "victim_team": k["victim"]["team"],
                "weapon": (k.get("weapon") or {}).get("name"),
                "is_first": is_first,
                "is_trade": is_trade,
                "vx": (k.get("location") or {}).get("x"),
                "vy": (k.get("location") or {}).get("y"),
                "headshot": False,  # kill-level HS não vem; usamos shots no resumo
            })
    return enriched


def death_was_traded(match, kill_list=None, trade_window_ms=TRADE_WINDOW_MS):
    """(round, victim_puuid) -> bool: a morte foi vingada dentro da janela depois."""
    by_round = kills_by_round(match)
    out = {}
    for round_id, kills in by_round.items():
        for k in kills:
            victim = k["victim"]["puuid"]
            victim_team = k["victim"]["team"]
            killer = k["killer"]["puuid"]
            t = k["time_in_round_in_ms"]
            traded = False
            for nxt in kills:
                if nxt["time_in_round_in_ms"] <= t:
                    continue
                if nxt["time_in_round_in_ms"] - t > trade_window_ms:
                    break
                # alguém do time da vítima matou o killer = vingança
                if nxt["victim"]["puuid"] == killer and nxt["killer"]["team"] == victim_team:
                    traded = True
                    break
            out[(round_id, victim)] = traded
    return out


# ---------------------------------------------------------------------------
# Clutch (1vX) — varredura da timeline contando vivos
# ---------------------------------------------------------------------------
def compute_clutches(match):
    """Lista de clutches: {puuid, round, size (X em 1vX), won}.

    Para cada round, reconstrói os vivos a partir do roster e da ordem de mortes;
    quando alguém fica último vivo do time com inimigos ainda vivos, é um clutch.
    """
    rosters = team_rosters(match)
    team_ids = list(rosters.keys())
    if len(team_ids) != 2:
        return []
    by_round = kills_by_round(match)
    results = {rd["id"]: rd.get("winning_team") for rd in match.get("rounds", [])}

    clutches = []
    for round_id, kills in by_round.items():
        alive = {t: set(p) for t, p in rosters.items()}
        clutch_owner = None  # (puuid, team, size_when_started)
        for k in kills:
            vt, vp = k["victim"]["team"], k["victim"]["puuid"]
            alive[vt].discard(vp)
            # checa situação 1vX para cada time
            for team in team_ids:
                enemy = team_ids[0] if team_ids[1] == team else team_ids[1]
                if len(alive[team]) == 1 and len(alive[enemy]) >= 1:
                    last = next(iter(alive[team]))
                    if clutch_owner is None or clutch_owner[0] != last:
                        clutch_owner = (last, team, len(alive[enemy]))
        if clutch_owner:
            puuid, team, size = clutch_owner
            clutches.append({
                "puuid": puuid,
                "round": round_id,
                "size": size,
                "won": results.get(round_id) == team,
            })
    return clutches


# ---------------------------------------------------------------------------
# Buy type
# ---------------------------------------------------------------------------
def classify_buy(loadout_value):
    """eco / force / full a partir do valor do loadout do jogador no round."""
    if loadout_value is None:
        return "unknown"
    if loadout_value < 1500:
        return "eco"
    if loadout_value < 3900:
        return "force"
    return "full"


# ---------------------------------------------------------------------------
# Extração por jogador por round + resumo da partida
# ---------------------------------------------------------------------------
def player_round_rows(match, puuid):
    """Linhas por round para um jogador: kills, dano, hs, buy, vivo no fim, traded."""
    rows = []
    traded = death_was_traded(match)
    by_round = kills_by_round(match)
    for rd in match.get("rounds", []):
        rid = rd["id"]
        ps = next((s for s in rd.get("stats", []) if s["player"]["puuid"] == puuid), None)
        if not ps:
            continue
        st = ps.get("stats", {})
        econ = ps.get("economy", {})
        loadout = econ.get("loadout_value")
        # vivo no fim do round? = não foi vítima de nenhum kill neste round
        died = any(
            k["victim"]["puuid"] == puuid for k in by_round.get(rid, [])
        )
        rows.append({
            "round_id": rid,
            "kills": st.get("kills", 0),
            "damage": (sum(d["damage"] for d in ps.get("damage_events", []))
                       if ps.get("damage_events") else 0),
            "headshots": st.get("headshots", 0),
            "bodyshots": st.get("bodyshots", 0),
            "legshots": st.get("legshots", 0),
            "loadout_value": loadout,
            "buy_type": classify_buy(loadout),
            "weapon": (econ.get("weapon") or {}).get("name"),
            "alive_end": not died,
            "was_traded": traded.get((rid, puuid), False) if died else False,
        })
    return rows


def player_summary(match, puuid):
    """Resumo da partida para um jogador (espelha match_players)."""
    p = index_players(match).get(puuid)
    if not p:
        return None
    st = p.get("stats", {})
    dmg = st.get("damage", {})
    won = teams_won(match).get(p["team_id"], False)
    return {
        "puuid": puuid,
        "name": p.get("name"),
        "tag": p.get("tag"),
        "team": p["team_id"],
        "agent": (p.get("agent") or {}).get("name"),
        "won": won,
        "score": st.get("score", 0),
        "kills": st.get("kills", 0),
        "deaths": st.get("deaths", 0),
        "assists": st.get("assists", 0),
        "headshots": st.get("headshots", 0),
        "bodyshots": st.get("bodyshots", 0),
        "legshots": st.get("legshots", 0),
        "damage_made": dmg.get("dealt", 0),
        "damage_received": dmg.get("received", 0),
        "loadout_avg": (p.get("economy", {}).get("loadout_value", {}) or {}).get("average"),
        "party_id": p.get("party_id"),
        "tier": (p.get("tier") or {}).get("id", 0),
        "tier_name": (p.get("tier") or {}).get("name"),
    }


def match_meta(match):
    md = match.get("metadata", {})
    started = md.get("started_at")
    started_raw = None
    if isinstance(started, str):
        try:
            started_raw = int(datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp())
        except ValueError:
            started_raw = None
    return {
        "match_id": md.get("match_id"),
        "map": (md.get("map") or {}).get("name"),
        "mode": (md.get("queue") or {}).get("name") or (md.get("queue") or {}).get("id"),
        "queue": (md.get("queue") or {}).get("id"),
        "started_at": started,
        "started_raw": started_raw,
        "rounds": len(match.get("rounds", [])),
        "region": md.get("region"),
        "season": (md.get("season") or {}).get("short"),
    }


def round_winners(match):
    return {rd["id"]: rd.get("winning_team") for rd in match.get("rounds", [])}


PISTOL_ROUNDS = {0, 12}


def player_ability_casts(match, puuid):
    """Casts de utilidade do jogador na partida.

    Só o nível de JOGADOR traz os números: em `rounds[].stats[].ability_casts`
    a Henrik devolve tudo `null` (e ainda com outra grafia de chave —
    `ability_1` vs `ability1`). Somar os rounds daria zero sempre.
    """
    p = index_players(match).get(puuid) or {}
    c = p.get("ability_casts") or {}
    return {
        "grenade": c.get("grenade") or c.get("grenade_casts") or 0,
        "ability1": c.get("ability1") or c.get("ability_1") or 0,
        "ability2": c.get("ability2") or c.get("ability_2") or 0,
        "ultimate": c.get("ultimate") or c.get("ultimate_casts") or 0,
    }


def plant_stats(match, team):
    """Pós-plant, retake e sites, do ponto de vista de um time.

    - pós-plant: rounds em que o SEU time plantou → quantos vocês seguraram.
    - retake: rounds em que o INIMIGO plantou → quantos vocês reverteram.
    - sites: onde o seu time planta (A/B/C) e o aproveitamento em cada um.
    """
    pp_r = pp_w = rt_r = rt_w = plants = defuses = 0
    sites = defaultdict(lambda: {"n": 0, "wins": 0})
    for rd in match.get("rounds", []):
        plant = rd.get("plant") or {}
        planter = plant.get("player") or {}
        planter_team = planter.get("team")
        if not planter_team:
            continue
        won = rd.get("winning_team") == team
        if planter_team == team:
            plants += 1
            pp_r += 1
            pp_w += 1 if won else 0
            site = plant.get("site")
            if site:
                sites[site]["n"] += 1
                sites[site]["wins"] += 1 if won else 0
        else:
            rt_r += 1
            rt_w += 1 if won else 0
        defuse = rd.get("defuse") or {}
        defuser = defuse.get("player") or {}
        if defuser.get("team") == team:
            defuses += 1
    return {
        "plants": plants, "defuses": defuses,
        "postplant_rounds": pp_r, "postplant_wins": pp_w,
        "retake_rounds": rt_r, "retake_wins": rt_w,
        "sites": {k: dict(v) for k, v in sites.items()},
    }


def build_player_derived(match, puuid, kills_annotated=None, attacker_map=None):
    """Linha de métricas profundas de um jogador na partida (alimenta o agregado)."""
    summary = player_summary(match, puuid)
    if not summary:
        return None
    if kills_annotated is None:
        kills_annotated = annotate_kills(match)
    if attacker_map is None:
        attacker_map = infer_attacker_by_round(match)
    rounds = player_round_rows(match, puuid)
    winners = round_winners(match)
    team = summary["team"]

    fb = sum(1 for k in kills_annotated if k["is_first"] and k["killer_puuid"] == puuid)
    fd = sum(1 for k in kills_annotated if k["is_first"] and k["victim_puuid"] == puuid)
    trade_kills = sum(1 for k in kills_annotated if k["is_trade"] and k["killer_puuid"] == puuid)
    deaths_traded = sum(1 for r in rounds if r["was_traded"])

    assist_rounds = set()
    for k in match.get("kills", []):
        if any(a["puuid"] == puuid for a in k.get("assistants", [])):
            assist_rounds.add(k["round"])

    kast = 0
    mk = {2: 0, 3: 0, 4: 0, 5: 0}
    atk_r = atk_w = def_r = def_w = 0
    eco_r = eco_w = force_r = force_w = full_r = full_w = pistol_r = pistol_w = 0

    for r in rounds:
        rid = r["round_id"]
        won_round = winners.get(rid) == team
        if r["kills"] > 0 or rid in assist_rounds or r["alive_end"] or r["was_traded"]:
            kast += 1
        for n in (2, 3, 4, 5):
            if r["kills"] >= n:
                mk[n] += 1
        # lado
        atk = attacker_map.get(rid)
        if atk == team:
            atk_r += 1; atk_w += 1 if won_round else 0
        elif atk is not None:
            def_r += 1; def_w += 1 if won_round else 0
        # economia
        bt = r["buy_type"]
        if bt == "eco":
            eco_r += 1; eco_w += 1 if won_round else 0
        elif bt == "force":
            force_r += 1; force_w += 1 if won_round else 0
        elif bt == "full":
            full_r += 1; full_w += 1 if won_round else 0
        if rid in PISTOL_ROUNDS:
            pistol_r += 1; pistol_w += 1 if won_round else 0

    clutches = [c for c in compute_clutches(match) if c["puuid"] == puuid]
    pl = plant_stats(match, team)
    casts = player_ability_casts(match, puuid)

    return {
        "match_id": match_meta(match)["match_id"],
        "puuid": puuid,
        "map": match_meta(match)["map"],
        "agent": summary["agent"],
        "won": 1 if summary["won"] else 0,
        "rounds": len(rounds),
        "kills": summary["kills"], "deaths": summary["deaths"], "assists": summary["assists"],
        "score": summary["score"],
        "damage": summary["damage_made"],
        "headshots": summary["headshots"], "bodyshots": summary["bodyshots"], "legshots": summary["legshots"],
        "first_bloods": fb, "first_deaths": fd,
        "trade_kills": trade_kills, "deaths_traded": deaths_traded,
        "kast_rounds": kast,
        "mk2": mk[2], "mk3": mk[3], "mk4": mk[4], "mk5": mk[5],
        "atk_rounds": atk_r, "atk_wins": atk_w, "def_rounds": def_r, "def_wins": def_w,
        "eco_rounds": eco_r, "eco_wins": eco_w,
        "force_rounds": force_r, "force_wins": force_w,
        "full_rounds": full_r, "full_wins": full_w,
        "pistol_rounds": pistol_r, "pistol_wins": pistol_w,
        "clutches_json": json.dumps(
            [{"size": c["size"], "won": c["won"]} for c in clutches]
        ),
        "plants": pl["plants"], "defuses": pl["defuses"],
        "postplant_rounds": pl["postplant_rounds"], "postplant_wins": pl["postplant_wins"],
        "retake_rounds": pl["retake_rounds"], "retake_wins": pl["retake_wins"],
        "sites_json": json.dumps(pl["sites"]),
        "cast_grenade": casts["grenade"], "cast_ability1": casts["ability1"],
        "cast_ability2": casts["ability2"], "cast_ultimate": casts["ultimate"],
        "full_data": 1,
    }


# Modos com estrutura padrão de rounds (5v5, plant/eco). Outros (Deathmatch,
# Spike Rush, Escalation...) poluiriam as métricas e são ignorados.
STANDARD_MODE_TYPES = {"standard", "premier"}
STANDARD_MODE_NAMES = {"competitive", "unrated", "premier"}


def is_standard(match):
    q = match.get("metadata", {}).get("queue", {}) or {}
    mt = (q.get("mode_type") or "").lower()
    qid = (q.get("id") or "").lower()
    return mt in STANDARD_MODE_TYPES or qid in STANDARD_MODE_NAMES


def lifetime_is_standard(mode_name):
    return (mode_name or "").lower() in STANDARD_MODE_NAMES


def lifetime_summary(lm, puuid):
    """Converte uma partida do endpoint lifetime (só-resumo) em linhas de
    backfill: (meta, match_player, derived com full_data=0). None se não-padrão.
    """
    meta_in = lm.get("meta", {})
    if not lifetime_is_standard(meta_in.get("mode")):
        return None
    st = lm.get("stats", {})
    if st.get("puuid") != puuid:
        return None
    teams = lm.get("teams", {}) or {}
    team = (st.get("team") or "").lower()
    red, blue = teams.get("red", 0), teams.get("blue", 0)
    won = 1 if ((team == "red" and red > blue) or (team == "blue" and blue > red)) else 0
    rounds = (red or 0) + (blue or 0)
    shots = st.get("shots", {}) or {}
    dmg = st.get("damage", {}) or {}
    agent = (st.get("character") or {}).get("name")
    started = meta_in.get("started_at")
    started_raw = None
    if isinstance(started, str):
        try:
            started_raw = int(datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass

    meta = {
        "match_id": meta_in.get("id"),
        "map": (meta_in.get("map") or {}).get("name"),
        "mode": meta_in.get("mode"),
        "queue": (meta_in.get("mode") or "").lower(),
        "season": (meta_in.get("season") or {}).get("short"),
        "started_at": started, "started_raw": started_raw,
        "rounds": rounds, "region": meta_in.get("region"),
    }
    mp = {
        "match_id": meta["match_id"], "puuid": puuid, "name": st.get("name"), "tag": st.get("tag"),
        # v4 grava "Red"/"Blue"; o lifetime às vezes vem em minúsculas. Mesmo
        # time precisa comparar igual entre partida completa e de resumo.
        "team": team.capitalize() or None, "agent": agent, "won": won, "score": st.get("score", 0),
        "kills": st.get("kills", 0), "deaths": st.get("deaths", 0), "assists": st.get("assists", 0),
        "headshots": shots.get("head", 0), "bodyshots": shots.get("body", 0), "legshots": shots.get("leg", 0),
        "damage_made": dmg.get("made", 0), "damage_received": dmg.get("received", 0),
        "loadout_avg": None, "party_id": None,
        "tier": st.get("tier", 0), "tier_name": None,
    }
    derived = {
        "match_id": meta["match_id"], "puuid": puuid, "map": meta["map"], "agent": agent,
        "won": won, "rounds": rounds,
        "kills": st.get("kills", 0), "deaths": st.get("deaths", 0), "assists": st.get("assists", 0),
        "score": st.get("score", 0), "damage": dmg.get("made", 0),
        "headshots": shots.get("head", 0), "bodyshots": shots.get("body", 0), "legshots": shots.get("leg", 0),
        # métricas profundas indisponíveis no resumo -> 0, com full_data=0:
        "first_bloods": 0, "first_deaths": 0, "trade_kills": 0, "deaths_traded": 0, "kast_rounds": 0,
        "mk2": 0, "mk3": 0, "mk4": 0, "mk5": 0,
        "atk_rounds": 0, "atk_wins": 0, "def_rounds": 0, "def_wins": 0,
        "eco_rounds": 0, "eco_wins": 0, "force_rounds": 0, "force_wins": 0,
        "full_rounds": 0, "full_wins": 0, "pistol_rounds": 0, "pistol_wins": 0,
        "clutches_json": "[]", "full_data": 0,
    }
    return {"meta": meta, "mp": mp, "derived": derived}


def strip_raw(match):
    """Strips bulky round-level player_locations from plant/defuse only.
    Kill-level player_locations are preserved for spatial analysis recomputation."""
    import copy
    m = copy.deepcopy(match)
    for rd in m.get("rounds", []):
        if rd.get("plant"):
            rd["plant"].pop("player_locations", None)
        if rd.get("defuse"):
            rd["defuse"].pop("player_locations", None)
    return m


def validate_match_metrics(match, puuid):
    """Cross-checks computed metrics against raw data to surface discrepancies.

    Returns a dict with any anomalies found; empty dict = clean.
    Discrepancies are also emitted as WARNING log lines.
    """
    import logging
    _log = logging.getLogger("validate")

    summary = player_summary(match, puuid)
    if not summary:
        return {"error": "player_not_found"}

    issues = {}
    rounds_list = player_round_rows(match, puuid)
    n_rounds = len(rounds_list) or 1

    # ACS check
    acs_computed = round(summary["score"] / n_rounds, 0)
    if summary["score"] == 0 and n_rounds > 5:
        issues["zero_score"] = {"rounds": n_rounds, "score": summary["score"]}
        _log.warning("VALIDATE %s: zero score over %d rounds", puuid[:8], n_rounds)

    # HS% check
    hs = summary["headshots"]
    bs = summary["bodyshots"]
    ls = summary["legshots"]
    total_hits = hs + bs + ls
    hs_pct = round(100 * hs / total_hits, 1) if total_hits else 0
    if total_hits == 0 and summary["kills"] > 0:
        issues["zero_shots_with_kills"] = {"kills": summary["kills"]}
        _log.warning("VALIDATE %s: %d kills but zero shot breakdown", puuid[:8], summary["kills"])
    if hs_pct > 80:
        issues["implausible_hs_pct"] = {"hs_pct": hs_pct, "hits": total_hits}
        _log.warning("VALIDATE %s: HS%% %.1f%% implausible", puuid[:8], hs_pct)

    # KD sanity
    if summary["kills"] > n_rounds * 5:
        issues["impossible_kills"] = {"kills": summary["kills"], "rounds": n_rounds}
        _log.warning("VALIDATE %s: %d kills in %d rounds (impossible)", puuid[:8], summary["kills"], n_rounds)

    # Damage check
    total_dmg = sum(r["damage"] for r in rounds_list)
    if total_dmg == 0 and summary["kills"] > 0:
        issues["zero_damage_with_kills"] = {"kills": summary["kills"]}
        _log.warning("VALIDATE %s: kills but zero damage recorded", puuid[:8])

    return {
        "puuid": puuid[:8],
        "acs": acs_computed,
        "hs_pct": hs_pct,
        "rounds": n_rounds,
        "issues": issues,
        "clean": len(issues) == 0,
    }
