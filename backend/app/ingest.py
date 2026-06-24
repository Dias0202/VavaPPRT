"""Ingestão v4: busca partidas, computa métricas e grava no banco.

Para cada partida nova:
  - resumo de TODOS os 10 jogadores (sinergia/contexto de rank);
  - métricas profundas + rounds para os AMIGOS cadastrados (por puuid);
  - todos os kills (heatmap/recompute);
  - raw enxuto comprimido (gzip) para recomputar métricas futuras.
Idempotente por match_id. Respeita o rate limit (cliente serializa).
"""
import logging

from . import db
from . import valorant_api as api
from . import metrics as M

logger = logging.getLogger("ingest")


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
    for puuid in friends_here:
        d = M.build_player_derived(match, puuid, kills_ann, attacker_map)
        if d:
            derived.append(d)
        for r in M.player_round_rows(match, puuid):
            round_rows.append({
                "match_id": meta["match_id"], "round_id": r["round_id"], "puuid": puuid,
                "kills": r["kills"], "damage": r["damage"], "headshots": r["headshots"],
                "loadout_value": r["loadout_value"], "buy_type": r["buy_type"], "weapon": r["weapon"],
                "alive_end": 1 if r["alive_end"] else 0, "was_traded": 1 if r["was_traded"] else 0,
                "attacker_side": 1 if attacker_map.get(r["round_id"]) == M.player_summary(match, puuid)["team"] else 0,
            })

    kill_rows = [{
        "match_id": meta["match_id"], "round_id": k["round"], "order_in_round": k["order_in_round"],
        "time_ms": k["time_in_round_in_ms"], "killer_puuid": k["killer_puuid"],
        "victim_puuid": k["victim_puuid"], "weapon": k["weapon"],
        "is_first": 1 if k["is_first"] else 0, "is_trade": 1 if k["is_trade"] else 0,
        "vx": k["vx"], "vy": k["vy"],
    } for k in kills_ann]

    raw_gz = db.compress_raw(M.strip_raw(match))
    return {
        "meta": meta, "raw_gz": raw_gz, "match_players": mp_rows,
        "derived": derived, "round_rows": round_rows, "kill_rows": kill_rows,
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
                             bundle["derived"], bundle["round_rows"], bundle["kill_rows"])
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
        logger.info("Jogador %s#%s: %d completas, %d resumo.",
                    member["riot_id"], member["tagline"], new, back)
        return {"full": new, "summary": back}
    except Exception as e:
        logger.warning("Erro ingest_player_now %s#%s: %s", member["riot_id"], member["tagline"], e)
        return {"full": 0, "summary": 0, "error": str(e)}
