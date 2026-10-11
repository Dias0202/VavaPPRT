"""Detalhe de partida e comparação com o lobby/elo.

Privacidade (política da Riot contra scouting): adversários e aliados de fora
do grupo aparecem só como linha do placar de uma partida em que um amigo
jogou — sem puuid, sem link, sem perfil navegável.
"""
from collections import defaultdict

from . import db
from .analytics import _conf, _kd, _rate, _rate_or_none
from .ingest import tier_name
from .metrics import classify_buy

MIN_LOBBY = 5        # partidas completas para a comparação com o lobby valer
MIN_BASELINE = 40    # jogadores-partida no elo para a média do elo valer


def _team_buy(avg_loadout):
    return classify_buy(avg_loadout) if avg_loadout is not None else "unknown"


def match_detail(match_id, roster_puuids, info_by):
    row = db.get_match_row(match_id)
    if not row:
        return None
    mps = db.match_players_for_match(match_id)
    if not any(p["puuid"] in roster_puuids for p in mps):
        return None  # só partidas em que alguém do grupo jogou

    base = {"match_id": match_id, "map": row["map"], "mode": row["mode"],
            "started_at": row["started_at"], "started_raw": row["started_raw"],
            "rounds": row["rounds"], "season": row["season"]}
    raw = db.get_raw_match(match_id) if row.get("has_raw") else None
    if not raw:
        # Partida de resumo: só a linha dos amigos, sem rounds.
        return {**base, "full": False, "teams": [], "rounds_detail": [], "kills": [],
                "players": [_summary_player(p, info_by, row["rounds"])
                            for p in mps if p["puuid"] in roster_puuids]}

    rounds = raw.get("rounds") or []
    n_rounds = len(rounds) or 1
    players = raw.get("players") or []
    team_of = {p["puuid"]: p.get("team_id") for p in players}
    name_of = {p["puuid"]: (p.get("name") or "?") for p in players}
    our_team = next((team_of[p] for p in team_of if p in roster_puuids), None)

    # First kills por jogador (placar) e por round (timeline).
    kills = sorted(raw.get("kills") or [],
                   key=lambda k: (k.get("round", 0), k.get("time_in_round_in_ms", 0)))
    first_by_round = {}
    fk = defaultdict(int)
    for k in kills:
        rid = k.get("round", 0)
        if rid not in first_by_round:
            first_by_round[rid] = k
            fk[(k.get("killer") or {}).get("puuid")] += 1

    parties = {}
    out_players = []
    for p in players:
        st = p.get("stats") or {}
        dmg = (st.get("damage") or {}).get("dealt") or 0
        shots = (st.get("headshots") or 0) + (st.get("bodyshots") or 0) + (st.get("legshots") or 0)
        pid = p.get("party_id")
        if pid and pid not in parties:
            parties[pid] = len(parties) + 1
        is_friend = p["puuid"] in roster_puuids
        info = info_by.get(p["puuid"], {}) if is_friend else {}
        out_players.append({
            "name": info.get("username") or p.get("name"),
            "tag": info.get("tagline") if is_friend else None,
            "riot_id": info.get("riot_id") if is_friend else None,
            "friend": is_friend,
            "team": p.get("team_id"),
            "agent": (p.get("agent") or {}).get("name"),
            "tier": (p.get("tier") or {}).get("id"),
            "tier_name": (p.get("tier") or {}).get("name"),
            "kills": st.get("kills", 0), "deaths": st.get("deaths", 0),
            "assists": st.get("assists", 0),
            "acs": round((st.get("score") or 0) / n_rounds),
            "adr": round(dmg / n_rounds, 1),
            "hs_pct": _rate(st.get("headshots") or 0, shots),
            "first_kills": fk.get(p["puuid"], 0),
            "party": pid,
        })
    # Party só importa quando tem 2+ pessoas: vira um número pequeno no placar.
    sizes = defaultdict(int)
    for p in out_players:
        sizes[p["party"]] += 1
    for p in out_players:
        p["party"] = parties.get(p["party"]) if p["party"] and sizes[p["party"]] > 1 else None
    out_players.sort(key=lambda p: (p["team"] != our_team, -p["acs"]))

    teams = [{"team": t.get("team_id"), "won": bool(t.get("won")),
              "rounds_won": (t.get("rounds") or {}).get("won"),
              "rounds_lost": (t.get("rounds") or {}).get("lost"),
              "ours": t.get("team_id") == our_team}
             for t in raw.get("teams") or []]
    teams.sort(key=lambda t: not t["ours"])

    timeline = []
    for rd in rounds:
        rid = rd.get("id")
        loadout = defaultdict(list)
        alive_friends = []
        for s in rd.get("stats") or []:
            pu = (s.get("player") or {}).get("puuid")
            lv = (s.get("economy") or {}).get("loadout_value")
            if lv is not None:
                loadout[team_of.get(pu)].append(lv)
        died = {(k.get("victim") or {}).get("puuid") for k in kills if k.get("round") == rid}
        for pu in team_of:
            if pu in roster_puuids and pu not in died:
                alive_friends.append(info_by.get(pu, {}).get("username") or name_of[pu])
        f = first_by_round.get(rid)
        plant = rd.get("plant") or {}
        timeline.append({
            "round": rid,
            "winner": rd.get("winning_team"),
            "won": rd.get("winning_team") == our_team if our_team else None,
            "result": rd.get("result"),
            "plant_site": plant.get("site"),
            "first_kill": None if not f else {
                "killer": name_of.get((f.get("killer") or {}).get("puuid"), "?"),
                "victim": name_of.get((f.get("victim") or {}).get("puuid"), "?"),
                "ours": (f.get("killer") or {}).get("team") == our_team,
            },
            "economy": {
                t: {"avg_loadout": round(sum(v) / len(v)) if v else None,
                    "buy": _team_buy(sum(v) / len(v) if v else None)}
                for t, v in loadout.items() if t},
            "friends_alive": alive_friends,
        })

    kill_pts = []
    for k in kills:
        loc = k.get("location") or {}
        if loc.get("x") is None:
            continue
        kp = (k.get("killer") or {}).get("puuid")
        vp = (k.get("victim") or {}).get("puuid")
        kill_pts.append({
            "round": k.get("round"), "x": loc.get("x"), "y": loc.get("y"),
            "ours": (k.get("killer") or {}).get("team") == our_team,
            "friend_killer": kp in roster_puuids, "friend_victim": vp in roster_puuids,
        })

    return {**base, "full": True, "our_team": our_team, "teams": teams,
            "players": out_players, "rounds_detail": timeline, "kills": kill_pts}


