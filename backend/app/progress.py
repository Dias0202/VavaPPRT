"""Camada temporal: como o jogador **está indo**, não só como ele **é**.

O resto da plataforma agrega a vida inteira do jogador — ótimo para retrato,
inútil para responder "melhorei?". Este módulo cobre a dimensão que faltava:

- `trend`      — janela móvel: últimas N partidas vs as N anteriores.
- `sessions`   — sessões de jogo e queda de rendimento dentro delas (tilt).
- `rank_curve` — trajetória de RR/elo (fonte: /v1/mmr-history).
- `weekly_report` — digest do grupo: o que mudou nos últimos 7 dias.
- `goals`      — metas mensuráveis derivadas das lacunas, com acompanhamento.

O mesmo gating de amostragem do `analytics` vale aqui: sem janela cheia dos
dois lados, a comparação não é publicada como conclusão.
"""
import time
from collections import defaultdict

from . import db, analytics as A

# Intervalo que separa duas sessões de jogo. Abaixo disso é a mesma sentada.
SESSION_GAP_SECONDS = 3 * 3600
WEEK_SECONDS = 7 * 86400


# ---------------------------------------------------------------------------
# Métricas comparáveis entre janelas
# ---------------------------------------------------------------------------
def _window_metrics(rows):
    """Resumo de uma janela de partidas. `full` isola o que exige partida
    completa — senão o backfill de resumo zeraria KAST/trade/entrada."""
    n = len(rows)
    if not n:
        return None
    full = [r for r in rows if r.get("full_data")]

    def s(k, src=None):
        return sum(r.get(k) or 0 for r in (rows if src is None else src))

    rounds = s("rounds") or 1
    frounds = sum(r.get("rounds") or 0 for r in full) or 1
    shots = s("headshots") + s("bodyshots") + s("legshots")
    deaths = s("deaths")
    fdeaths = sum(r.get("deaths") or 0 for r in full)

    out = {
        "matches": n,
        "full_matches": len(full),
        "win_rate": A._rate(s("won"), n),
        "kd": A._kd(s("kills"), deaths),
        "acs": round(s("score") / rounds, 0),
        "adr": round(s("damage") / rounds, 1),
        "hs_pct": A._rate(s("headshots"), shots),
    }
    if full:
        out.update({
            "kast": A._rate(s("kast_rounds", full), frounds),
            "trade_pct": A._rate(s("deaths_traded", full), fdeaths) if fdeaths else 0.0,
            "fb_per_match": round(s("first_bloods", full) / len(full), 1),
            "fd_per_match": round(s("first_deaths", full) / len(full), 1),
        })
    return out


# Direção "boa" de cada métrica: 'up' = maior é melhor.
METRIC_DIRECTION = {
    "win_rate": "up", "kd": "up", "acs": "up", "adr": "up", "hs_pct": "up",
    "kast": "up", "trade_pct": "up", "fb_per_match": "up", "fd_per_match": "down",
}
METRIC_LABEL = {
    "win_rate": "Taxa de vitória", "kd": "K/D", "acs": "ACS", "adr": "ADR",
    "hs_pct": "HS%", "kast": "KAST", "trade_pct": "Mortes trocadas",
    "fb_per_match": "First bloods/jogo", "fd_per_match": "First deaths/jogo",
}


def trend(puuid, window=20):
    """Últimas `window` partidas vs as `window` anteriores.

    É o que transforma "seu trade é 22%" (um fato estático) em "seu trade caiu
    de 31% para 22%" (uma coisa sobre a qual dá pra agir).
    """
    rows = db.derived_for_puuid(puuid)  # já vem mais novas primeiro
    recent, previous = rows[:window], rows[window:window * 2]
    cur = _window_metrics(recent)
    prev = _window_metrics(previous)
    if not cur:
        return {"available": False, "reason": "sem partidas"}
    if not prev or len(previous) < min(window, A.MIN_TREND):
        return {"available": False, "window": window, "current": cur,
                "reason": f"faltam partidas na janela anterior "
                          f"({len(previous)}/{min(window, A.MIN_TREND)})"}

    deltas = []
    for key, direction in METRIC_DIRECTION.items():
        if key not in cur or key not in prev:
            continue
        a, b = cur[key], prev[key]
        diff = round(a - b, 2)
        if diff == 0:
            verdict = "estável"
        elif (diff > 0) == (direction == "up"):
            verdict = "melhorou"
        else:
            verdict = "piorou"
        deltas.append({"metric": key, "label": METRIC_LABEL[key],
                       "current": a, "previous": b, "delta": diff,
                       "direction": direction, "verdict": verdict})
    # o que mais mudou primeiro, em módulo relativo
    deltas.sort(key=lambda d: abs(d["delta"]) / (abs(d["previous"]) or 1), reverse=True)
    return {"available": True, "window": window, "current": cur, "previous": prev,
            "deltas": deltas,
            "improved": [d for d in deltas if d["verdict"] == "melhorou"],
            "declined": [d for d in deltas if d["verdict"] == "piorou"]}


