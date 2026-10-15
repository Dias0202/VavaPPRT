"""Motor de fatos do X9 (docs/PLANO_X9_V2.md, Fase 4).

Transforma uma partida + o histórico de quem jogou em **ganchos**: frases
factuais, cada uma com o número que a prova e uma nota de **surpresa**
(raridade × tamanho). O LLM recebe só os melhores e escolhe o ângulo da
zoeira; nada aqui inventa, tudo sai do banco.

Também mora aqui:
- `match_factors`: por que a partida foi ganha/perdida, fatores por impacto;
- `night_summary`: a noite (sessão) de jogo do grupo;
- `nicknames`: apelidos conquistados pelos dados da semana.

Sem LLM e sem rede: recebe a partida já carregada (`botapi._Match`) e as
linhas do banco, então é testável com as fixtures.
"""
import statistics
from collections import defaultdict
from datetime import datetime

SESSION_GAP_S = 3 * 3600


def _acs(r):
    return (r.get("score") or 0) / max(1, r.get("rounds") or 1)


def _kd(k, d):
    return round(k / max(1, d), 2)


def _hook(tipo, texto, nota, quem=None, puuid=None):
    return {"tipo": tipo, "texto": texto, "nota": round(float(nota), 2),
            "quem": quem, "puuid": puuid}


def _ord(n):
    return f"{n}ª"


