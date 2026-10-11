"""Análises de grupo que o tracker.gg não faz: correlação por round do stack,
dupla (quem vinga quem, quem carrega) e plano de pré-fila para quem está on.

Unidade de análise: o **stack** — os amigos do roster que estavam no MESMO time
de uma partida. Com 6–10 pessoas no grupo nunca há "o grupo inteiro" num time
de 5, então tudo aqui é sobre o subconjunto que efetivamente jogou junto.
"""
from collections import defaultdict

from . import db
from .analytics import (MIN_AGENT, MIN_DUO, MIN_MAP, _conf, _rate, _rate_or_none,
                        all_maps, get_map_pool)

TRADE_WINDOW_MS = 4000
MIN_ROUNDS = 40  # rounds para um recorte de round virar conclusão

# Função de cada agente. Agente novo (lançado depois desta lista) cai em "?"
# e só perde o critério de diversidade — o resto da recomendação segue.
AGENT_ROLES = {
    "Jett": "Duelista", "Phoenix": "Duelista", "Reyna": "Duelista", "Raze": "Duelista",
    "Yoru": "Duelista", "Neon": "Duelista", "Iso": "Duelista", "Waylay": "Duelista",
    "Sova": "Iniciador", "Breach": "Iniciador", "Skye": "Iniciador", "KAY/O": "Iniciador",
    "Fade": "Iniciador", "Gekko": "Iniciador", "Tejo": "Iniciador",
    "Brimstone": "Controlador", "Viper": "Controlador", "Omen": "Controlador",
    "Astra": "Controlador", "Harbor": "Controlador", "Clove": "Controlador",
    "Sage": "Sentinela", "Cypher": "Sentinela", "Killjoy": "Sentinela",
    "Chamber": "Sentinela", "Deadlock": "Sentinela", "Vyse": "Sentinela", "Veto": "Sentinela",
}


def role_of(agent):
    return AGENT_ROLES.get(agent or "", "?")


def _label(info_by, p):
    i = info_by.get(p) or {}
    return i.get("username") or i.get("riot_id") or p[:6]


def _member(info_by, p):
    i = info_by.get(p) or {}
    return {"label": _label(info_by, p), "riot_id": i.get("riot_id"), "tagline": i.get("tagline")}


