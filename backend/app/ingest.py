"""Ingestão v4: busca partidas, computa métricas e grava no banco.

Para cada partida nova:
  - resumo de TODOS os 10 jogadores (sinergia/contexto de rank);
  - métricas profundas + rounds para os AMIGOS cadastrados (por puuid);
  - todos os kills (heatmap/recompute);
  - raw enxuto comprimido (gzip) para recomputar métricas futuras.
Idempotente por match_id. Respeita o rate limit (cliente serializa).
"""
import logging
from datetime import datetime

from . import db
from . import valorant_api as api
from . import metrics as M

logger = logging.getLogger("ingest")


def extract_spatial_events(match, friend_puuids):
    """Extracts player positions at kill-frame snapshots for spatial analysis."""
    rows = []
    meta = M.match_meta(match)
    mid = meta["match_id"]

    for k in match.get("kills", []):
        rid = k.get("round", 0)
        t = k.get("time_in_round_in_ms", 0)
        for entry in k.get("player_locations", []) or []:
            p = entry.get("player") or {}
            puuid = p.get("puuid")
            if not puuid or puuid not in friend_puuids:
                continue
            loc = entry.get("location") or {}
            rows.append({
                "match_id": mid, "round_id": rid, "puuid": puuid,
                "event_type": "kill_snapshot",
                "time_ms": t,
                "x": loc.get("x"), "y": loc.get("y"), "z": 0,
                "outcome": "kill_event",
            })

    return rows


def process_match(match, friend_puuids):
    """Monta o bundle de gravação de uma partida v4. Retorna dict de listas."""
    meta = M.match_meta(match)
    players = match.get("players", [])
    friends_here = [p["puuid"] for p in players if p["puuid"] in friend_puuids]

    # Resumo de todos os 10.
    match_players = []
    for p in players:
        s = M.player_summary(match, p["puuid"])
        if s:
            match_players.append(s)
    # normaliza chaves para o schema match_players
    mp_rows = [{
        "match_id": meta["match_id"], "puuid": s["puuid"], "name": s["name"], "tag": s["tag"],
        "team": s["team"], "agent": s["agent"], "won": 1 if s["won"] else 0, "score": s["score"],
        "kills": s["kills"], "deaths": s["deaths"], "assists": s["assists"],
        "headshots": s["headshots"], "bodyshots": s["bodyshots"], "legshots": s["legshots"],
        "damage_made": s["damage_made"], "damage_received": s["damage_received"],
        "loadout_avg": s["loadout_avg"], "party_id": s["party_id"],
        "tier": s["tier"], "tier_name": s["tier_name"],
    } for s in match_players]

    # Pré-computa uma vez por partida.
    kills_ann = M.annotate_kills(match)
    attacker_map = M.infer_attacker_by_round(match)

    derived, round_rows = [], []
    player_summary_cache = {}
    for puuid in friends_here:
        d = M.build_player_derived(match, puuid, kills_ann, attacker_map)
        if d:
            derived.append(d)
        ps = M.player_summary(match, puuid)
        player_summary_cache[puuid] = ps
        if ps:
            for r in M.player_round_rows(match, puuid):
                round_rows.append({
                    "match_id": meta["match_id"], "round_id": r["round_id"], "puuid": puuid,
                    "kills": r["kills"], "damage": r["damage"], "headshots": r["headshots"],
                    "bodyshots": r.get("bodyshots", 0), "legshots": r.get("legshots", 0),
                    "loadout_value": r["loadout_value"], "buy_type": r["buy_type"], "weapon": r["weapon"],
                    "alive_end": 1 if r["alive_end"] else 0, "was_traded": 1 if r["was_traded"] else 0,
                    "attacker_side": 1 if attacker_map.get(r["round_id"]) == ps["team"] else 0,
                })

        # Validation logging per friend
        report = M.validate_match_metrics(match, puuid)
        if report and not report.get("clean"):
            logger.warning("Validação partida %s jogador %s: %s",
                           meta["match_id"][:8], puuid[:8], report.get("issues"))

    kill_rows = [{
        "match_id": meta["match_id"], "round_id": k["round"], "order_in_round": k["order_in_round"],
        "time_ms": k["time_in_round_in_ms"], "killer_puuid": k["killer_puuid"],
        "victim_puuid": k["victim_puuid"], "weapon": k["weapon"],
        "is_first": 1 if k["is_first"] else 0, "is_trade": 1 if k["is_trade"] else 0,
        "vx": k["vx"], "vy": k["vy"],
    } for k in kills_ann]

    spatial_rows = extract_spatial_events(match, friend_puuids)

    raw_gz = db.compress_raw(M.strip_raw(match))
    return {
        "meta": meta, "raw_gz": raw_gz, "match_players": mp_rows,
        "derived": derived, "round_rows": round_rows, "kill_rows": kill_rows,
        "spatial_rows": spatial_rows,
        "friends_here": len(friends_here),
    }