def _names(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " e " + names[-1]


# ---------------------------------------------------------------------------
# Números de uma partida a partir do raw (vale também quem só tem resumo)
# ---------------------------------------------------------------------------
def player_line(mv, puuid):
    p = next((x for x in mv.players if x["puuid"] == puuid), None) or {}
    st = p.get("stats") or {}
    n = max(1, mv.n_rounds)
    shots = (st.get("headshots") or 0) + (st.get("bodyshots") or 0) + (st.get("legshots") or 0)
    return {"score": st.get("score") or 0, "kills": st.get("kills") or 0,
            "deaths": st.get("deaths") or 0, "assists": st.get("assists") or 0,
            "rounds": n, "acs": round((st.get("score") or 0) / n),
            "damage": (st.get("damage") or {}).get("dealt") or 0,
            "hs_pct": round(100 * (st.get("headshots") or 0) / shots, 1) if shots else 0.0,
            "agent": mv.agent_of.get(puuid)}


def _round_buckets(mv):
    """Contagens por round usadas por ganchos e fatores."""
    out = {"fb": [], "fd": [], "full_lost": 0, "eco_won": 0, "pistols": [],
           "post_plant": [0, 0], "retake": [0, 0], "atk": [0, 0], "def": [0, 0],
           "bonus": []}
    for rd in mv.rounds:
        rid = rd["id"]
        won = mv.winners.get(rid) == mv.our
        ks = mv.kills.get(rid, [])
        if ks:
            (out["fb"] if ks[0]["killer"]["team"] == mv.our else out["fd"]).append(won)
        econ, _ = mv.economy(rd)
        ours, theirs = econ.get("nós", {}), econ.get("eles", {})
        pistol = rid in (0, 12)
        if pistol:
            out["pistols"].append(won)
        elif rid in (1, 13):
            out["bonus"].append(won)
        if not pistol and not won and ours.get("compra") == "full" and \
                theirs.get("compra") in ("eco", "force"):
            out["full_lost"] += 1
        if not pistol and won and ours.get("compra") == "eco":
            out["eco_won"] += 1
        atk = mv.attacker.get(rid)
        if atk is not None:
            side = "atk" if atk == mv.our else "def"
            out[side][0 if won else 1] += 1
            if (rd.get("plant") or {}).get("player"):
                key = "post_plant" if side == "atk" else "retake"
                out[key][0 if won else 1] += 1
    return out


def _deaths_untraded(mv, puuid):
    deaths = untraded = 0
    for rid, ks in mv.kills.items():
        for k in ks:
            if k["victim"]["puuid"] == puuid:
                deaths += 1
                if not mv.traded.get((rid, puuid)):
                    untraded += 1
    return deaths, untraded


def _zero_damage_deaths(mv, puuid):
    n = 0
    for rd in mv.rounds:
        died = any(k["victim"]["puuid"] == puuid for k in mv.kills.get(rd["id"], []))
        if not died:
            continue
        s = next((s for s in rd.get("stats") or []
                  if (s.get("player") or {}).get("puuid") == puuid), None)
        if s is not None and sum(d.get("damage", 0) for d in s.get("damage_events") or []) == 0:
            n += 1
    return n


def _first_duels(mv, puuid):
    fb = fd = 0
    for ks in mv.kills.values():
        if ks:
            fb += ks[0]["killer"]["puuid"] == puuid
            fd += ks[0]["victim"]["puuid"] == puuid
    return fb, fd


def _round_kills(mv, puuid):
    """[(round 1-based, kills)] do jogador."""
    out = []
    for i, rd in enumerate(mv.rounds, 1):
        k = sum(1 for x in mv.kills.get(rd["id"], []) if x["killer"]["puuid"] == puuid)
        if k:
            out.append((i, k))
    return out


def _streak(results):
    """results: mais novo primeiro. (resultado, tamanho) da sequência atual."""
    if not results:
        return None, 0
    first, n = results[0], 0
    for r in results:
        if r != first:
            break
        n += 1
    return first, n


# ---------------------------------------------------------------------------
# Ganchos de uma partida
# ---------------------------------------------------------------------------
def match_hooks(mv, ctx, history, rank_rows=None, prev_ranks=None, limit=8):
    """Ganchos ranqueados por surpresa.

    history: {puuid: [linhas de player_match_derived com started_raw], mais novas primeiro}
    rank_rows: {puuid: linha do rank_history desta partida}
    prev_ranks: {puuid: linha do rank_history anterior a esta partida}
    """
    rank_rows, prev_ranks = rank_rows or {}, prev_ranks or {}
    mid = mv.match_id
    friends = [p for p in mv.team_of if p in ctx.friends]
    hooks = []
    o, t = mv.final_score()
    won = o > t
    started = None
    streaks = defaultdict(list)  # (resultado, n) -> nomes

    rnum = {rd["id"]: i for i, rd in enumerate(mv.rounds, 1)}
    worst_untraded = max((_deaths_untraded(mv, p["puuid"])[1] for p in mv.players), default=0)
    lobby = sorted(mv.players, key=lambda p: -((p.get("stats") or {}).get("score") or 0))
    top, bottom = lobby[0]["puuid"] if lobby else None, lobby[-1]["puuid"] if lobby else None

    for pu in friends:
        name = mv.label(pu)
        rows = history.get(pu) or []
        this = next((r for r in rows if r["match_id"] == mid), None)
        started = started or (this or {}).get("started_raw")
        line = player_line(mv, pu)
        before = [r for r in rows if r["match_id"] != mid and
                  (not this or (r.get("started_raw") or 0) < (this.get("started_raw") or 0))]
        acs_now = line["acs"]

        # --- contra a própria média / recordes
        accs = [_acs(r) for r in before]
        if len(accs) >= 15 and acs_now > max(accs):
            hooks.append(_hook("recorde", f"{name} fez o MAIOR ACS da história dele no banco: "
                                          f"{acs_now} (antes: {round(max(accs))}, em {len(accs)} partidas)",
                               3.2, name, pu))
        elif len(accs) >= 15 and acs_now < min(accs):
            hooks.append(_hook("pior_da_vida", f"{name} fez o PIOR ACS da história dele no banco: "
                                               f"{acs_now} (média {round(statistics.mean(accs))})",
                               3.2, name, pu))
        elif len(accs) >= 5:
            window = accs[:30]
            mean = statistics.mean(window)
            sd = max(statistics.pstdev(window), 15.0)
            z = (acs_now - mean) / sd
            if acs_now > max(window):
                hooks.append(_hook("melhor_recente", f"{name}: melhor ACS das últimas {len(window)} "
                                                     f"partidas dele ({acs_now}; média {round(mean)})",
                                   2.2 + min(len(window), 30) / 30, name, pu))
            elif acs_now < min(window):
                hooks.append(_hook("pior_recente", f"{name}: pior ACS das últimas {len(window)} "
                                                   f"partidas dele ({acs_now}; média {round(mean)})",
                                   2.2 + min(len(window), 30) / 30, name, pu))
            elif abs(z) >= 1.2:
                hooks.append(_hook("vs_media", f"{name}: ACS {acs_now}, "
                                               f"{'bem acima' if z > 0 else 'bem abaixo'} da média "
                                               f"dele ({round(mean)})", abs(z), name, pu))
        kills_before = [r.get("kills") or 0 for r in before]
        if len(kills_before) >= 10 and line["kills"] > max(kills_before):
            hooks.append(_hook("recorde", f"recorde de kills do {name} no banco: {line['kills']} "
                                          f"(antes: {max(kills_before)})", 3.0, name, pu))

        # --- agente novo
        if len(before) >= 5 and line["agent"] and \
                not any((r.get("agent") or "") == line["agent"] for r in before):
            hooks.append(_hook("estreia", f"estreia do {name} de {line['agent']}: "
                                          f"{line['kills']}/{line['deaths']}/{line['assists']}",
                               1.6, name, pu))

        # --- sequência individual
        res = ([bool(this["won"])] if this else [won if mv.team_of.get(pu) == mv.our else not won]) \
            + [bool(r.get("won")) for r in before]
        r0, n = _streak(res)
        if n >= 3:
            streaks[(r0, n)].append(name)

        # --- erros e destaques do jogo
        # Pela definição do projeto (assassino morto em até 4 s) a maioria das
        # mortes fica sem trade; o fato só surpreende quando é o pior do lobby.
        deaths, untraded = _deaths_untraded(mv, pu)
        if untraded >= 8 and untraded == worst_untraded:
            hooks.append(_hook("sem_trade", f"{name} morreu {untraded} vezes sem ninguém vingar "
                                            f"(de {deaths}) — o mais abandonado do lobby",
                               1.2 + (untraded - 8) * 0.1, name, pu))
        zero = _zero_damage_deaths(mv, pu)
        if zero >= 6:
            hooks.append(_hook("sem_dano", f"{name} morreu sem dar dano em {zero} rounds",
                               1.2 + (zero - 6) * 0.15, name, pu))
        fb, fd = _first_duels(mv, pu)
        if fd >= 5 and fd > fb:
            hooks.append(_hook("first_death", f"{name} morreu primeiro em {fd} rounds "
                                              f"(abriu só {fb})", 1.0 + (fd - 5) * 0.2, name, pu))
        elif fb >= 6:
            hooks.append(_hook("first_blood", f"{name} abriu {fb} rounds matando primeiro",
                               1.0 + (fb - 6) * 0.2, name, pu))
        for rnd, k in _round_kills(mv, pu):
            if k >= 5:
                hooks.append(_hook("ace", f"ACE do {name} no round {rnd}", 3.0, name, pu))
            elif k == 4:
                hooks.append(_hook("4k", f"{name} fez 4 kills no round {rnd}", 1.4, name, pu))
        for rid, cls in mv.clutches.items():
            for cl in cls:
                if cl["puuid"] == pu and cl["won"] and cl["size"] >= 2:
                    hooks.append(_hook("clutch", f"{name} ganhou um clutch 1v{cl['size']} "
                                                 f"no round {rnum.get(rid, '?')}",
                                       1.5 + 0.5 * cl["size"], name, pu))

        # --- lugar no lobby
        if pu == top:
            if mv.team_of.get(pu) == mv.our and not won:
                hooks.append(_hook("top_lobby", f"{name} foi o top 1 do lobby e mesmo assim perdeu",
                                   2.2, name, pu))
            else:
                hooks.append(_hook("top_lobby", f"{name} foi o top 1 do lobby ({acs_now} de ACS)",
                                   1.8, name, pu))
        elif pu == bottom:
            hooks.append(_hook("ultimo_lobby", f"{name} foi o ÚLTIMO do lobby inteiro "
                                               f"({line['kills']}/{line['deaths']}/{line['assists']})",
                               2.0, name, pu))

        # --- RR e elo
        rr = rank_rows.get(pu)
        if rr:
            prev = prev_ranks.get(pu)
            change = rr.get("rr_change")
            if prev and rr.get("tier") and prev.get("tier") and rr["tier"] != prev["tier"]:
                up = rr["tier"] > prev["tier"]
                hooks.append(_hook("elo", f"{name} {'SUBIU pra' if up else 'CAIU pro'} "
                                          f"{rr.get('tier_name')}"
                                          + (f" ({change:+d} RR)" if change is not None else ""),
                                   3.5, name, pu))
            elif change is not None and abs(change) >= 25:
                hooks.append(_hook("rr", f"{name}: {change:+d} RR nessa", 1.3, name, pu))
            elif change is not None and change > 0 and (rr.get("rr") or 0) >= 85:
                hooks.append(_hook("quase_elo", f"{name} tá a {100 - rr['rr']} RR de subir de elo",
                                   1.0, name, pu))

        # --- marco
        total = len(rows) if this else len(rows) + 1
        if total >= 100 and total % 100 == 0:
            hooks.append(_hook("marco", f"{_ord(total)} partida do {name} registrada no banco",
                               1.5, name, pu))

    for (res, n), names in streaks.items():
        what = "vitória" if res else "derrota"
        hooks.append(_hook("sequencia", f"{_ord(n)} {what} seguida de {_names(sorted(names))}",
                           min(4.0, 0.7 * n) + (0.3 if len(names) > 1 else 0)))

    # --- do time
    margin = abs(o - t)
    if not won and margin >= 8:
        hooks.append(_hook("atropelo", f"atropelados: {o}x{t}", 2.0))
    elif won and margin >= 8:
        hooks.append(_hook("atropelo", f"atropelo: {o}x{t}", 1.6))
    if mv.n_rounds > 24:
        hooks.append(_hook("prorrogacao", f"foi pra prorrogação ({o}x{t})", 1.6))
    worst_def = best_lead = 0
    for rd in mv.rounds:
        a, b = mv.score_before(rd["id"] + 1)
        worst_def, best_lead = max(worst_def, b - a), max(best_lead, a - b)
    if won and worst_def >= 4:
        hooks.append(_hook("virada", f"viramos depois de estar {worst_def} rounds atrás", 2.6))
    if not won and best_lead >= 4:
        hooks.append(_hook("virada", f"tomamos virada depois de abrir {best_lead} rounds", 2.8))

    b = _round_buckets(mv)
    n = max(1, mv.n_rounds)
    if len(b["fd"]) >= 0.6 * n:
        w = sum(b["fd"])
        hooks.append(_hook("abertura", f"tomamos first blood em {len(b['fd'])} de {n} rounds "
                                       f"e ganhamos {w} desses", 1.4 + (len(b["fd"]) / n - 0.6) * 4))
    elif len(b["fb"]) >= 0.6 * n and not won:
        hooks.append(_hook("abertura", f"abrimos {len(b['fb'])} de {n} rounds com first blood "
                                       f"e mesmo assim perdemos", 1.8))
    if b["full_lost"] >= 2:
        hooks.append(_hook("economia", f"perdemos {b['full_lost']} rounds de full buy pra eco/force",
                           1.0 + 0.4 * b["full_lost"]))
    if b["eco_won"] >= 2:
        hooks.append(_hook("economia", f"roubamos {b['eco_won']} rounds de eco", 1.2 + 0.3 * b["eco_won"]))
    if len(b["pistols"]) == 2 and not any(b["pistols"]):
        hooks.append(_hook("pistol", "perdemos os dois pistols", 0.9))
    elif len(b["pistols"]) == 2 and all(b["pistols"]):
        hooks.append(_hook("pistol", "ganhamos os dois pistols", 0.7))

    hooks += _map_hooks(mv, ctx, friends, history, won)
    hooks += _night_hooks(mv, friends, history, started)

    seen, out = set(), []
    for h in sorted(hooks, key=lambda h: -h["nota"]):
        if h["texto"] in seen:
            continue
        seen.add(h["texto"])
        out.append(h)
    return out[:limit]


def _map_hooks(mv, ctx, friends, history, won):
    """Retrospecto do stack (ou do jogador) no mapa desta partida."""
    stack = [p for p in friends if mv.team_of.get(p) == mv.our]
    if not stack or not mv.map:
        return []
    by_match = defaultdict(dict)
    for p in stack:
        for r in history.get(p) or []:
            if r.get("map") == mv.map:
                by_match[r["match_id"]][p] = r
    games = []  # (started_raw, won) em que TODO o stack jogou no mesmo time
    for mid, rows in by_match.items():
        if len(rows) == len(stack) and len({bool(r.get("won")) for r in rows.values()}) == 1:
            r = next(iter(rows.values()))
            games.append((r.get("started_raw") or 0, bool(r.get("won"))))
    if mv.match_id not in by_match:
        games.append((10 ** 12, won))
    games.sort(reverse=True)
    who = _names(sorted(mv.label(p) for p in stack))
    juntos = " juntos" if len(stack) > 1 else ""
    out = []
    res, n = _streak([g[1] for g in games])
    if n >= 3:
        out.append(_hook("mapa", f"{_ord(n)} {'vitória' if res else 'derrota'} seguida de {who}"
                                 f"{juntos} na {mv.map}", 1.4 + 0.3 * n))
    w = sum(1 for g in games if g[1])
    floor = 4 if len(stack) > 1 else 6
    if len(games) >= floor:
        wr = w / len(games)
        if wr <= 0.35:
            out.append(_hook("mapa", f"{who}{juntos} na {mv.map}: {w}V {len(games) - w}D no banco",
                             1.5 + (0.35 - wr) * 3))
        elif wr >= 0.65:
            out.append(_hook("mapa", f"{who}{juntos} na {mv.map}: {w}V {len(games) - w}D no banco",
                             1.2 + (wr - 0.65) * 3))
    return out


def _night_hooks(mv, friends, history, started):
    if not started:
        return []
    rows = {}
    for p in friends:
        for r in history.get(p) or []:
            s = r.get("started_raw") or 0
            if started - 12 * 3600 <= s <= started:
                rows.setdefault(r["match_id"], r)
    chain = session_chain(sorted(rows.values(), key=lambda r: -(r.get("started_raw") or 0)))
    k = len(chain)
    if k < 3:
        return []
    w = sum(1 for r in chain if r.get("won"))
    nota = 0.8 + (0.5 if k - 2 * w >= 2 else 0) + 0.2 * (k - 3)
    return [_hook("noite", f"{_ord(k)} partida da noite; a noite tá {w}V {k - w}D", min(nota, 2.5))]


def session_chain(rows, gap=SESSION_GAP_S):
    """Linhas (mais novas primeiro) até o primeiro buraco maior que `gap`."""
    out = []
    for r in rows:
        if out and (out[-1].get("started_raw") or 0) - (r.get("started_raw") or 0) > gap:
            break
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Por que ganhamos/perdemos
# ---------------------------------------------------------------------------
def match_factors(mv, hooks=()):
    """Fatores da partida inteira, ordenados pelo que mais pesou no resultado."""
    o, t = mv.final_score()
    won = o > t
    b = _round_buckets(mv)
    f = []

    def wl(lst):
        return sum(lst), len(lst) - sum(lst)

    fbw, fbl = wl(b["fb"])
    fdw, fdl = wl(b["fd"])
    f.append({"fator": "aberturas",
              "resumo": f"abrimos {len(b['fb'])} rounds com first blood (ganhamos {fbw}); "
                        f"tomamos a abertura em {len(b['fd'])} (ganhamos {fdw})",
              "impacto": len(b["fd"]) - len(b["fb"])})

    ours_dead = theirs_dead = ours_untraded = theirs_untraded = 0
    for rid, ks in mv.kills.items():
        for k in ks:
            mine = k["victim"]["team"] == mv.our
            traded = bool(mv.traded.get((rid, k["victim"]["puuid"])))
            if mine:
                ours_dead += 1
                ours_untraded += not traded
            else:
                theirs_dead += 1
                theirs_untraded += not traded
    f.append({"fator": "trades",
              "resumo": f"{ours_untraded} de {ours_dead} mortes nossas sem trade; "
                        f"deles: {theirs_untraded} de {theirs_dead}",
              "impacto": round((ours_untraded - theirs_untraded) / 3, 1)})
    f.append({"fator": "economia",
              "resumo": f"perdemos {b['full_lost']} full buys pra eco/force; "
                        f"ganhamos {b['eco_won']} rounds de eco",
              "impacto": b["full_lost"] - b["eco_won"]})
    pw, pl = b["post_plant"]
    rw, rl = b["retake"]
    f.append({"fator": "pós-plant e retake",
              "resumo": f"com a spike plantada por nós: {pw}V {pl}D; "
                        f"defendendo spike plantada: {rw}V {rl}D",
              "impacto": pl - rw})
    aw, al = b["atk"]
    dw, dl = b["def"]
    f.append({"fator": "lados", "resumo": f"ataque {aw}V {al}D; defesa {dw}V {dl}D",
              "impacto": round(abs((al - aw) - (dl - dw)) / 2, 1)})
    pw2, pl2 = wl(b["pistols"])
    bw, bl = wl(b["bonus"])
    f.append({"fator": "pistols", "resumo": f"pistols {pw2}V {pl2}D; round seguinte {bw}V {bl}D",
              "impacto": (pl2 - pw2) * 1.5})
    below = [h["texto"] for h in hooks if h["tipo"] in ("pior_recente", "pior_da_vida")
             or (h["tipo"] == "vs_media" and "abaixo" in h["texto"])]
    above = [h["texto"] for h in hooks if h["tipo"] in ("melhor_recente", "recorde")
             or (h["tipo"] == "vs_media" and "acima" in h["texto"])]
    f.sort(key=lambda x: x["impacto"], reverse=not won)
    return {"placar": f"{o}x{t}", "resultado": "vitória" if won else "derrota" if o < t else "empate",
            "fatores": f,
            "leitura": ("fatores do que MAIS PESOU contra (impacto maior = pior)" if not won
                        else "fatores ordenados do que MAIS AJUDOU (impacto menor = melhor)"),
            "abaixo_da_media": below, "acima_da_media": above}


# ---------------------------------------------------------------------------
# A noite (sessão) do grupo
# ---------------------------------------------------------------------------
def night_summary(rows_by_puuid, labels, rank_rows=(), now=None, gap=SESSION_GAP_S):
    """rows_by_puuid: {puuid: linhas derivadas recentes (mais novas primeiro)}."""
    now = now or datetime.now().timestamp()
    unique = {}
    for pu, rows in rows_by_puuid.items():
        for r in rows:
            unique.setdefault(r["match_id"], r)
    chain = session_chain(sorted(unique.values(), key=lambda r: -(r.get("started_raw") or 0)), gap)
    if not chain:
        return {"tem_noite": False}
    ids = {r["match_id"] for r in chain}
    last = chain[0].get("started_raw") or 0
    rr = defaultdict(int)
    rr_n = defaultdict(int)
    for r in rank_rows:
        if r.get("match_id") in ids and r.get("rr_change") is not None:
            rr[r["puuid"]] += r["rr_change"]
            rr_n[r["puuid"]] += 1
    players = []
    for pu, rows in rows_by_puuid.items():
        mine = [r for r in rows if r["match_id"] in ids]
        if not mine:
            continue
        mine.sort(key=lambda r: r.get("started_raw") or 0)
        w = sum(1 for r in mine if r.get("won"))
        k = sum(r.get("kills") or 0 for r in mine)
        d = sum(r.get("deaths") or 0 for r in mine)
        p = {"nome": labels.get(pu, "?"), "partidas": len(mine), "vitorias": w,
             "derrotas": len(mine) - w, "kd": _kd(k, d),
             "acs": round(sum(_acs(r) for r in mine) / len(mine)),
             "rr": rr[pu] if rr_n[pu] else None}
        if len(mine) >= 4:
            h = len(mine) // 2
            a, z = mine[:h], mine[h:]
            kd_a = _kd(sum(r.get("kills") or 0 for r in a), sum(r.get("deaths") or 0 for r in a))
            kd_z = _kd(sum(r.get("kills") or 0 for r in z), sum(r.get("deaths") or 0 for r in z))
            p["tilt"] = ("caiu" if kd_z <= kd_a - 0.25 else "subiu" if kd_z >= kd_a + 0.25
                         else "estável") + f" (K/D {kd_a} → {kd_z})"
        players.append(p)
    players.sort(key=lambda p: (-p["partidas"], -p["acs"]))
    ordered = sorted(chain, key=lambda r: r.get("started_raw") or 0)
    w = sum(1 for r in ordered if r.get("won"))
    eligible = [p for p in players if p["partidas"] >= 2] or players
    mvp = max(eligible, key=lambda p: (p["acs"], p["kd"]))["nome"] if eligible else None
    bagre = min(eligible, key=lambda p: (p["acs"], p["kd"]))["nome"] if len(eligible) > 1 else None
    return {"tem_noite": True, "em_andamento": now - last < gap,
            "comecou": datetime.fromtimestamp(ordered[0].get("started_raw") or 0).strftime("%d/%m %H:%M"),
            "inicio_raw": ordered[0].get("started_raw"), "fim_raw": last,
            "partidas": len(ordered), "vitorias": w, "derrotas": len(ordered) - w,
            "resultados": "".join("V" if r.get("won") else "D" for r in ordered),
            "mapas": [r.get("map") for r in ordered],
            "mvp_da_noite": mvp, "bagre_da_noite": bagre, "jogadores": players}


# ---------------------------------------------------------------------------
# Apelidos conquistados pelos dados (lore)
# ---------------------------------------------------------------------------
NICKNAMES = [
    # (chave, apelido, métrica, condição(valor, média do grupo)); "_low" = o menor leva
    ("first-death", "Rei do first death", "fd_pm", lambda v, avg: v >= 3.0 and v >= 1.3 * avg),
    ("entry", "Abridor oficial", "fb_pm", lambda v, avg: v >= 3.0 and v >= 1.3 * avg),
    ("carregador", "Carregador do grupo", "acs", lambda v, avg: v >= 1.15 * avg),
    ("turista", "Turista do lobby", "acs_low", lambda v, avg: v <= 0.85 * avg),
    ("headshot", "Máquina de headshot", "hs", lambda v, avg: v >= 28),
    ("alface", "Mão de alface", "hs_low", lambda v, avg: v < 15),
    ("pe-frio", "Pé frio", "wr_low", lambda v, avg: v <= 35),
    ("pe-quente", "Pé quente", "wr", lambda v, avg: v >= 65),
]


def nicknames(rows_by_puuid, labels, min_matches=3):
    """[{chave, puuid, nome, apelido, prova}] da semana. Um dono por apelido."""
    stats = {}
    for pu, rows in rows_by_puuid.items():
        if len(rows) < min_matches:
            continue
        n = len(rows)
        full = [r for r in rows if r.get("full_data", 1)]
        nf = max(1, len(full))
        shots = sum((r.get("headshots") or 0) + (r.get("bodyshots") or 0) + (r.get("legshots") or 0)
                    for r in rows)
        stats[pu] = {
            "n": n,
            "fd_pm": sum(r.get("first_deaths") or 0 for r in full) / nf,
            "fb_pm": sum(r.get("first_bloods") or 0 for r in full) / nf,
            "acs": sum(_acs(r) for r in rows) / n,
            "hs": 100 * sum(r.get("headshots") or 0 for r in rows) / shots if shots else 0,
            "wr": 100 * sum(1 for r in rows if r.get("won")) / n,
        }
    if len(stats) < 2:
        return []
    out = []
    for key, title, metric, cond in NICKNAMES:
        base = metric.replace("_low", "")
        values = {pu: s[base] for pu, s in stats.items()}
        if base == "wr" and any(s["n"] < 6 for s in stats.values()):
            values = {pu: v for pu, v in values.items() if stats[pu]["n"] >= 6}
        if not values:
            continue
        avg = sum(values.values()) / len(values)
        pick = (min if metric.endswith("_low") else max)(values, key=values.get)
        v = values[pick]
        if not cond(v, avg):
            continue
        unit = {"fd_pm": " first deaths/jogo", "fb_pm": " first bloods/jogo", "acs": " de ACS",
                "hs": "% de HS", "wr": "% de vitória"}[base]
        val = round(v, 1) if base in ("fd_pm", "fb_pm", "hs", "wr") else round(v)
        out.append({"chave": key, "puuid": pick, "nome": labels.get(pick, "?"), "apelido": title,
                    "prova": f"{val}{unit} em {stats[pick]['n']} partidas na semana"})
    return out