def stacks(roster_puuids, members=None):
    """Lista de stacks: {match_id, team, map, won, full, rounds, rows{puuid: mp}}.

    members=None -> todo stack com 2+ amigos. members=set -> só os stacks cujo
    conjunto é EXATAMENTE esse (mesma regra de /group/squads e /group/detail).
    """
    rp = set(roster_puuids)
    by = defaultdict(lambda: defaultdict(dict))
    for r in db.all_match_players_for_puuids(list(rp)):
        if r["puuid"] in rp:
            by[r["match_id"]][r["team"]][r["puuid"]] = r
    out = []
    for mid, teams in by.items():
        for team, rows in teams.items():
            if len(rows) < 2 and not (members and len(members) == 1):
                continue
            if members is not None and set(rows) != set(members):
                continue
            first = next(iter(rows.values()))
            out.append({"match_id": mid, "team": team, "map": first.get("map") or "?",
                        "won": bool(first.get("won")), "full": bool(first.get("full_match")),
                        "rounds": first.get("match_rounds") or 0,
                        "started_raw": first.get("started_raw") or 0, "rows": rows})
    out.sort(key=lambda s: s["started_raw"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Correlação do stack: mapa × composição, duelo de abertura, sobreviventes
# ---------------------------------------------------------------------------
def correlation(roster_puuids, info_by, members=None):
    st = stacks(roster_puuids, set(members) if members else None)

    combos = defaultdict(lambda: {"g": 0, "w": 0})
    for s in st:
        comp = " + ".join(sorted(r.get("agent") or "?" for r in s["rows"].values()))
        k = (s["map"], comp)
        combos[k]["g"] += 1
        combos[k]["w"] += 1 if s["won"] else 0

    # Nível de round, só partidas completas: quem abriu o round e quantos do
    # stack terminaram vivos, contra a vitória DO ROUND (não da partida).
    full = [s for s in st if s["full"]]
    team_of = {(s["match_id"], p): s["team"] for s in full for p in s["rows"]}
    stack_of = {s["match_id"]: s for s in full}
    puuids = sorted({p for s in full for p in s["rows"]})
    round_won = {}
    alive = defaultdict(int)
    for r in db.round_rows_for_matches(list(stack_of), puuids):
        s = stack_of.get(r["match_id"])
        if not s or r["puuid"] not in s["rows"]:
            continue
        key = (r["match_id"], r["round_id"])
        if r.get("round_won") is not None:
            round_won[key] = bool(r["round_won"])
        if r.get("alive_end"):
            alive[key] += 1

    opening = {"won": {"n": 0, "w": 0}, "lost": {"n": 0, "w": 0}}
    all_players = defaultdict(dict)  # match_id -> puuid -> team (todos os 10)
    for mid in stack_of:
        for p in db.match_players_for_match(mid):
            all_players[mid][p["puuid"]] = p["team"]
    for k in db.kills_for_matches(list(stack_of)):
        if not k.get("is_first"):
            continue
        key = (k["match_id"], k["round_id"])
        if key not in round_won:
            continue
        s = stack_of[k["match_id"]]
        killer_team = all_players[k["match_id"]].get(k["killer_puuid"])
        b = opening["won" if killer_team == s["team"] else "lost"]
        b["n"] += 1
        b["w"] += 1 if round_won[key] else 0

    surv = defaultdict(lambda: {"n": 0, "w": 0})
    for key, won in round_won.items():
        n = alive.get(key, 0)
        surv[n]["n"] += 1
        surv[n]["w"] += 1 if won else 0

    rounds_total = len(round_won)
    return {
        "members": [_member(info_by, p) for p in members] if members else None,
        "stacks_analyzed": len(st),
        "full_stacks": len(full),
        "rounds_analyzed": rounds_total,
        "confidence": _conf(rounds_total, MIN_ROUNDS),
        "map_agent_combos": sorted(
            ({"map": m, "agent_combo": c, "games": v["g"], "wins": v["w"],
              "win_rate": _rate(v["w"], v["g"]), "confidence": _conf(v["g"], MIN_AGENT)}
             for (m, c), v in combos.items()),
            key=lambda x: (x["games"], x["win_rate"]), reverse=True)[:12],
        "opening_duel": {
            k: {"rounds": v["n"], "win_rate": _rate_or_none(v["w"], v["n"])}
            for k, v in opening.items()},
        "survivors": [
            {"alive": n, "rounds": v["n"], "win_rate": _rate_or_none(v["w"], v["n"])}
            for n, v in sorted(surv.items())],
    }


# ---------------------------------------------------------------------------
# Dupla: juntos vs separados, quem carrega, quem vinga quem
# ---------------------------------------------------------------------------
def _acs(r):
    return (r.get("score") or 0) / max(1, r.get("match_rounds") or 0)


def duo(pa, pb, info_by):
    rows_by = defaultdict(dict)
    for r in db.all_match_players_for_puuids([pa, pb]):
        if r["puuid"] in (pa, pb):
            rows_by[r["match_id"]][r["puuid"]] = r
    together = [(mid, d[pa], d[pb]) for mid, d in rows_by.items()
                if pa in d and pb in d and d[pa]["team"] == d[pb]["team"]]
    against = [mid for mid, d in rows_by.items()
               if pa in d and pb in d and d[pa]["team"] != d[pb]["team"]]
    together.sort(key=lambda t: t[1].get("started_raw") or 0, reverse=True)
    tids = {t[0] for t in together}
    tg = len(together)
    tw = sum(1 for _, a, _ in together if a["won"])
    twr = _rate(tw, tg)

    def apart(p):
        rs = [r for r in db.derived_for_puuid(p) if r["match_id"] not in tids]
        return len(rs), sum(r["won"] or 0 for r in rs)

    lifts = {}
    for key, p in (("a", pa), ("b", pb)):
        n, w = apart(p)
        lifts[key] = {"name": _label(info_by, p), "apart_games": n, "apart_wr": _rate(w, n),
                      "lift": round(twr - _rate(w, n), 1) if n else None}

    # Quem carrega: ACS médio de cada um nas partidas juntos e quem topou mais.
    carry = {"a_acs": 0, "b_acs": 0, "a_top": 0, "b_top": 0}
    if tg:
        carry["a_acs"] = round(sum(_acs(a) for _, a, _ in together) / tg)
        carry["b_acs"] = round(sum(_acs(b) for _, _, b in together) / tg)
        carry["a_top"] = sum(1 for _, a, b in together if _acs(a) > _acs(b))
        carry["b_top"] = sum(1 for _, a, b in together if _acs(b) > _acs(a))

    # Quem vinga quem: B morre para X em t; A mata X até t+4s no mesmo round.
    trades = {"a_avenges_b": 0, "b_avenges_a": 0, "b_deaths": 0, "a_deaths": 0}
    by_round = defaultdict(list)
    for k in db.kills_for_matches([t[0] for t in together]):
        by_round[(k["match_id"], k["round_id"])].append(k)
    for ks in by_round.values():
        ks.sort(key=lambda k: k.get("time_ms") or 0)
        for i, k in enumerate(ks):
            for dead, avenger, cnt, dn in ((pb, pa, "a_avenges_b", "b_deaths"),
                                           (pa, pb, "b_avenges_a", "a_deaths")):
                if k["victim_puuid"] != dead:
                    continue
                trades[dn] += 1
                t0 = k.get("time_ms") or 0
                if any(k2["killer_puuid"] == avenger and k2["victim_puuid"] == k["killer_puuid"]
                       and 0 <= (k2.get("time_ms") or 0) - t0 <= TRADE_WINDOW_MS
                       for k2 in ks[i + 1:]):
                    trades[cnt] += 1
    trades["a_avenges_b_pct"] = _rate_or_none(trades["a_avenges_b"], trades["b_deaths"])
    trades["b_avenges_a_pct"] = _rate_or_none(trades["b_avenges_a"], trades["a_deaths"])

    by_map = defaultdict(lambda: {"g": 0, "w": 0})
    combos = defaultdict(lambda: {"g": 0, "w": 0})
    for _, a, b in together:
        by_map[a.get("map") or "?"]["g"] += 1
        by_map[a.get("map") or "?"]["w"] += 1 if a["won"] else 0
        c = f"{a.get('agent') or '?'} + {b.get('agent') or '?'}"
        combos[c]["g"] += 1
        combos[c]["w"] += 1 if a["won"] else 0

    return {
        "a": _member(info_by, pa), "b": _member(info_by, pb),
        "together": {"games": tg, "wins": tw, "win_rate": twr, "confidence": _conf(tg, MIN_DUO),
                     "full_games": sum(1 for _, a, _ in together if a.get("full_match"))},
        "against": {"games": len(against)},
        "form": [{"won": bool(a["won"]), "map": a.get("map"), "match_id": mid}
                 for mid, a, _ in together[:12]],
        "lift": lifts,
        "carry": carry,
        "trades": trades,
        "maps": sorted(({"map": m, "games": v["g"], "win_rate": _rate(v["w"], v["g"]),
                         "confidence": _conf(v["g"], 3)} for m, v in by_map.items()),
                       key=lambda x: (x["games"], x["win_rate"]), reverse=True),
        "agent_combos": sorted(({"combo": c, "games": v["g"], "win_rate": _rate(v["w"], v["g"])}
                                for c, v in combos.items()),
                               key=lambda x: (x["games"], x["win_rate"]), reverse=True)[:8],
    }


def synergy_matrix(roster_puuids, info_by):
    """Matriz N×N: WR de cada dupla no mesmo time (todas as partidas)."""
    pair = defaultdict(lambda: {"g": 0, "w": 0})
    for s in stacks(roster_puuids):
        ps = sorted(s["rows"])
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                pair[(ps[i], ps[j])]["g"] += 1
                pair[(ps[i], ps[j])]["w"] += 1 if s["won"] else 0
    players = sorted(roster_puuids, key=lambda p: _label(info_by, p).lower())
    cells = []
    for (a, b), v in pair.items():
        cells.append({"a": a, "b": b, "games": v["g"], "win_rate": _rate(v["w"], v["g"]),
                      "confidence": _conf(v["g"], MIN_DUO)})
    return {"players": [{"id": p, **_member(info_by, p)} for p in players], "cells": cells}


# ---------------------------------------------------------------------------
# Pré-fila: quem está on hoje -> mapas e agentes
# ---------------------------------------------------------------------------
def _smooth(w, n, prior=0.5, k=5):
    """Win rate com encolhimento para 50%: 3/3 não vence 30/50."""
    return (w + prior * k) / (n + k)


def pre_queue(members, info_by):
    """members: puuids de quem vai jogar agora (1–5).

    Por mapa da rotação: base de evidência mais forte disponível (o stack
    exato > duplas desse grupo > individual), veredito e uma escalação de
    agentes sem repetir agente e cobrindo funções.
    """
    members = list(dict.fromkeys(members))
    pool = get_map_pool() or all_maps()
    exact = defaultdict(lambda: {"g": 0, "w": 0})
    if len(members) >= 2:
        for s in stacks(members, set(members)):
            exact[s["map"]]["g"] += 1
            exact[s["map"]]["w"] += 1 if s["won"] else 0

    pairs = defaultdict(lambda: {"g": 0, "w": 0})
    if len(members) >= 2:
        for s in stacks(members):
            if len(s["rows"]) >= 2:
                pairs[s["map"]]["g"] += 1
                pairs[s["map"]]["w"] += 1 if s["won"] else 0

    indiv = defaultdict(lambda: {"g": 0, "w": 0})
    agent_stats = defaultdict(lambda: defaultdict(lambda: {"g": 0, "w": 0}))  # p -> (map, agent)
    agent_all = defaultdict(lambda: defaultdict(lambda: {"g": 0, "w": 0}))    # p -> agent
    for p in members:
        for r in db.derived_for_puuid(p):
            mp, ag, won = r["map"] or "?", r["agent"] or "?", 1 if r["won"] else 0
            indiv[mp]["g"] += 1
            indiv[mp]["w"] += won
            agent_stats[p][(mp, ag)]["g"] += 1
            agent_stats[p][(mp, ag)]["w"] += won
            agent_all[p][ag]["g"] += 1
            agent_all[p][ag]["w"] += won

    out = []
    for mp in pool:
        if exact[mp]["g"] >= MIN_MAP:
            basis, v = "stack exato", exact[mp]
        elif pairs[mp]["g"] >= MIN_MAP:
            basis, v = "duplas do grupo", pairs[mp]
        elif indiv[mp]["g"] >= MIN_MAP:
            basis, v = "individual", indiv[mp]
        else:
            basis, v = "sem amostra", {"g": max(exact[mp]["g"], pairs[mp]["g"], indiv[mp]["g"]), "w": 0}
        wr = _rate(v["w"], v["g"]) if basis != "sem amostra" else None
        if wr is None:
            verdict = "sem amostra"
        elif wr >= 55:
            verdict = "filar"
        elif wr < 45:
            verdict = "evitar"
        else:
            verdict = "neutro"
        out.append({"map": mp, "verdict": verdict, "basis": basis, "games": v["g"],
                    "win_rate": wr, "confidence": _conf(v["g"], MIN_MAP),
                    "lineup": _lineup(members, mp, agent_stats, agent_all, info_by)})

    order = {"filar": 0, "neutro": 1, "sem amostra": 2, "evitar": 3}
    out.sort(key=lambda m: (order[m["verdict"]], -(m["win_rate"] or 0)))
    return {"members": [_member(info_by, p) for p in members], "pool": pool, "maps": out}


def _lineup(members, mp, agent_stats, agent_all, info_by):
    """Escalação gulosa: maior score primeiro, sem repetir agente nem pessoa,
    com penalidade para função repetida (dois duelistas e nenhum smoke perde)."""
    cands = []
    for p in members:
        for (m, ag), v in agent_stats[p].items():
            if m == mp and ag != "?" and v["g"] >= 2:
                cands.append((_smooth(v["w"], v["g"]), p, ag, v["g"], v["w"], "mapa"))
        for ag, v in agent_all[p].items():
            if ag != "?" and v["g"] >= MIN_AGENT:
                # evidência geral vale menos que a do próprio mapa
                cands.append((_smooth(v["w"], v["g"]) - 0.03, p, ag, v["g"], v["w"], "geral"))
    taken_p, taken_a, roles = set(), set(), defaultdict(int)
    lineup = []
    while len(taken_p) < len(members):
        best = None
        for c in cands:
            score, p, ag = c[0], c[1], c[2]
            if p in taken_p or ag in taken_a:
                continue
            adj = score - 0.06 * roles[role_of(ag)] if role_of(ag) != "?" else score
            if best is None or adj > best[0]:
                best = (adj, c)
        if best is None:
            break
        _, (score, p, ag, g, w, src) = best
        taken_p.add(p)
        taken_a.add(ag)
        roles[role_of(ag)] += 1
        lineup.append({"player": _label(info_by, p), "agent": ag, "role": role_of(ag),
                       "games": g, "win_rate": _rate(w, g), "basis": src})
    for p in members:
        if p not in taken_p:
            lineup.append({"player": _label(info_by, p), "agent": None, "role": None,
                           "games": 0, "win_rate": None, "basis": "sem histórico"})
    return lineup