def ingest_user(user, friend_puuids):
    """Ingere partidas v4 recentes de um usuário. Retorna nº de partidas novas."""
    name, tag = user["riot_id"], user["tagline"]
    region = user.get("region") or None
    data = api.fetch_matches_v4(name, tag, region=region)
    if isinstance(data, dict) and data.get("error"):
        logger.warning("Falha v4 %s#%s: %s", name, tag, data.get("error"))
        return 0
    matches = data.get("data") or []
    new = 0
    for match in matches:
        mid = match.get("metadata", {}).get("match_id")
        if not mid or db.match_exists(mid):
            continue
        if not M.is_standard(match):  # ignora Deathmatch/Spike Rush/etc.
            continue
        bundle = process_match(match, friend_puuids)
        db.save_match_bundle(bundle["meta"], bundle["raw_gz"], bundle["match_players"],
                             bundle["derived"], bundle["round_rows"], bundle["kill_rows"],
                             bundle.get("spatial_rows", []))
        new += 1
        logger.info("Partida %s gravada (%d amigos).", mid, bundle["friends_here"])
    return new


def ensure_puuid(user):
    """Preenche puuid uma vez (chamada barata)."""
    if user.get("puuid"):
        return user["puuid"]
    acc = api.fetch_account(user["riot_id"], user["tagline"])
    puuid = acc.get("data", {}).get("puuid") if isinstance(acc, dict) else None
    if puuid:
        db.update_user_puuid(user["riot_id"], user["tagline"], puuid)
    return puuid


def backfill_user(user, pages=3, size=20):
    """Backfill de RESUMO via lifetime (barato): popula K/D/WR/mapa/agente de
    partidas antigas. Sem rounds → métricas profundas ficam para a ingestão v4.
    """
    name, tag = user["riot_id"], user["tagline"]
    region = user.get("region") or None
    puuid = ensure_puuid(user)
    if not puuid:
        return 0
    added = 0
    for page in range(1, pages + 1):
        data = api.fetch_lifetime(name, tag, region=region, page=page, size=size)
        if isinstance(data, dict) and data.get("error"):
            logger.warning("Lifetime %s#%s p%d: %s", name, tag, page, data.get("error"))
            break
        items = data.get("data") or []
        if not items:
            break
        for lm in items:
            mid = (lm.get("meta") or {}).get("id")
            if not mid or db.match_exists(mid):
                continue  # já temos (completa ou resumo)
            rows = M.lifetime_summary(lm, puuid)
            if not rows:
                continue  # modo não-padrão
            db.save_summary_match(rows["meta"], rows["mp"], rows["derived"])
            added += 1
    logger.info("Backfill %s#%s: +%d partidas-resumo.", name, tag, added)
    return added


def backfill_all(pages=3):
    roster = db.get_roster()
    total = 0
    for u in roster:
        try:
            total += backfill_user(u, pages=pages)
        except Exception as e:
            logger.warning("Erro backfill %s#%s: %s", u["riot_id"], u["tagline"], e)
    return {"players": len(roster), "backfilled": total}


def recompute_all():
    """Recalcula as métricas derivadas a partir do `raw_gz` guardado.

    Zero chamadas à API: é o caminho para publicar uma métrica nova sobre o
    histórico que já está no banco (PLANO_ANALYTICS §10, "custo de recompute").
    """
    roster = db.get_roster()
    friend_puuids = {u["puuid"] for u in roster if u.get("puuid")}
    ids = db.match_ids_with_raw()
    done = skipped = 0
    for mid in ids:
        raw = db.get_raw_match(mid)
        if not raw:
            skipped += 1
            continue
        try:
            bundle = process_match(raw, friend_puuids)
            db.save_match_bundle(bundle["meta"], bundle["raw_gz"], bundle["match_players"],
                                 bundle["derived"], bundle["round_rows"], bundle["kill_rows"],
                                 bundle.get("spatial_rows", []), upgrade=True)
            done += 1
        except Exception as e:
            logger.warning("Recompute %s falhou: %s", mid[:8], e)
            skipped += 1
    logger.info("Recompute: %d partidas, %d ignoradas.", done, skipped)
    return {"recomputed": done, "skipped": skipped}


