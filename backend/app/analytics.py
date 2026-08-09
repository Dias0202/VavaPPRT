"""Camada de analytics — agrega as tabelas (sempre por puuid) nas respostas da API.

Princípio de produto: **gating de amostragem**. Toda quebra (mapa, agente,
dupla, clutch) carrega `n` e um `confidence` ('high'|'low'); abaixo do piso o
número aparece mas marcado como dado baixo, nunca vendido como conclusão.
"""
import json
from collections import defaultdict

from . import db

# Pisos mínimos de amostra
MIN_MAP = 5
MIN_AGENT = 3
MIN_DUO = 4
MIN_CLUTCH = 5
MIN_MATCHES = 3
MIN_SITE = 5           # plants no mesmo site para o aproveitamento valer
MIN_WEAPON_ROUNDS = 15  # rounds com a arma para o HS% dela valer
MIN_TREND = 10          # partidas em cada janela para comparar tendência
MIN_SESSION = 3         # partidas na sessão para ela entrar na análise de tilt


def _rate(part, whole, nd=1):
    return round(100 * part / whole, nd) if whole else 0.0


def _rate_or_none(part, whole, nd=1):
    """Como _rate, mas devolve None quando não há amostra.

    0.0% e "sem dados" são coisas diferentes: partidas de backfill (resumo) não
    têm rounds por lado, e exibir 0.0% ali afirmaria que o jogador nunca vence
    no ataque — ruído vendido como fato (PDD §4, princípio 2)."""
    return round(100 * part / whole, nd) if whole else None


def _kd(k, d):
    return round(k / max(1, d), 2)


def _conf(n, floor):
    return "high" if n >= floor else "low"


# ---------------------------------------------------------------------------
# Visão geral do jogador
# ---------------------------------------------------------------------------
def player_overview(puuid):
    rows = db.derived_for_puuid(puuid)
    n = len(rows)
    if n == 0:
        return {"matches": 0, "confidence": "low"}

    # Métricas básicas: TODAS as partidas (inclui backfill de resumo).
    # Métricas profundas (KAST/clutch/entrada/trade/economia/lado): só partidas
    # completas (full_data=1, vindas da ingestão v4) — senão o resumo as zeraria.
    full = [r for r in rows if r.get("full_data")]
    nf = len(full)

    def s(k):
        return sum(r.get(k) or 0 for r in rows)

    def sf(k):
        return sum(r.get(k) or 0 for r in full)

    rounds = s("rounds") or 1
    frounds = sf("rounds") or 1
    wins = s("won")
    shots = s("headshots") + s("bodyshots") + s("legshots")
    # ACS  = sum(score) / sum(rounds)  — matches Valorant Tracker (avg combat score per round)
    # ADR  = sum(damage) / sum(rounds)
    # HS%  = headshots / (headshots + bodyshots + legshots) × 100  — hit-based, same as tracker.gg
    # KAST = rounds_with_(kill OR assist OR survived OR traded) / total_full_rounds × 100

    # Clutches agregados por tamanho (só full).
    clutch_by_size = defaultdict(lambda: {"att": 0, "won": 0})
    for r in full:
        for c in json.loads(r.get("clutches_json") or "[]"):
            b = clutch_by_size[c["size"]]
            b["att"] += 1
            b["won"] += 1 if c["won"] else 0
    clutch_att = sum(b["att"] for b in clutch_by_size.values())
    clutch_won = sum(b["won"] for b in clutch_by_size.values())

    deaths = s("deaths")
    fdeaths = sf("deaths")
    return {
        "matches": n,
        "deep_matches": nf,
        "confidence": _conf(n, MIN_MATCHES),
        "deep_confidence": _conf(nf, MIN_MATCHES),
        "wins": wins, "losses": n - wins,
        "win_rate": _rate(wins, n),
        "kills": s("kills"), "deaths": deaths, "assists": s("assists"),
        "kd": _kd(s("kills"), deaths),
        "acs": round((s("score") / rounds), 0),
        "adr": round((s("damage") / rounds), 1),
        "hs_pct": _rate(s("headshots"), shots),
        "kast": _rate(sf("kast_rounds"), frounds),
        "first_bloods": sf("first_bloods"), "first_deaths": sf("first_deaths"),
        "fb_per_match": round(sf("first_bloods") / nf, 2) if nf else 0,
        "fd_per_match": round(sf("first_deaths") / nf, 2) if nf else 0,
        "trade_kills": sf("trade_kills"),
        "deaths_traded": sf("deaths_traded"),
        "deaths_untraded": fdeaths - sf("deaths_traded"),
        "trade_pct": _rate(sf("deaths_traded"), fdeaths),
        "multikills": {"2k": sf("mk2"), "3k": sf("mk3"), "4k": sf("mk4"), "5k": sf("mk5")},
        "clutch": {
            "attempted": clutch_att, "won": clutch_won,
            "win_rate": _rate(clutch_won, clutch_att),
            "by_size": {str(k): v for k, v in sorted(clutch_by_size.items())},
        },
        "economy": {
            "eco": {"n": s("eco_rounds"), "wr": _rate(s("eco_wins"), s("eco_rounds"))},
            "force": {"n": s("force_rounds"), "wr": _rate(s("force_wins"), s("force_rounds"))},
            "full": {"n": s("full_rounds"), "wr": _rate(s("full_wins"), s("full_rounds"))},
            "pistol": {"n": s("pistol_rounds"), "wr": _rate(s("pistol_wins"), s("pistol_rounds"))},
        },
        "sides": {
            "attack": {"n": s("atk_rounds"), "wr": _rate(s("atk_wins"), s("atk_rounds"))},
            "defense": {"n": s("def_rounds"), "wr": _rate(s("def_wins"), s("def_rounds"))},
        },
    }