# ---------------------------------------------------------------------------
# Sessões e tilt
# ---------------------------------------------------------------------------
def _group_sessions(rows):
    """Agrupa partidas em sessões (ordem cronológica dentro de cada uma)."""
    timed = [r for r in rows if r.get("started_raw")]
    timed.sort(key=lambda r: r["started_raw"])
    sessions, cur = [], []
    for r in timed:
        if cur and r["started_raw"] - cur[-1]["started_raw"] > SESSION_GAP_SECONDS:
            sessions.append(cur)
            cur = []
        cur.append(r)
    if cur:
        sessions.append(cur)
    return sessions


def sessions(puuid, limit=15):
    """Sessões recentes + curva de rendimento por posição na sessão.

    Responde a pergunta mais acionável que existe para um grupo de amigos:
    até que ponto da noite vale continuar filando.
    """
    rows = db.derived_for_puuid(puuid)
    all_sessions = _group_sessions(rows)
    if not all_sessions:
        return {"sessions": [], "tilt": {"available": False}}

    by_index = defaultdict(lambda: {"n": 0, "wins": 0, "kd_k": 0, "kd_d": 0})
    for sess in all_sessions:
        if len(sess) < A.MIN_SESSION:
            continue
        for i, m in enumerate(sess):
            # 5ª partida em diante entra num balde só — a cauda é rala.
            bucket = min(i + 1, 5)
            b = by_index[bucket]
            b["n"] += 1
            b["wins"] += m.get("won") or 0
            b["kd_k"] += m.get("kills") or 0
            b["kd_d"] += m.get("deaths") or 0

    curve = [{
        "position": k if k < 5 else "5+",
        "matches": v["n"],
        "win_rate": A._rate(v["wins"], v["n"]),
        "kd": A._kd(v["kd_k"], v["kd_d"]),
        "confidence": A._conf(v["n"], A.MIN_TREND),
    } for k, v in sorted(by_index.items())]

    tilt = {"available": False}
    early = [c for c in curve if c["position"] in (1, 2) and c["matches"]]
    late = [c for c in curve if c["position"] in (4, "5+") and c["matches"]]
    if early and late:
        e_n = sum(c["matches"] for c in early)
        l_n = sum(c["matches"] for c in late)
        if e_n >= A.MIN_TREND and l_n >= A.MIN_TREND:
            e_wr = A._rate(sum(c["win_rate"] * c["matches"] for c in early) / 100, e_n)
            l_wr = A._rate(sum(c["win_rate"] * c["matches"] for c in late) / 100, l_n)
            drop = round(e_wr - l_wr, 1)
            tilt = {
                "available": True, "early_wr": e_wr, "late_wr": l_wr,
                "drop": drop, "early_matches": e_n, "late_matches": l_n,
                "verdict": ("queda clara" if drop >= 12 else
                            "queda leve" if drop >= 5 else
                            "sem queda"),
            }

    recent = []
    for sess in reversed(all_sessions[-limit:]):
        wins = sum(m.get("won") or 0 for m in sess)
        recent.append({
            "started_raw": sess[0]["started_raw"],
            "matches": len(sess),
            "wins": wins, "losses": len(sess) - wins,
            "win_rate": A._rate(wins, len(sess)),
            "kd": A._kd(sum(m.get("kills") or 0 for m in sess),
                        sum(m.get("deaths") or 0 for m in sess)),
            "results": [bool(m.get("won")) for m in sess],
        })
    return {"sessions": recent, "curve": curve, "tilt": tilt,
            "total_sessions": len(all_sessions)}