def ingest_rank_history(user):
    """Baixa a curva de RR do jogador (/v1/mmr-history) e grava."""
    puuid = user.get("puuid")
    if not puuid:
        return 0
    data = api.fetch_mmr_history(user["riot_id"], user["tagline"], region=user.get("region"))
    if isinstance(data, dict) and data.get("error"):
        logger.warning("mmr-history %s#%s: %s", user["riot_id"], user["tagline"], data.get("error"))
        return 0
    items = data.get("data") or []
    rows = []
    for it in items:
        mid = it.get("match_id") or (it.get("meta") or {}).get("id")
        if not mid:
            continue
        # v1 e v2 do endpoint nomeiam os campos de forma diferente.
        tier = it.get("currenttier")
        tier_name = it.get("currenttierpatched")
        if tier is None and isinstance(it.get("tier"), dict):
            tier = it["tier"].get("id")
            tier_name = it["tier"].get("name")
        date_raw = it.get("date_raw")
        if date_raw is None and isinstance(it.get("date"), str):
            try:
                date_raw = int(datetime.fromisoformat(
                    it["date"].replace("Z", "+00:00")).timestamp())
            except ValueError:
                date_raw = None
        rows.append({
            "puuid": puuid, "match_id": mid, "date_raw": date_raw,
            "tier": tier, "tier_name": tier_name,
            "rr": it.get("ranking_in_tier") if it.get("ranking_in_tier") is not None
                 else it.get("rr"),
            "rr_change": it.get("mmr_change_to_last_game")
                 if it.get("mmr_change_to_last_game") is not None
                 else it.get("last_change"),
            "elo": it.get("elo"),
            "map": (it.get("map") or {}).get("name") if isinstance(it.get("map"), dict)
                 else it.get("map"),
        })
    n = db.save_rank_history(rows)
    logger.info("Rank history %s#%s: %d pontos.", user["riot_id"], user["tagline"], n)
    return n


# Escada de tiers do Valorant. 3-5 Ferro, 6-8 Bronze, ... 27 Radiante.
TIER_NAMES = {0: "Unranked"}
for _i, _div in enumerate(["Iron", "Bronze", "Silver", "Gold", "Platinum",
                           "Diamond", "Ascendant", "Immortal"]):
    for _d in (1, 2, 3):
        TIER_NAMES[3 + _i * 3 + (_d - 1)] = f"{_div} {_d}"
TIER_NAMES[27] = "Radiant"


def tier_name(tier):
    return TIER_NAMES.get(tier or 0, f"Tier {tier}")


def ingest_mmr_profile(user):
    """Rank atual, PICO HISTÓRICO e histórico por ato (fonte: /v2/mmr).

    O `mmr-history` devolve só ~20 partidas — serve para a curva recente, nunca
    para "pico". O pico de verdade e os atos passados só existem aqui.
    """
    puuid = user.get("puuid")
    if not puuid:
        return 0
    data = api.fetch_mmr(user["riot_id"], user["tagline"], region=user.get("region"))
    if isinstance(data, dict) and data.get("error"):
        logger.warning("mmr %s#%s: %s", user["riot_id"], user["tagline"], data.get("error"))
        return 0
    d = data.get("data") or {}
    cur = d.get("current_data") or {}
    high = d.get("highest_rank") or {}

    seasons = []
    for season, s in (d.get("by_season") or {}).items():
        if not isinstance(s, dict) or s.get("error") or not s.get("number_of_games"):
            continue
        peak = max((w.get("tier", 0) for w in (s.get("act_rank_wins") or [])), default=0)
        final_tier = s.get("final_rank") or 0
        # o pico do ato nunca pode ser menor que o rank em que ele terminou
        peak = max(peak, final_tier)
        seasons.append({
            "puuid": puuid, "season": season,
            "games": s.get("number_of_games") or 0, "wins": s.get("wins") or 0,
            "final_tier": final_tier,
            "final_tier_name": s.get("final_rank_patched") or tier_name(final_tier),
            "peak_tier": peak, "peak_tier_name": tier_name(peak),
            "is_old": 1 if s.get("old") else 0,
        })
    db.save_rank_seasons(seasons)

    # Pico histórico: o maior entre o que a Henrik reporta e o melhor ato visto.
    best_season = max(seasons, key=lambda s: s["peak_tier"], default=None)
    peak_tier = high.get("tier") or 0
    peak_name = high.get("patched_tier") or tier_name(peak_tier)
    peak_season = high.get("season")
    if best_season and best_season["peak_tier"] > peak_tier:
        peak_tier = best_season["peak_tier"]
        peak_name = best_season["peak_tier_name"]
        peak_season = best_season["season"]

    db.save_rank_profile({
        "puuid": puuid,
        "current_tier": cur.get("currenttier"),
        "current_tier_name": cur.get("currenttierpatched"),
        "current_rr": cur.get("ranking_in_tier"),
        "elo": cur.get("elo"),
        "games_needed": cur.get("games_needed_for_rating"),
        "peak_tier": peak_tier, "peak_tier_name": peak_name, "peak_season": peak_season,
        "updated_raw": int(datetime.now().timestamp()),
    })
    logger.info("MMR %s#%s: %s agora, pico %s (%s), %d atos.",
                user["riot_id"], user["tagline"], cur.get("currenttierpatched"),
                peak_name, peak_season, len(seasons))
    return len(seasons)