# ---------------------------------------------------------------------------
# Por mapa
# ---------------------------------------------------------------------------
# Catálogo-semente de mapas competitivos. A ROTAÇÃO ativa é escolhida pelo
# admin do grupo (app_config: map_pool) — não faz sentido analisar mapa que
# ninguém pode filar hoje.
KNOWN_MAPS = ["Ascent", "Bind", "Breeze", "Fracture", "Haven", "Icebox", "Lotus",
              "Pearl", "Split", "Sunset", "Abyss", "Corrode", "Summit"]

MAP_POOL_KEY = "map_pool"


def all_maps():
    """Semente + o que realmente apareceu nas partidas do grupo.

    Lista fixa envelhece: a Riot lança mapa novo e ele fica invisível para o
    admin, que não consegue pô-lo na rotação. Unindo com o que o banco já viu,
    mapa novo aparece sozinho na primeira partida ingerida."""
    seen = [m for m in db.distinct_maps() if m]
    return sorted(dict.fromkeys(KNOWN_MAPS + seen))


def get_map_pool():
    """Mapas da rotação ativa. Vazio/ausente = todos (nada filtrado)."""
    raw = db.get_config(MAP_POOL_KEY)
    if not raw:
        return []
    try:
        pool = json.loads(raw)
        return [m for m in pool if isinstance(m, str)]
    except ValueError:
        return []


def set_map_pool(maps):
    valid = set(all_maps())
    clean = [m for m in dict.fromkeys(maps) if m in valid]
    db.set_config(MAP_POOL_KEY, json.dumps(clean))
    return clean


def in_pool(map_name, pool=None):
    pool = get_map_pool() if pool is None else pool
    return not pool or map_name in pool