# ---------------------------------------------------------------------------
# Curva de rank / RR
# ---------------------------------------------------------------------------
def rank_curve(puuid, limit=100):
    rows = db.rank_history_for_puuid(puuid, limit=limit)
    if not rows:
        return {"available": False, "points": [],
                "note": "Sem histórico de RR ainda — o poller preenche na próxima execução."}
    pts = sorted(rows, key=lambda r: r.get("date_raw") or 0)
    points = [{
        "date_raw": p.get("date_raw"), "tier": p.get("tier"),
        "tier_name": p.get("tier_name"), "rr": p.get("rr"),
        "rr_change": p.get("rr_change"), "elo": p.get("elo"), "map": p.get("map"),
    } for p in pts]

    last20 = points[-20:]
    net20 = sum(p["rr_change"] or 0 for p in last20)
    cur = points[-1]
    peak = max(points, key=lambda p: (p.get("elo") or 0))
    return {
        "available": True,
        "points": points,
        "current": {"tier": cur.get("tier"), "tier_name": cur.get("tier_name"),
                    "rr": cur.get("rr"), "elo": cur.get("elo")},
        "peak": {"tier_name": peak.get("tier_name"), "elo": peak.get("elo"),
                 "date_raw": peak.get("date_raw")},
        "net_rr_last20": net20,
        "matches_last20": len(last20),
        "verdict": ("subindo" if net20 > 30 else "caindo" if net20 < -30 else "estável"),
    }


# ---------------------------------------------------------------------------
# Metas de treino — fecham o loop do motor de insights
# ---------------------------------------------------------------------------
# Cada meta aponta para uma métrica que a plataforma sabe medir sozinha.
GOAL_SPECS = {
    "kast":       {"label": "KAST", "step": 5.0, "unit": "%", "min": 0, "max": 100},
    "trade_pct":  {"label": "Mortes trocadas", "step": 6.0, "unit": "%", "min": 0, "max": 100},
    "hs_pct":     {"label": "HS%", "step": 3.0, "unit": "%", "min": 0, "max": 100},
    "kd":         {"label": "K/D", "step": 0.1, "unit": "", "min": 0, "max": 5},
    "acs":        {"label": "ACS", "step": 15.0, "unit": "", "min": 0, "max": 500},
    "adr":        {"label": "ADR", "step": 10.0, "unit": "", "min": 0, "max": 400},
    "fd_per_match": {"label": "First deaths/jogo", "step": -0.8, "unit": "",
                     "min": 0, "max": 20, "direction": "down"},
    "win_rate":   {"label": "Taxa de vitória", "step": 5.0, "unit": "%", "min": 0, "max": 100},
}


def _current_metric_values(puuid, window=20):
    """Valor atual de cada métrica de meta, medido na janela recente."""
    rows = db.derived_for_puuid(puuid)[:window]
    m = _window_metrics(rows) or {}
    return m


def suggest_goals(puuid, window=20):
    """Converte as lacunas do motor de insights em metas mensuráveis.

    O insights hoje diz "morre isolado, jogue mais perto do time" e o assunto
    morre ali. Uma meta transforma isso em número com prazo e acompanhamento.
    """
    ov = A.player_overview(puuid)
    if ov.get("matches", 0) < A.MIN_MATCHES:
        return []
    cur = _current_metric_values(puuid, window)
    existing = {g["metric"] for g in db.goals_for_puuid(puuid) if g["status"] == "active"}

    candidates = []
    # As lacunas primeiro: a meta certa é a que ataca o ponto mais fraco.
    if "kast" in cur and cur["kast"] < 70:
        candidates.append("kast")
    if "trade_pct" in cur and cur["trade_pct"] < 30:
        candidates.append("trade_pct")
    if "fd_per_match" in cur and cur["fd_per_match"] >= 3:
        candidates.append("fd_per_match")
    if cur.get("hs_pct", 0) < 22:
        candidates.append("hs_pct")
    if cur.get("kd", 0) < 1.0:
        candidates.append("kd")
    if cur.get("win_rate", 0) < 50:
        candidates.append("win_rate")

    out = []
    for metric in candidates:
        if metric in existing or metric not in cur:
            continue
        spec = GOAL_SPECS[metric]
        baseline = cur[metric]
        target = round(min(spec["max"], max(spec["min"], baseline + spec["step"])), 2)
        if target == baseline:
            continue
        out.append({
            "metric": metric, "label": spec["label"], "unit": spec["unit"],
            "baseline": baseline, "target": target,
            "direction": spec.get("direction", "up"),
            "text": f"{spec['label']}: de {baseline}{spec['unit']} para "
                    f"{target}{spec['unit']} nas próximas {window} partidas.",
        })
    return out[:4]