def _summary_player(p, info_by, rounds):
    info = info_by.get(p["puuid"], {})
    shots = (p.get("headshots") or 0) + (p.get("bodyshots") or 0) + (p.get("legshots") or 0)
    return {"name": info.get("username") or p.get("name"), "riot_id": info.get("riot_id"),
            "tag": info.get("tagline"), "friend": True, "team": p.get("team"),
            "agent": p.get("agent"), "tier": p.get("tier"), "tier_name": p.get("tier_name"),
            "kills": p.get("kills"), "deaths": p.get("deaths"), "assists": p.get("assists"),
            "acs": round((p.get("score") or 0) / max(1, rounds or 1)),
            "adr": round((p.get("damage_made") or 0) / max(1, rounds or 1), 1),
            "hs_pct": _rate(p.get("headshots") or 0, shots), "first_kills": None, "party": None}


# ---------------------------------------------------------------------------
# Você vs o lobby e vs o seu elo
# ---------------------------------------------------------------------------
def _line(score, kills, deaths, damage, hs, bs, ls, rounds):
    shots = hs + bs + ls
    return {"acs": round(score / max(1, rounds), 1), "kd": _kd(kills, deaths),
            "adr": round(damage / max(1, rounds), 1), "hs_pct": _rate(hs, shots)}