def deep_backfill_user(user, limit=50, friend_puuids=None):
    """Re-hidrata partidas de RESUMO em partidas COMPLETAS (1 chamada por
    partida, via /v4/match/{id}).

    É o que dá lastro real às métricas profundas: sem isto, clutch/KAST/trade/
    economia/heatmap saem só das ~5 partidas que o /v4/matches devolve.
    Caro por design (30 req/min) — rodar sob demanda, nunca no caminho crítico.
    """
    puuid = user.get("puuid")
    if not puuid:
        return {"upgraded": 0, "failed": 0}
    if friend_puuids is None:
        friend_puuids = {u["puuid"] for u in db.get_roster() if u.get("puuid")}
    ids = db.summary_match_ids_for_puuid(puuid, limit=limit)
    up = fail = 0
    for mid in ids:
        data = api.fetch_match_v4(mid, region=user.get("region"))
        if isinstance(data, dict) and data.get("error"):
            logger.warning("Backfill profundo %s: %s", mid[:8], data.get("error"))
            fail += 1
            if data.get("status") == 429:
                break  # rate limit: para e continua na próxima execução
            continue
        match = data.get("data") or data
        if not match.get("rounds"):
            fail += 1
            continue
        if not M.is_standard(match):
            continue
        bundle = process_match(match, friend_puuids)
        db.save_match_bundle(bundle["meta"], bundle["raw_gz"], bundle["match_players"],
                             bundle["derived"], bundle["round_rows"], bundle["kill_rows"],
                             bundle.get("spatial_rows", []), upgrade=True)
        up += 1
        logger.info("Partida %s re-hidratada (completa).", mid[:8])
    return {"upgraded": up, "failed": fail}


def deep_backfill_all(limit=50):
    roster = db.get_roster()
    friend_puuids = {u["puuid"] for u in roster if u.get("puuid")}
    total = {"upgraded": 0, "failed": 0}
    for u in roster:
        try:
            r = deep_backfill_user(u, limit=limit, friend_puuids=friend_puuids)
            total["upgraded"] += r["upgraded"]
            total["failed"] += r["failed"]
        except Exception as e:
            logger.warning("Erro backfill profundo %s#%s: %s", u["riot_id"], u["tagline"], e)
    return {"players": len(roster), **total}


def ingest_all():
    roster = db.get_roster()
    if not roster:
        logger.info("Roster vazio.")
        return {"players": 0, "new_matches": 0}

    friend_puuids = {u["puuid"] for u in roster if u.get("puuid")}
    total_new = 0
    for u in roster:
        try:
            total_new += ingest_user(u, friend_puuids)
        except Exception as e:
            logger.warning("Erro ao ingerir %s#%s: %s", u["riot_id"], u["tagline"], e, exc_info=True)
        for fn, nome in ((ingest_rank_history, "rank history"), (ingest_mmr_profile, "mmr")):
            try:
                fn(u)
            except Exception as e:
                logger.warning("Erro %s %s#%s: %s", nome, u["riot_id"], u["tagline"], e)

    logger.info("Ingestão: %d jogadores, %d partidas novas.", len(roster), total_new)
    return {"players": len(roster), "new_matches": total_new}


def ingest_player_now(member, pages=5):
    """Baixa os dados de UM jogador recém-adicionado: v4 recente + backfill.
    friend_puuids = roster inteiro (capta companheiros já rastreados)."""
    roster_puuids = {u["puuid"] for u in db.get_roster() if u.get("puuid")}
    roster_puuids.add(member["puuid"])
    try:
        new = ingest_user(member, roster_puuids)
        back = backfill_user(member, pages=pages)
        ingest_rank_history(member)
        ingest_mmr_profile(member)
        logger.info("Jogador %s#%s: %d completas, %d resumo.",
                    member["riot_id"], member["tagline"], new, back)
        return {"full": new, "summary": back}
    except Exception as e:
        logger.warning("Erro ingest_player_now %s#%s: %s", member["riot_id"], member["tagline"], e)
        return {"full": 0, "summary": 0, "error": str(e)}