def evaluate_goals(puuid, window=20):
    """Estado atual de cada meta ativa, com progresso de 0 a 100%."""
    goals = db.goals_for_puuid(puuid)
    if not goals:
        return []
    cur = _current_metric_values(puuid, window)
    out = []
    for g in goals:
        metric = g["metric"]
        spec = GOAL_SPECS.get(metric, {})
        now = cur.get(metric)
        baseline, target = g.get("baseline"), g.get("target")
        progress = None
        achieved = False
        if now is not None and baseline is not None and target is not None:
            span = target - baseline
            if span:
                progress = max(0.0, min(100.0, round(100 * (now - baseline) / span, 1)))
            achieved = (now >= target) if g.get("direction", "up") == "up" else (now <= target)
        if achieved and g["status"] == "active":
            db.update_goal_status(g["id"], "achieved", int(time.time()))
        out.append({
            "id": g["id"], "metric": metric,
            "label": g.get("label") or spec.get("label") or metric,
            "unit": spec.get("unit", ""),
            "baseline": baseline, "target": target, "current": now,
            "direction": g.get("direction", "up"),
            "status": "achieved" if achieved else g["status"],
            "progress": progress,
        })
    return out


# ---------------------------------------------------------------------------
# Relatório semanal do grupo
# ---------------------------------------------------------------------------
def weekly_report(roster, now_raw=None):
    """Digest dos últimos 7 dias vs os 7 anteriores, por pessoa e do grupo.

    Roda sobre o mesmo dado que o cron já mantém — nenhuma chamada extra à API.
    """
    now_raw = now_raw or int(time.time())
    cut1, cut2 = now_raw - WEEK_SECONDS, now_raw - 2 * WEEK_SECONDS

    players, group_matches = [], set()
    week_wins = week_total = 0
    for u in roster:
        pu = u.get("puuid")
        if not pu or not u.get("consent"):
            continue
        rows = db.derived_for_puuid(pu)
        this_week = [r for r in rows if (r.get("started_raw") or 0) >= cut1]
        last_week = [r for r in rows if cut2 <= (r.get("started_raw") or 0) < cut1]
        cur = _window_metrics(this_week)
        if not cur:
            continue
        prev = _window_metrics(last_week)
        for r in this_week:
            group_matches.add(r["match_id"])
        week_wins += sum(r.get("won") or 0 for r in this_week)
        week_total += len(this_week)

        changes = []
        if prev:
            for key in ("win_rate", "kd", "acs", "kast", "hs_pct"):
                if key in cur and key in prev:
                    diff = round(cur[key] - prev[key], 2)
                    if abs(diff) >= (2 if key != "kd" else 0.1):
                        changes.append({"metric": key, "label": METRIC_LABEL[key],
                                        "delta": diff,
                                        "verdict": "melhorou" if diff > 0 else "piorou"})
            changes.sort(key=lambda c: abs(c["delta"]), reverse=True)

        best_map = None
        by_map = defaultdict(lambda: {"n": 0, "w": 0})
        for r in this_week:
            b = by_map[r["map"] or "?"]
            b["n"] += 1
            b["w"] += r.get("won") or 0
        ranked = [(m, v) for m, v in by_map.items() if v["n"] >= 2]
        if ranked:
            m, v = max(ranked, key=lambda kv: (kv[1]["w"] / kv[1]["n"], kv[1]["n"]))
            best_map = {"map": m, "matches": v["n"], "win_rate": A._rate(v["w"], v["n"])}

        players.append({
            "username": u.get("username"), "riot_id": u.get("riot_id"),
            "tagline": u.get("tagline"),
            "matches": cur["matches"], "win_rate": cur["win_rate"], "kd": cur["kd"],
            "acs": cur["acs"], "kast": cur.get("kast"),
            "changes": changes[:3], "best_map": best_map,
            "had_previous_week": bool(prev),
        })

    players.sort(key=lambda p: (p["matches"], p["win_rate"]), reverse=True)
    mvp = None
    eligible = [p for p in players if p["matches"] >= A.MIN_MATCHES]
    if eligible:
        mvp = max(eligible, key=lambda p: (p["win_rate"], p["kd"]))

    return {
        "period": {"from_raw": cut1, "to_raw": now_raw},
        "active_players": len(players),
        "matches_played": len(group_matches),
        "group_win_rate": A._rate(week_wins, week_total),
        "mvp": {"username": mvp["username"], "win_rate": mvp["win_rate"],
                "kd": mvp["kd"], "matches": mvp["matches"]} if mvp else None,
        "players": players,
    }