def vs_lobby(puuid):
    rows = db.lobby_rows_for_puuid(puuid)
    by_match = defaultdict(list)
    for r in rows:
        by_match[r["match_id"]].append(r)

    n = 0
    place_sum = team_place_sum = 0
    mvp = team_mvp = 0
    acc = defaultdict(lambda: defaultdict(float))  # grupo -> métrica -> soma
    my_tiers = defaultdict(int)
    mine_by_tier = defaultdict(lambda: defaultdict(float))
    for mid, ps in by_match.items():
        me = next((p for p in ps if p["puuid"] == puuid), None)
        if not me or len(ps) < 10:
            continue
        n += 1
        rounds = me.get("match_rounds") or 1
        ranked = sorted(ps, key=lambda p: p.get("score") or 0, reverse=True)
        place = 1 + ranked.index(me)
        mates = [p for p in ranked if p["team"] == me["team"]]
        tplace = 1 + mates.index(me)
        place_sum += place
        team_place_sum += tplace
        mvp += place == 1
        team_mvp += tplace == 1
        for grp, members in (("me", [me]),
                             ("team", [p for p in ps if p["team"] == me["team"] and p is not me]),
                             ("enemy", [p for p in ps if p["team"] != me["team"]])):
            for p in members:
                a = acc[grp]
                a["score"] += p.get("score") or 0
                a["kills"] += p.get("kills") or 0
                a["deaths"] += p.get("deaths") or 0
                a["damage"] += p.get("damage_made") or 0
                a["hs"] += p.get("headshots") or 0
                a["bs"] += p.get("bodyshots") or 0
                a["ls"] += p.get("legshots") or 0
                a["rounds"] += rounds
        if me.get("tier"):
            my_tiers[me["tier"]] += 1
            t = mine_by_tier[me["tier"]]
            for k, src in (("score", "score"), ("kills", "kills"), ("deaths", "deaths"),
                           ("damage", "damage_made"), ("hs", "headshots"),
                           ("bs", "bodyshots"), ("ls", "legshots")):
                t[k] += me.get(src) or 0
            t["rounds"] += rounds
            t["n"] += 1

    def line(a):
        return _line(a["score"], a["kills"], a["deaths"], a["damage"], a["hs"], a["bs"],
                     a["ls"], a["rounds"]) if a.get("rounds") else None

    lobby = {
        "matches": n, "confidence": _conf(n, MIN_LOBBY),
        "avg_place": round(place_sum / n, 2) if n else None,
        "avg_team_place": round(team_place_sum / n, 2) if n else None,
        "match_mvp_pct": _rate_or_none(mvp, n), "team_mvp_pct": _rate_or_none(team_mvp, n),
        "me": line(acc["me"]), "teammates": line(acc["team"]), "enemies": line(acc["enemy"]),
    }

    # Elo: o tier atual (ou o mais jogado) contra todo jogador já visto nesse
    # tier e nos vizinhos — tirando as suas próprias linhas da média.
    prof = db.rank_profile_for_puuid(puuid) or {}
    tier = prof.get("current_tier") or (max(my_tiers, key=my_tiers.get) if my_tiers else None)
    by_tier = {r["tier"]: r for r in db.tier_baselines()}
    rank = None
    if tier:
        band = [t for t in (tier - 1, tier, tier + 1) if t in by_tier]
        tot = defaultdict(float)
        for t in band:
            r = by_tier[t]
            for k in ("n", "score", "kills", "deaths", "damage", "hs", "bs", "ls", "rounds"):
                tot[k] += r[k] or 0
            mine = mine_by_tier.get(t) or {}
            for k in ("n", "score", "kills", "deaths", "damage", "hs", "bs", "ls", "rounds"):
                tot[k] -= mine.get(k, 0)
        base = _line(tot["score"], tot["kills"], tot["deaths"], tot["damage"], tot["hs"],
                     tot["bs"], tot["ls"], tot["rounds"]) if tot["rounds"] > 0 else None
        rank = {"tier": tier, "tier_name": prof.get("current_tier_name") or tier_name(tier),
                "band": [tier_name(t) for t in band],
                "sample": int(tot["n"]), "confidence": _conf(int(tot["n"]), MIN_BASELINE),
                "baseline": base, "me": lobby["me"]}
    return {"lobby": lobby, "rank": rank}