def player_maps(puuid, only_pool=True):
    """Aproveitamento por mapa. Por padrão só os mapas da rotação ativa —
    saber que você vai bem num mapa fora do pool não ajuda a filar hoje."""
    rows = db.derived_for_puuid(puuid)
    pool = get_map_pool() if only_pool else []
    by = defaultdict(list)
    for r in rows:
        mp = r["map"] or "?"
        if pool and mp not in pool:
            continue
        by[mp].append(r)
    out = []
    for mp, rs in by.items():
        n = len(rs)
        rounds = sum(r["rounds"] or 0 for r in rs) or 1
        wins = sum(r["won"] for r in rs)
        out.append({
            "map": mp, "matches": n, "confidence": _conf(n, MIN_MAP),
            "win_rate": _rate(wins, n),
            "kd": _kd(sum(r["kills"] for r in rs), sum(r["deaths"] for r in rs)),
            "acs": round(sum(r["score"] for r in rs) / rounds, 0),
            "atk_wr": _rate_or_none(sum(r["atk_wins"] for r in rs), sum(r["atk_rounds"] for r in rs)),
            "def_wr": _rate_or_none(sum(r["def_wins"] for r in rs), sum(r["def_rounds"] for r in rs)),
        })
    out.sort(key=lambda x: (x["matches"], x["win_rate"]), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Por agente (forte/fraco)
# ---------------------------------------------------------------------------
def player_agents(puuid):
    rows = db.derived_for_puuid(puuid)
    by = defaultdict(list)
    for r in rows:
        by[r["agent"] or "?"].append(r)
    out = []
    for ag, rs in by.items():
        n = len(rs)
        rounds = sum(r["rounds"] or 0 for r in rs) or 1
        wins = sum(r["won"] for r in rs)
        wr = _rate(wins, n)
        acs = round(sum(r["score"] for r in rs) / rounds, 0)
        impact = min(100.0, acs / 3.0)
        out.append({
            "agent": ag, "matches": n, "confidence": _conf(n, MIN_AGENT),
            "win_rate": wr, "kd": _kd(sum(r["kills"] for r in rs), sum(r["deaths"] for r in rs)),
            "acs": acs, "score_index": round(0.6 * wr + 0.4 * impact, 1), "tag": "neutro",
        })
    # Ordena com os agentes que passam o piso de amostra SEMPRE na frente: um
    # 100% de WR em 2 jogos não pode encabeçar a lista acima de um agente com
    # amostra real (PDD §4, princípio 2).
    out.sort(key=lambda x: (x["matches"] >= MIN_AGENT, x["score_index"], x["matches"]),
             reverse=True)
    qualified = [a for a in out if a["matches"] >= MIN_AGENT]
    if qualified:
        qualified[0]["tag"] = "forte"
        if len(qualified) > 1:
            qualified[-1]["tag"] = "fraco"
    return out


# ---------------------------------------------------------------------------
# Armas (a partir dos kills)
# ---------------------------------------------------------------------------
def player_weapons(puuid):
    """Kills por arma + HS% por arma.

    Kills vêm da tabela `kills` (arma do abate). A precisão vem dos rounds:
    `player_round_stats` guarda os tiros do round e a arma comprada nele, então
    o HS% por arma é o agregado dos rounds em que aquela arma era a principal.
    É aproximado por construção — o v4 não dá acerto-a-acerto por arma — mas
    responde a pergunta que importa: "minha Vandal está pior que minha Phantom?"
    """
    kills = [k for k in db.kills_for_puuid(puuid) if k["killer_puuid"] == puuid]
    by = defaultdict(int)
    for k in kills:
        # Abate sem arma nomeada é habilidade de agente ou faca — não é uma
        # arma que exista no catálogo, e rotular "?" não diz nada a ninguém.
        by[k["weapon"] or "Habilidade / faca"] += 1
    total = sum(by.values()) or 1

    aim = defaultdict(lambda: {"hs": 0, "bs": 0, "ls": 0, "rounds": 0})
    for r in db.round_rows_for_puuid(puuid):
        w = r.get("weapon")
        if not w:
            continue
        a = aim[w]
        a["hs"] += r.get("headshots") or 0
        a["bs"] += r.get("bodyshots") or 0
        a["ls"] += r.get("legshots") or 0
        a["rounds"] += 1

    out = []
    for w, c in by.items():
        a = aim.get(w)
        hits = (a["hs"] + a["bs"] + a["ls"]) if a else 0
        out.append({
            "weapon": w, "kills": c, "share": _rate(c, total),
            "hs_pct": _rate_or_none(a["hs"], hits) if a else None,
            "aim_rounds": a["rounds"] if a else 0,
            "aim_confidence": _conf(a["rounds"] if a else 0, MIN_WEAPON_ROUNDS),
        })
    out.sort(key=lambda x: x["kills"], reverse=True)
    return {"total_kills": total, "weapons": out[:12]}


# ---------------------------------------------------------------------------
# Pós-plant, retake e sites
# ---------------------------------------------------------------------------
def player_postplant(puuid, map_name=None):
    """Aproveitamento depois da spike plantada, dos dois lados."""
    rows = [r for r in db.derived_for_puuid(puuid) if r.get("full_data")]
    if map_name:
        rows = [r for r in rows if r["map"] == map_name]
    if not rows:
        return {"matches": 0, "confidence": "low"}

    def s(k):
        return sum(r.get(k) or 0 for r in rows)

    sites = defaultdict(lambda: {"n": 0, "wins": 0})
    for r in rows:
        try:
            for site, v in json.loads(r.get("sites_json") or "{}").items():
                sites[site]["n"] += v.get("n", 0)
                sites[site]["wins"] += v.get("wins", 0)
        except (ValueError, AttributeError):
            continue

    site_list = [{"site": k, "plants": v["n"], "wins": v["wins"],
                  "win_rate": _rate(v["wins"], v["n"]),
                  "confidence": _conf(v["n"], MIN_SITE)}
                 for k, v in sites.items()]
    site_list.sort(key=lambda x: x["plants"], reverse=True)

    pp_r, rt_r = s("postplant_rounds"), s("retake_rounds")
    return {
        "matches": len(rows),
        "confidence": _conf(len(rows), MIN_MATCHES),
        "plants": s("plants"), "defuses": s("defuses"),
        "postplant": {"rounds": pp_r, "wins": s("postplant_wins"),
                      "win_rate": _rate_or_none(s("postplant_wins"), pp_r)},
        "retake": {"rounds": rt_r, "wins": s("retake_wins"),
                   "win_rate": _rate_or_none(s("retake_wins"), rt_r)},
        "sites": site_list,
    }


# ---------------------------------------------------------------------------
# Uso de utilidade
# ---------------------------------------------------------------------------
def player_utility(puuid):
    """Casts por round. Para iniciador/controlador, ACS mente — utilidade é o
    trabalho real. Fatiado por agente, que é onde a comparação faz sentido."""
    rows = [r for r in db.derived_for_puuid(puuid) if r.get("full_data")]
    if not rows:
        return {"matches": 0, "confidence": "low", "by_agent": []}

    def per_round(rs):
        rounds = sum(r.get("rounds") or 0 for r in rs) or 1
        c = {k: sum(r.get(f"cast_{k}") or 0 for r in rs)
             for k in ("grenade", "ability1", "ability2", "ultimate")}
        return {
            "rounds": rounds,
            "total_per_round": round(sum(c.values()) / rounds, 2),
            "grenade": round(c["grenade"] / rounds, 2),
            "ability1": round(c["ability1"] / rounds, 2),
            "ability2": round(c["ability2"] / rounds, 2),
            "ultimate": round(c["ultimate"] / rounds, 2),
        }

    by_agent = defaultdict(list)
    for r in rows:
        by_agent[r["agent"] or "?"].append(r)
    agents = [{"agent": ag, "matches": len(rs), "confidence": _conf(len(rs), MIN_AGENT),
               **per_round(rs)}
              for ag, rs in by_agent.items()]
    agents.sort(key=lambda x: (x["matches"] >= MIN_AGENT, x["total_per_round"]), reverse=True)
    return {"matches": len(rows), "confidence": _conf(len(rows), MIN_MATCHES),
            "overall": per_round(rows), "by_agent": agents}


# ---------------------------------------------------------------------------
# Heatmap (kills e mortes do jogador)
# ---------------------------------------------------------------------------
def player_heatmap(puuid, map_name=None):
    kills = db.kills_for_puuid(puuid)
    # Mapeia round->map por partida exigiria join; filtramos por match do mapa.
    match_ids = None
    if map_name:
        match_ids = {r["match_id"] for r in db.derived_for_puuid(puuid) if r["map"] == map_name}
    k_pts, d_pts = [], []
    for k in kills:
        if match_ids is not None and k["match_id"] not in match_ids:
            continue
        if k["vx"] is None:
            continue
        if k["killer_puuid"] == puuid:
            k_pts.append({"x": k["vx"], "y": k["vy"]})
        if k["victim_puuid"] == puuid:
            d_pts.append({"x": k["vx"], "y": k["vy"]})
    return {"map": map_name, "kills": k_pts, "deaths": d_pts}


# ---------------------------------------------------------------------------
# Sinergia de grupo (co-ocorrência no mesmo time)
# ---------------------------------------------------------------------------
def group_synergy(friend_puuids, info_by_puuid):
    """Win rate de duplas/trios/stacks que jogaram juntos (mesmo time).

    info_by_puuid: puuid -> {username, riot_id, tagline}.
    """
    rows = db.all_match_players_for_puuids(list(friend_puuids))
    # agrupa por partida: quais amigos, em qual time, ganhou?
    by_match = defaultdict(list)
    for r in rows:
        if r["puuid"] in friend_puuids:
            by_match[r["match_id"]].append(r)

    duo = defaultdict(lambda: {"games": 0, "wins": 0})
    stack_sizes = defaultdict(lambda: {"games": 0, "wins": 0})
    for mid, friends in by_match.items():
        # mesmo time
        by_team = defaultdict(list)
        for f in friends:
            by_team[f["team"]].append(f)
        for team, members in by_team.items():
            if len(members) < 2:
                continue
            won = bool(members[0]["won"])
            stack_sizes[len(members)]["games"] += 1
            stack_sizes[len(members)]["wins"] += 1 if won else 0
            ps = sorted(m["puuid"] for m in members)
            for i in range(len(ps)):
                for j in range(i + 1, len(ps)):
                    key = (ps[i], ps[j])
                    duo[key]["games"] += 1
                    duo[key]["wins"] += 1 if won else 0

    duos = []
    for (a, b), v in duo.items():
        ia = info_by_puuid.get(a, {})
        ib = info_by_puuid.get(b, {})
        duos.append({
            "a": ia.get("username", a[:8]), "b": ib.get("username", b[:8]),
            "a_riot": ia.get("riot_id"), "a_tag": ia.get("tagline"),
            "b_riot": ib.get("riot_id"), "b_tag": ib.get("tagline"),
            "games": v["games"], "wins": v["wins"], "win_rate": _rate(v["wins"], v["games"]),
            "confidence": _conf(v["games"], MIN_DUO),
        })
    duos.sort(key=lambda x: (x["games"] >= MIN_DUO, x["win_rate"], x["games"]), reverse=True)
    stacks = [{"size": k, "games": v["games"], "wins": v["wins"], "win_rate": _rate(v["wins"], v["games"])}
              for k, v in sorted(stack_sizes.items())]
    return {"duos": duos, "stacks": stacks}


# ---------------------------------------------------------------------------
# Grupos (2-5 pessoas que jogam juntas, mesmo time)
# ---------------------------------------------------------------------------
MIN_GROUP = 3  # piso de jogos para um grupo ser "confiável"


def _match_index(roster_puuids):
    """match_id -> {team -> [rows de membros do roster naquele time]}."""
    rows = db.all_match_players_for_puuids(list(roster_puuids))
    rp = set(roster_puuids)
    by_match = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["puuid"] in rp:
            by_match[r["match_id"]][r["team"]].append(r)
    return by_match


def squads(roster_puuids, info_by_puuid):
    """Grupos de 2-5 do roster que jogaram juntos (mesmo time), agregados pelo
    conjunto EXATO de membros presentes na partida."""
    by_match = _match_index(roster_puuids)
    groups = defaultdict(lambda: {"games": 0, "wins": 0, "maps": defaultdict(lambda: {"g": 0, "w": 0})})
    for mid, teams in by_match.items():
        for team, members in teams.items():
            if len(members) < 2:
                continue
            members = members[:5]  # grupos de até 5
            key = frozenset(m["puuid"] for m in members)
            won = bool(members[0]["won"])
            g = groups[key]
            g["games"] += 1
            g["wins"] += 1 if won else 0
            mp = members[0].get("map") or "?"
            g["maps"][mp]["g"] += 1
            g["maps"][mp]["w"] += 1 if won else 0

    out = []
    for key, v in groups.items():
        members = [{"label": info_by_puuid.get(p, {}).get("username", p[:6]),
                    "riot_id": info_by_puuid.get(p, {}).get("riot_id"),
                    "tagline": info_by_puuid.get(p, {}).get("tagline"),
                    "puuid": p} for p in key]
        members.sort(key=lambda m: m["label"].lower())
        best_map = max(v["maps"].items(), key=lambda kv: (kv[1]["g"], kv[1]["w"]), default=(None, None))
        out.append({
            "size": len(key), "members": members,
            "games": v["games"], "wins": v["wins"], "win_rate": _rate(v["wins"], v["games"]),
            "confidence": _conf(v["games"], MIN_GROUP),
            "best_map": best_map[0],
        })
    out.sort(key=lambda x: (x["size"], x["games"], x["win_rate"]), reverse=True)
    return out


def group_detail(puuids, info_by_puuid):
    """Detalhe acionável de um grupo (2-5): WR, forma recente, lift por membro,
    melhores/piores mapas pra fila, composições, e métricas PROFUNDAS do grupo
    junto (FB/clutch/KAST/trade das partidas completas compartilhadas)."""
    pset = set(puuids)
    # Índice com o roster INTEIRO, para enxergar outros membros no time e poder
    # exigir o conjunto EXATO (consistente com /group/squads).
    roster_set = set(info_by_puuid.keys()) | pset
    by_match = _match_index(roster_set)
    together = []  # (match_id, rows[], started_raw)
    together_ids = set()
    for mid, teams in by_match.items():
        for team, members in teams.items():
            mp = {m["puuid"]: m for m in members}
            if set(mp.keys()) == pset:
                rows = [mp[p] for p in puuids]
                together.append((mid, rows, rows[0].get("started_raw") or 0))
                together_ids.add(mid)
                break

    together.sort(key=lambda t: t[2], reverse=True)  # mais recente primeiro
    tg = len(together)
    tw = sum(1 for _, rows, _ in together if rows[0]["won"])
    together_wr = _rate(tw, tg)

    # Forma recente: últimas 12 partidas juntos (V/D), do mais recente p/ antigo.
    form = [{"won": bool(rows[0]["won"]), "map": rows[0].get("map")}
            for _, rows, _ in together[:12]]

    # Lift por membro + dados para métricas profundas (derived por membro).
    member_derived = {p: db.derived_for_puuid(p) for p in puuids}
    lifts = []
    for p in puuids:
        ap = [r for r in member_derived[p] if r["match_id"] not in together_ids]
        apw = sum(r["won"] for r in ap)
        lifts.append({
            "label": info_by_puuid.get(p, {}).get("username", p[:6]),
            "apart_wr": _rate(apw, len(ap)), "apart_games": len(ap),
            "lift": round(together_wr - _rate(apw, len(ap)), 1),
        })

    # Mapas juntos (o que filar / evitar) e composições.
    by_map = defaultdict(lambda: {"g": 0, "w": 0})
    comps = defaultdict(lambda: {"g": 0, "w": 0})
    for _, rows, _ in together:
        won = rows[0]["won"]
        mp = rows[0].get("map") or "?"
        by_map[mp]["g"] += 1
        by_map[mp]["w"] += 1 if won else 0
        comp = " + ".join(sorted(r.get("agent") or "?" for r in rows))
        comps[comp]["g"] += 1
        comps[comp]["w"] += 1 if won else 0
    maps = [{"map": m, "games": v["g"], "win_rate": _rate(v["w"], v["g"]),
             "confidence": _conf(v["g"], 3)} for m, v in by_map.items()]
    maps.sort(key=lambda x: (x["win_rate"], x["games"]), reverse=True)
    comp_list = [{"comp": c, "games": v["g"], "win_rate": _rate(v["w"], v["g"])}
                 for c, v in comps.items()]
    comp_list.sort(key=lambda x: (x["games"], x["win_rate"]), reverse=True)

    # Métricas PROFUNDAS do grupo junto: soma das stats dos membros nas partidas
    # COMPLETAS (full_data) que jogaram juntos.
    deep_ids = set()
    dfb = dfd = dtk = ddt = ddeaths = dkast = drounds = 0
    dmk = {2: 0, 3: 0, 4: 0, 5: 0}
    cl_att = cl_won = 0
    for p in puuids:
        for r in member_derived[p]:
            if r["match_id"] in together_ids and r.get("full_data"):
                deep_ids.add(r["match_id"])
                dfb += r["first_bloods"] or 0
                dfd += r["first_deaths"] or 0
                dtk += r["trade_kills"] or 0
                ddt += r["deaths_traded"] or 0
                ddeaths += r["deaths"] or 0
                dkast += r["kast_rounds"] or 0
                drounds += r["rounds"] or 0
                for n in (2, 3, 4, 5):
                    dmk[n] += r.get(f"mk{n}") or 0
                for c in json.loads(r.get("clutches_json") or "[]"):
                    cl_att += 1
                    cl_won += 1 if c["won"] else 0
    deep = {
        "games": len(deep_ids),
        "confidence": _conf(len(deep_ids), MIN_GROUP),
        "first_bloods": dfb, "first_deaths": dfd,
        "kast": _rate(dkast, drounds) if drounds else 0,
        "trade_pct": _rate(ddt, ddeaths) if ddeaths else 0,
        "clutch": {"won": cl_won, "attempted": cl_att, "win_rate": _rate(cl_won, cl_att)},
        "multikills": {"2k": dmk[2], "3k": dmk[3], "4k": dmk[4], "5k": dmk[5]},
    }

    members = [{"label": info_by_puuid.get(p, {}).get("username", p[:6]),
                "riot_id": info_by_puuid.get(p, {}).get("riot_id"),
                "tagline": info_by_puuid.get(p, {}).get("tagline")} for p in puuids]

    best = max(maps, key=lambda m: m["win_rate"], default=None) if maps else None
    worst = min(maps, key=lambda m: m["win_rate"], default=None) if maps else None
    return {
        "members": members, "size": len(puuids),
        "together": {"games": tg, "wins": tw, "win_rate": together_wr,
                     "confidence": _conf(tg, MIN_GROUP)},
        "form": form,
        "lifts": lifts,
        "maps": maps, "comps": comp_list[:8],
        "deep": deep,
        "best_map": best, "worst_map": worst,
        "recommendation": _group_reco(together_wr, tg, lifts, best, worst),
    }


def _group_reco(wr, games, lifts, best, worst):
    if games < MIN_GROUP:
        return f"Poucos jogos juntos ({games}). Joguem mais como grupo pra liberar recomendação confiável."
    parts = []
    if wr >= 55:
        parts.append(f"Grupo forte ({wr}% em {games} jogos) — vale filar junto.")
    elif wr < 45:
        parts.append(f"Grupo abaixo do esperado ({wr}% em {games}).")
    else:
        parts.append(f"Grupo equilibrado ({wr}% em {games}).")
    if best and best["games"] >= 3 and best["win_rate"] >= 60:
        parts.append(f"Priorizem {best['map']} ({best['win_rate']}%).")
    if worst and worst["games"] >= 3 and worst["win_rate"] <= 40:
        parts.append(f"Evitem {worst['map']} ({worst['win_rate']}%) ou treinem antes.")
    drag = min(lifts, key=lambda l: l["lift"]) if lifts else None
    if drag and drag["lift"] <= -8 and drag["apart_games"] >= 10:
        parts.append(f"{drag['label']} rende mais fora do grupo ({drag['lift']}pp) — revisar função.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Detalhe da dupla (juntos vs separados, mapas, combos de agente)
# ---------------------------------------------------------------------------
def duo_detail(pa, pb, name_a, name_b):
    rows = db.all_match_players_for_puuids([pa, pb])
    by_match = defaultdict(dict)
    for r in rows:
        if r["puuid"] in (pa, pb):
            by_match[r["match_id"]][r["puuid"]] = r

    together = []  # (match_id, row_a, row_b) mesmo time
    together_ids = set()
    for mid, d in by_match.items():
        if pa in d and pb in d and d[pa]["team"] == d[pb]["team"]:
            together.append((mid, d[pa], d[pb]))
            together_ids.add(mid)

    tg = len(together)
    tw = sum(1 for _, a, _ in together if a["won"])

    # Separados: cada um nas suas partidas SEM o outro no mesmo time.
    def apart(puuid):
        rs = db.derived_for_puuid(puuid)
        ap = [r for r in rs if r["match_id"] not in together_ids]
        w = sum(r["won"] for r in ap)
        return len(ap), w

    a_n, a_w = apart(pa)
    b_n, b_w = apart(pb)
    together_wr = _rate(tw, tg)

    # Melhores mapas juntos
    by_map = defaultdict(lambda: {"g": 0, "w": 0})
    combos = defaultdict(lambda: {"g": 0, "w": 0})
    a_k = a_d = b_k = b_d = 0
    for _, a, b in together:
        mp = a.get("map") or "?"
        by_map[mp]["g"] += 1
        by_map[mp]["w"] += 1 if a["won"] else 0
        combo = f"{a['agent']} + {b['agent']}"
        combos[combo]["g"] += 1
        combos[combo]["w"] += 1 if a["won"] else 0
        a_k += a["kills"] or 0; a_d += a["deaths"] or 0
        b_k += b["kills"] or 0; b_d += b["deaths"] or 0

    maps = [{"map": m, "games": v["g"], "win_rate": _rate(v["w"], v["g"])}
            for m, v in by_map.items()]
    maps.sort(key=lambda x: (x["games"], x["win_rate"]), reverse=True)
    combo_list = [{"combo": c, "games": v["g"], "win_rate": _rate(v["w"], v["g"])}
                  for c, v in combos.items()]
    combo_list.sort(key=lambda x: (x["games"], x["win_rate"]), reverse=True)

    return {
        "a": name_a, "b": name_b,
        "together": {"games": tg, "wins": tw, "win_rate": together_wr,
                     "confidence": _conf(tg, MIN_DUO)},
        "lift": {
            "a": {"name": name_a, "apart_wr": _rate(a_w, a_n), "apart_games": a_n,
                  "lift": round(together_wr - _rate(a_w, a_n), 1)},
            "b": {"name": name_b, "apart_wr": _rate(b_w, b_n), "apart_games": b_n,
                  "lift": round(together_wr - _rate(b_w, b_n), 1)},
        },
        "combined_kd": {"a": _kd(a_k, a_d), "b": _kd(b_k, b_d)},
        "maps": maps,
        "agent_combos": combo_list[:8],
    }


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------
def leaderboard(users):
    board = []
    for u in users:
        if not u.get("puuid"):
            continue
        ov = player_overview(u["puuid"])
        if ov["matches"] == 0:
            board.append({"username": u["username"], "riot_id": u["riot_id"], "tagline": u["tagline"],
                          "matches": 0, "win_rate": 0, "kd": 0, "acs": 0, "kast": 0})
            continue
        board.append({
            "username": u["username"], "riot_id": u["riot_id"], "tagline": u["tagline"],
            "matches": ov["matches"], "win_rate": ov["win_rate"], "kd": ov["kd"],
            "acs": ov["acs"], "kast": ov["kast"], "adr": ov["adr"],
        })
    board.sort(key=lambda x: (x["win_rate"], x["kd"]), reverse=True)
    for i, r in enumerate(board, 1):
        r["rank"] = i
    return board


# ---------------------------------------------------------------------------
# Spatial (extended positional heatmap with event context)
# ---------------------------------------------------------------------------
def player_spatial(puuid, map_name=None):
    """Position clusters for a player from kill-frame snapshots."""
    rows = db.spatial_events_for_puuid(puuid)
    if map_name:
        match_ids = {r["match_id"] for r in db.derived_for_puuid(puuid) if r["map"] == map_name}
        rows = [r for r in rows if r["match_id"] in match_ids]
    positions = [{"x": r["x"], "y": r["y"], "event_type": r["event_type"]}
                 for r in rows if r["x"] is not None]
    return {"map": map_name, "positions": positions, "count": len(positions)}


# ---------------------------------------------------------------------------
# Group correlation engine
# ---------------------------------------------------------------------------
def group_correlation(puuids, info_by_puuid):
    """For each map+agent_combo played by the EXACT group, computes win rate.
    Also surfaces first-blood impact on match outcome."""
    pset = set(puuids)
    roster_set = set(info_by_puuid.keys()) | pset
    by_match = _match_index(roster_set)

    together = []
    for mid, teams in by_match.items():
        for team, members in teams.items():
            mp = {m["puuid"]: m for m in members}
            if set(mp.keys()) == pset:
                together.append({
                    "match_id": mid,
                    "won": bool(members[0]["won"]),
                    "map": members[0].get("map") or "?",
                    "agents": {p: mp[p].get("agent") or "?" for p in pset},
                })
                break

    combo_map = defaultdict(lambda: {"g": 0, "w": 0})
    fb_impact = defaultdict(lambda: {"g": 0, "w": 0})

    member_derived = {p: {r["match_id"]: r for r in db.derived_for_puuid(p)} for p in pset}

    for t in together:
        mid = t["match_id"]
        agents_sorted = " + ".join(sorted(t["agents"].values()))
        key = (t["map"], agents_sorted)
        combo_map[key]["g"] += 1
        combo_map[key]["w"] += 1 if t["won"] else 0

        had_fb = any(
            (member_derived[p].get(mid) or {}).get("first_bloods", 0) > 0
            for p in pset
        )
        fb_key = "fb_secured" if had_fb else "fb_lost"
        fb_impact[fb_key]["g"] += 1
        fb_impact[fb_key]["w"] += 1 if t["won"] else 0

    return {
        "total_together": len(together),
        "map_agent_combos": [
            {
                "map": k[0], "agent_combo": k[1],
                "games": v["g"], "wins": v["w"],
                "win_rate": _rate(v["w"], v["g"]),
                "confidence": _conf(v["g"], MIN_AGENT),
            }
            for k, v in sorted(combo_map.items(), key=lambda x: x[1]["g"], reverse=True)
        ][:12],
        "first_blood_impact": {
            k: {"games": v["g"], "win_rate": _rate(v["w"], v["g"])}
            for k, v in fb_impact.items()
        },
    }


def round_correlation(puuids):
    """Round-level analysis: correlates number of group members alive at round end
    with match win rate. More survivors alive together = stronger closing."""
    pset = set(puuids)
    all_rounds = {}
    for p in pset:
        for r in db.round_rows_for_puuid(p):
            key = (r["match_id"], r["round_id"])
            if key not in all_rounds:
                all_rounds[key] = {"alive": set(), "match_id": r["match_id"]}
            if r["alive_end"]:
                all_rounds[key]["alive"].add(p)

    match_won = {}
    for p in pset:
        for r in db.derived_for_puuid(p):
            match_won[r["match_id"]] = r.get("won", 0)

    by_survivors = defaultdict(lambda: {"rounds": 0, "total": 0})
    for (mid, _rid), data in all_rounds.items():
        n_alive = len(data["alive"] & pset)
        if n_alive == 0:
            continue
        by_survivors[n_alive]["rounds"] += 1
        if match_won.get(mid):
            by_survivors[n_alive]["total"] += 1

    return {
        str(n): {"rounds": v["rounds"], "match_wins_when": v["total"]}
        for n, v in sorted(by_survivors.items())
    }


# ---------------------------------------------------------------------------
# Plano de fila — o que o capitão informal precisa antes de abrir a fila
# ---------------------------------------------------------------------------
def queue_plan(friend_puuids, info_by_puuid):
    """Para cada mapa da rotação: como o grupo vai nele e quem levar de agente.

    É a pergunta que o PDD chama de "decisão de fila", agora respondível porque
    a rotação ativa é conhecida: sem ela a lista misturava mapas que ninguém
    pode filar hoje.
    """
    pool = get_map_pool() or all_maps()
    puuids = list(friend_puuids)
    if not puuids:
        return {"pool": pool, "maps": []}

    # Partidas em que 2+ amigos jogaram juntos, por mapa.
    rows = db.all_match_players_for_puuids(puuids)
    by_match = defaultdict(list)
    for r in rows:
        if r["puuid"] in friend_puuids:
            by_match[r["match_id"]].append(r)

    per_map = defaultdict(lambda: {"games": 0, "wins": 0, "solo": 0, "solo_wins": 0})
    agent_map = defaultdict(lambda: defaultdict(lambda: {"n": 0, "w": 0}))
    for mid, friends in by_match.items():
        mp = friends[0].get("map") or "?"
        won = bool(friends[0].get("won"))
        if len(friends) >= 2:
            per_map[mp]["games"] += 1
            per_map[mp]["wins"] += 1 if won else 0
        else:
            per_map[mp]["solo"] += 1
            per_map[mp]["solo_wins"] += 1 if won else 0
        for f in friends:
            a = agent_map[mp][(f["puuid"], f.get("agent") or "?")]
            a["n"] += 1
            a["w"] += 1 if f.get("won") else 0

    out = []
    for mp in pool:
        st = per_map.get(mp, {"games": 0, "wins": 0, "solo": 0, "solo_wins": 0})
        n = st["games"]
        wr = _rate(st["wins"], n) if n else None
        # melhor agente por pessoa nesse mapa, com piso de amostra
        picks = []
        for (puuid, agent), v in agent_map.get(mp, {}).items():
            if v["n"] < MIN_AGENT or agent == "?":
                continue
            info = info_by_puuid.get(puuid) or {}
            picks.append({"player": info.get("username") or info.get("riot_id"),
                          "agent": agent, "games": v["n"],
                          "win_rate": _rate(v["w"], v["n"])})
        picks.sort(key=lambda p: (p["win_rate"], p["games"]), reverse=True)

        # Enquanto o grupo não tem partidas juntas suficientes, o palpite útil é
        # o desempenho individual. A base fica explícita para não dar a
        # entender que o número é do grupo.
        solo_wr = _rate_or_none(st["solo_wins"], st["solo"])
        if n >= MIN_MAP:
            basis, basis_n, basis_wr = "grupo", n, wr
        elif st["solo"] >= MIN_MAP:
            basis, basis_n, basis_wr = "individual", st["solo"], solo_wr
        else:
            basis, basis_n, basis_wr = "sem amostra", max(n, st["solo"]), None

        if basis_wr is None:
            verdict = "sem amostra"
        elif basis_wr >= 55:
            verdict = "filar"
        elif basis_wr < 45:
            verdict = "evitar"
        else:
            verdict = "neutro"

        out.append({
            "map": mp, "games": n, "wins": st["wins"], "win_rate": wr,
            "confidence": _conf(basis_n, MIN_MAP), "verdict": verdict,
            "basis": basis, "basis_games": basis_n, "basis_win_rate": basis_wr,
            "solo_games": st["solo"], "solo_win_rate": solo_wr,
            "picks": picks[:5],
        })

    order = {"filar": 0, "neutro": 1, "sem amostra": 2, "evitar": 3}
    out.sort(key=lambda m: (order[m["verdict"]], -(m["basis_win_rate"] or 0)))
    return {"pool": pool, "maps": out}


# ---------------------------------------------------------------------------
# Confronto direto entre dois amigos
# ---------------------------------------------------------------------------
def head_to_head(pa, pb, name_a, name_b):
    """Compara dois membros do grupo métrica a métrica. Puro bragging rights —
    e é o que mais faz gente abrir a plataforma."""
    def stats(puuid):
        rows = db.derived_for_puuid(puuid)
        full = [r for r in rows if r.get("full_data")]
        n = len(rows) or 1
        rounds = sum(r.get("rounds") or 0 for r in rows) or 1
        frounds = sum(r.get("rounds") or 0 for r in full) or 1
        shots = sum((r.get("headshots") or 0) + (r.get("bodyshots") or 0)
                    + (r.get("legshots") or 0) for r in rows)
        return {
            "matches": len(rows),
            "win_rate": _rate(sum(r.get("won") or 0 for r in rows), len(rows) or 1),
            "kd": _kd(sum(r.get("kills") or 0 for r in rows),
                      sum(r.get("deaths") or 0 for r in rows)),
            "acs": round(sum(r.get("score") or 0 for r in rows) / rounds, 0),
            "adr": round(sum(r.get("damage") or 0 for r in rows) / rounds, 1),
            "hs_pct": _rate(sum(r.get("headshots") or 0 for r in rows), shots),
            "kast": _rate(sum(r.get("kast_rounds") or 0 for r in full), frounds) if full else None,
            "fb_per_match": round(sum(r.get("first_bloods") or 0 for r in full) / len(full), 1) if full else None,
        }

    a, b = stats(pa), stats(pb)
    labels = {"win_rate": "Vitória", "kd": "K/D", "acs": "ACS", "adr": "ADR",
              "hs_pct": "HS%", "kast": "KAST", "fb_per_match": "First bloods/jogo"}
    rows = []
    wins_a = wins_b = 0
    for key, label in labels.items():
        va, vb = a.get(key), b.get(key)
        if va is None or vb is None:
            continue
        leader = "a" if va > vb else "b" if vb > va else "tie"
        wins_a += leader == "a"
        wins_b += leader == "b"
        rows.append({"metric": key, "label": label, "a": va, "b": vb, "leader": leader})

    # Partidas em que os dois jogaram juntos.
    ids_a = {r["match_id"] for r in db.derived_for_puuid(pa)}
    together = [r for r in db.derived_for_puuid(pb) if r["match_id"] in ids_a]
    tog_wins = sum(r.get("won") or 0 for r in together)

    return {
        "a": {"name": name_a, "matches": a["matches"], **{k: a[k] for k in labels if k in a}},
        "b": {"name": name_b, "matches": b["matches"], **{k: b[k] for k in labels if k in b}},
        "rows": rows,
        "score": {"a": wins_a, "b": wins_b},
        "together": {"games": len(together), "wins": tog_wins,
                     "win_rate": _rate_or_none(tog_wins, len(together)),
                     "confidence": _conf(len(together), MIN_DUO)},
    }


# ---------------------------------------------------------------------------
# Motor de insights (linguagem natural, com gating)
# ---------------------------------------------------------------------------
def insights(puuid):
    ov = player_overview(puuid)
    if ov["matches"] < MIN_MATCHES:
        return {"confidence": "low", "strengths": [], "gaps": [],
                "note": f"Poucas partidas ({ov['matches']}). Jogue mais para liberar análise confiável."}
    strengths, gaps = [], []

    deep = ov.get("deep_matches", 0)
    if deep < MIN_MATCHES:
        # Sem partidas completas suficientes, só dá pra falar do básico.
        if ov["win_rate"] >= 55:
            strengths.append(f"Boa taxa de vitória ({ov['win_rate']}%).")
        elif ov["win_rate"] < 45:
            gaps.append(f"Taxa de vitória baixa ({ov['win_rate']}%).")
        if ov["kd"] >= 1.1:
            strengths.append(f"K/D positivo ({ov['kd']}).")
        elif ov["kd"] < 0.9:
            gaps.append(f"K/D abaixo de 1 ({ov['kd']}).")
        return {"confidence": "low", "strengths": strengths[:5], "gaps": gaps[:5],
                "note": "Métricas profundas (clutch, entrada, KAST) liberam após mais partidas completas."}

    if ov["kast"] >= 72:
        strengths.append(f"KAST alto ({ov['kast']}%): você contribui na maioria dos rounds.")
    elif ov["kast"] < 65:
        gaps.append(f"KAST baixo ({ov['kast']}%): muitos rounds sem kill, assist, trade ou sobreviver.")

    if ov["fd_per_match"] >= 4 and ov["fd_per_match"] > ov["fb_per_match"] * 1.3:
        gaps.append(f"Morre cedo demais ({ov['fd_per_match']} first-deaths/jogo vs {ov['fb_per_match']} first-bloods).")
    if ov["fb_per_match"] >= 4:
        strengths.append(f"Entrada forte ({ov['fb_per_match']} first-bloods/jogo).")

    if ov["deaths"] and ov["trade_pct"] < 25:
        gaps.append(f"Morre isolado: só {ov['trade_pct']}% das suas mortes são vingadas. Jogue mais perto do time.")
    elif ov["trade_pct"] >= 40:
        strengths.append(f"Bem posicionado: {ov['trade_pct']}% das suas mortes são trocadas.")

    cl = ov["clutch"]
    if cl["attempted"] >= MIN_CLUTCH:
        if cl["win_rate"] >= 35:
            strengths.append(f"Clutch confiável ({cl['won']}/{cl['attempted']} = {cl['win_rate']}%).")
        elif cl["win_rate"] < 15:
            gaps.append(f"Clutch fraco ({cl['won']}/{cl['attempted']} = {cl['win_rate']}%) — treine 1vX.")

    eco = ov["economy"]
    if eco["pistol"]["n"] >= MIN_CLUTCH and eco["pistol"]["wr"] < 40:
        gaps.append(f"Pistol round fraco ({eco['pistol']['wr']}% de vitória).")
    if eco["eco"]["n"] >= MIN_CLUTCH and eco["eco"]["wr"] >= 35:
        strengths.append(f"Bom em eco ({eco['eco']['wr']}% de roubo de round).")

    sd = ov["sides"]
    if sd["attack"]["n"] >= 20 and sd["defense"]["n"] >= 20:
        diff = sd["attack"]["wr"] - sd["defense"]["wr"]
        if diff >= 12:
            strengths.append(f"Bem melhor no ataque ({sd['attack']['wr']}% vs {sd['defense']['wr']}% def).")
        elif diff <= -12:
            strengths.append(f"Bem melhor na defesa ({sd['defense']['wr']}% vs {sd['attack']['wr']}% atk).")

    return {"confidence": ov["confidence"], "strengths": strengths[:5], "gaps": gaps[:5]}
