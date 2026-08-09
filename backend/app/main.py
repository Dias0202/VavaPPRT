"""API web (FastAPI). Serve EXCLUSIVAMENTE do banco — o poller alimenta os dados.

Análise exibida só para amigos cadastrados/consentidos; dados de adversário só
entram de forma agregada (contexto de rank), nunca como perfil navegável.
"""
import logging
import time

from fastapi import FastAPI, Depends, HTTPException, Header, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

from . import config, db, auth, analytics, ingest, progress
from . import valorant_api as vapi
from .schemas import (RegisterIn, LoginIn, TokenOut, MeOut, AddPlayerIn,
                      GroupDetailIn, GoalIn, DeleteMeIn, MapPoolIn)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api")

app = FastAPI(title="VavaPPRT API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    try:
        db.init_db()
        logger.info("Banco inicializado.")
    except Exception as e:
        logger.error("Falha init_db: %s", e)


def current_user(authorization: str = Header(default="")):
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Token ausente.")
    payload = auth.decode_token(authorization.split(" ", 1)[1].strip())
    if not payload:
        raise HTTPException(401, "Token inválido ou expirado.")
    user = db.get_user_by_id(int(payload["sub"]))
    if not user:
        raise HTTPException(401, "Usuário não encontrado.")
    return user


def _account_puuid(riot_id, tagline):
    """Valida um Riot ID na HenrikDev e devolve o puuid, ou erra com motivo claro."""
    if not config.HENRIKDEV_API_KEY:
        raise HTTPException(503, "HENRIKDEV_API_KEY não configurada no servidor — "
                                 "sem ela não é possível validar Riot IDs nem baixar partidas.")
    acc = vapi.fetch_account(riot_id, tagline)
    if not isinstance(acc, dict):
        raise HTTPException(502, "Resposta inesperada da API da Valorant.")
    status = acc.get("status")
    if status == 404:
        raise HTTPException(404, "Riot ID não encontrado no Valorant.")
    if status in (401, 403):
        raise HTTPException(503, "Chave da HenrikDev inválida ou sem permissão (HTTP %s)." % status)
    if status == 429:
        raise HTTPException(503, "Rate limit da HenrikDev atingido. Tente de novo em 1 minuto.")
    puuid = (acc.get("data") or {}).get("puuid")
    if not puuid:
        raise HTTPException(502, "Não foi possível validar o jogador agora: %s"
                                 % (acc.get("error") or "resposta sem puuid"))
    return puuid, (acc.get("data") or {}).get("region")


def _resolve_puuid(riot_id, tagline):
    """Resolve um membro do roster (login ou rastreado) para puuid."""
    u = db.get_roster_member_by_riot(riot_id, tagline.lstrip("#"))
    if not u or not u.get("puuid"):
        raise HTTPException(404, "Jogador não encontrado ou ainda sem dados.")
    if not u.get("consent"):
        raise HTTPException(403, "Jogador não autorizou exibir dados.")
    return u["puuid"]


# ---------------------------------------------------------------------------
# Saúde + Auth
# ---------------------------------------------------------------------------
@app.get("/")
def root():
    return {"status": "ok", "service": "VavaPPRT API", "version": "2.0.0"}


@app.get("/health")
def health():
    """Saúde + diagnóstico de configuração (sem vazar segredos)."""
    try:
        roster = len(db.get_roster())
        db_ok = True
    except Exception:
        roster, db_ok = 0, False
    return {
        "status": "ok" if (db_ok and config.HENRIKDEV_API_KEY) else "degraded",
        "database": "postgres" if config.DATABASE_URL else "sqlite (dev)",
        "database_ok": db_ok,
        "henrikdev_key": bool(config.HENRIKDEV_API_KEY),
        "roster_size": roster,
    }


@app.post("/auth/register", response_model=TokenOut)
def register(body: RegisterIn, background: BackgroundTasks):
    username = body.username.strip()
    riot_id = body.riot_id.strip()
    tagline = body.tagline.strip().lstrip("#")
    if db.username_exists(username):
        raise HTTPException(409, "Nome de usuário já existe.")
    if db.riot_id_exists(riot_id, tagline):
        raise HTTPException(409, "Esse Riot ID já está cadastrado.")

    puuid, acct_region = _account_puuid(riot_id, tagline)

    region = acct_region or body.region.strip() or "latam"
    # Quem cria o grupo administra o grupo: sem isso ninguém pode definir a
    # rotação de mapas e a configuração fica travada para sempre.
    first_user = not db.get_all_users()
    db.create_user(username, auth.hash_password(body.password), riot_id, tagline,
                   region, puuid=puuid, is_admin=1 if first_user else 0)
    user = db.get_user_by_username(username)
    # Onboarding (PDD §6): registro → backfill → perfil populado. Sem isto o
    # perfil fica vazio até o cron rodar (e no dev, para sempre).
    background.add_task(_ingest_new_player,
                        {"riot_id": riot_id, "tagline": tagline,
                         "region": region, "puuid": puuid})
    return TokenOut(access_token=auth.create_token(user["id"], username), username=username)


@app.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn):
    user = db.get_user_by_username(body.username.strip())
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Usuário ou senha inválidos.")
    return TokenOut(access_token=auth.create_token(user["id"], user["username"]),
                    username=user["username"])


@app.get("/me", response_model=MeOut)
def me(user=Depends(current_user)):
    return MeOut(id=user["id"], username=user["username"], riot_id=user["riot_id"],
                 tagline=user["tagline"], region=user.get("region") or "latam",
                 is_admin=bool(user.get("is_admin")))


# ---------------------------------------------------------------------------
# Jogadores / analytics (tudo do banco, por puuid)
# ---------------------------------------------------------------------------
@app.get("/players")
def players(user=Depends(current_user)):
    return [{"username": u["username"], "riot_id": u["riot_id"], "tagline": u["tagline"],
             "kind": u.get("kind", "user")}
            for u in db.get_roster()]


# ---------------------------------------------------------------------------
# Roster — adicionar/listar pessoas rastreadas
# ---------------------------------------------------------------------------
def _ingest_new_player(member):
    """Roda em background após adicionar: baixa dados e forma grupos."""
    try:
        db.init_db()
        ingest.ingest_player_now(member, pages=5)
    except Exception as e:
        logger.error("Falha ao baixar dados de %s#%s: %s", member["riot_id"], member["tagline"], e)


@app.get("/roster")
def roster(user=Depends(current_user)):
    return [{"label": u["username"], "riot_id": u["riot_id"], "tagline": u["tagline"],
             "region": u["region"], "kind": u["kind"]} for u in db.get_roster()]


@app.post("/roster")
def add_player(body: AddPlayerIn, background: BackgroundTasks, user=Depends(current_user)):
    riot_id = body.riot_id.strip()
    tagline = body.tagline.strip().lstrip("#")
    region = (body.region or "latam").strip() or "latam"

    if db.get_roster_member_by_riot(riot_id, tagline):
        raise HTTPException(409, "Esse jogador já está no grupo.")

    puuid, acct_region = _account_puuid(riot_id, tagline)
    # região real da conta, se vier
    region = acct_region or region
    if db.puuid_in_roster(puuid):
        raise HTTPException(409, "Esse jogador já está no grupo.")

    db.add_tracked_player(riot_id, tagline, region, puuid, added_by=user["id"])
    member = {"riot_id": riot_id, "tagline": tagline, "region": region, "puuid": puuid}
    background.add_task(_ingest_new_player, member)
    return {"riot_id": riot_id, "tagline": tagline, "region": region,
            "status": "baixando", "message": "Jogador adicionado. Baixando histórico em segundo plano (~30s)."}


@app.get("/players/{riot_id}/{tagline}/overview")
def overview(riot_id: str, tagline: str, user=Depends(current_user)):
    puuid = _resolve_puuid(riot_id, tagline)
    return {"riot_id": riot_id, "tagline": tagline.lstrip("#"), **analytics.player_overview(puuid)}


@app.get("/players/{riot_id}/{tagline}/maps")
def maps(riot_id: str, tagline: str, user=Depends(current_user)):
    return analytics.player_maps(_resolve_puuid(riot_id, tagline))


@app.get("/players/{riot_id}/{tagline}/agents")
def agents(riot_id: str, tagline: str, user=Depends(current_user)):
    return analytics.player_agents(_resolve_puuid(riot_id, tagline))


@app.get("/players/{riot_id}/{tagline}/weapons")
def weapons(riot_id: str, tagline: str, user=Depends(current_user)):
    return analytics.player_weapons(_resolve_puuid(riot_id, tagline))


@app.get("/players/{riot_id}/{tagline}/heatmap")
def heatmap(riot_id: str, tagline: str, map: str = Query(default=None), user=Depends(current_user)):
    return analytics.player_heatmap(_resolve_puuid(riot_id, tagline), map)


@app.get("/players/{riot_id}/{tagline}/insights")
def insights(riot_id: str, tagline: str, user=Depends(current_user)):
    return analytics.insights(_resolve_puuid(riot_id, tagline))


@app.get("/players/{riot_id}/{tagline}/recent")
def recent(riot_id: str, tagline: str, user=Depends(current_user)):
    puuid = _resolve_puuid(riot_id, tagline)
    rows = db.recent_matches_for_puuid(puuid, limit=20)
    return [{
        "match_id": r["match_id"], "map": r["map"], "mode": r["mode"],
        "agent": r["agent"], "won": bool(r["won"]),
        "kills": r["kills"], "deaths": r["deaths"], "assists": r["assists"],
        "kd": round((r["kills"] or 0) / max(1, r["deaths"] or 0), 2),
        "acs": round((r["score"] or 0) / max(1, r["rounds"] or 1), 0),
        "started_at": r["started_at"],
    } for r in rows]


@app.get("/leaderboard")
def leaderboard(user=Depends(current_user)):
    return analytics.leaderboard(db.get_roster())


def _roster_info():
    roster = db.get_roster()
    friend_puuids = {u["puuid"] for u in roster if u.get("puuid") and u.get("consent")}
    info_by = {u["puuid"]: {"username": u["username"], "riot_id": u["riot_id"], "tagline": u["tagline"]}
               for u in roster if u.get("puuid")}
    return friend_puuids, info_by


@app.get("/group/synergy")
def group_synergy(user=Depends(current_user)):
    friend_puuids, info_by = _roster_info()
    return analytics.group_synergy(friend_puuids, info_by)


@app.get("/group/squads")
def group_squads(user=Depends(current_user)):
    """Grupos de 2-5 que jogam juntos."""
    friend_puuids, info_by = _roster_info()
    return analytics.squads(friend_puuids, info_by)


@app.post("/group/detail")
def group_detail(body: GroupDetailIn, user=Depends(current_user)):
    puuids = []
    for m in body.members:
        rm = db.get_roster_member_by_riot(m.riot_id, m.tagline.lstrip("#"))
        if not rm or not rm.get("puuid"):
            raise HTTPException(404, f"Jogador {m.riot_id}#{m.tagline} não encontrado.")
        if not rm.get("consent"):
            raise HTTPException(403, "Jogador não autorizou exibir dados.")
        puuids.append(rm["puuid"])
    _, info_by = _roster_info()
    return analytics.group_detail(puuids, info_by)


@app.get("/group/duo/{ra}/{ta}/{rb}/{tb}")
def group_duo(ra: str, ta: str, rb: str, tb: str, user=Depends(current_user)):
    ua = db.get_roster_member_by_riot(ra, ta.lstrip("#"))
    ub = db.get_roster_member_by_riot(rb, tb.lstrip("#"))
    if not ua or not ub or not ua.get("puuid") or not ub.get("puuid"):
        raise HTTPException(404, "Dupla não encontrada.")
    if not ua.get("consent") or not ub.get("consent"):
        raise HTTPException(403, "Jogador não autorizou exibir dados.")
    return analytics.duo_detail(ua["puuid"], ub["puuid"], ua["username"], ub["username"])


@app.get("/group/correlation")
def group_correlation(user=Depends(current_user)):
    """Motor de correlação: mapa × composição × first-blood do grupo completo."""
    friend_puuids, info_by = _roster_info()
    return analytics.group_correlation(list(friend_puuids), info_by)


@app.get("/group/round-analysis")
def group_round_analysis(user=Depends(current_user)):
    """Correlação de sobrevivência em round: N membros vivos no fim → WR da partida."""
    friend_puuids, _ = _roster_info()
    return analytics.round_correlation(list(friend_puuids))


@app.get("/players/{riot_id}/{tagline}/spatial")
def spatial(riot_id: str, tagline: str, map: str = Query(default=None), user=Depends(current_user)):
    """Posições do jogador em snapshots de kills (para análise espacial avançada)."""
    return analytics.player_spatial(_resolve_puuid(riot_id, tagline), map)


# ---------------------------------------------------------------------------
# Progresso no tempo: tendência, sessões/tilt, curva de RR
# ---------------------------------------------------------------------------
@app.get("/players/{riot_id}/{tagline}/trend")
def trend(riot_id: str, tagline: str, window: int = Query(default=20, ge=5, le=100),
          user=Depends(current_user)):
    """Últimas N partidas vs as N anteriores."""
    return progress.trend(_resolve_puuid(riot_id, tagline), window=window)


@app.get("/players/{riot_id}/{tagline}/sessions")
def sessions(riot_id: str, tagline: str, user=Depends(current_user)):
    """Sessões de jogo e queda de rendimento ao longo da noite."""
    return progress.sessions(_resolve_puuid(riot_id, tagline))


@app.get("/players/{riot_id}/{tagline}/rank")
def rank(riot_id: str, tagline: str, user=Depends(current_user)):
    """Trajetória de RR/elo."""
    return progress.rank_curve(_resolve_puuid(riot_id, tagline))


@app.get("/players/{riot_id}/{tagline}/postplant")
def postplant(riot_id: str, tagline: str, map: str = Query(default=None),
              user=Depends(current_user)):
    """Pós-plant, retake e sites."""
    return analytics.player_postplant(_resolve_puuid(riot_id, tagline), map)


@app.get("/players/{riot_id}/{tagline}/utility")
def utility(riot_id: str, tagline: str, user=Depends(current_user)):
    """Uso de utilidade por round, fatiado por agente."""
    return analytics.player_utility(_resolve_puuid(riot_id, tagline))


@app.get("/players/{riot_id}/{tagline}/coverage")
def coverage(riot_id: str, tagline: str, user=Depends(current_user)):
    """Quantas partidas têm dado profundo — honestidade sobre o lastro."""
    full, total = db.deep_coverage(_resolve_puuid(riot_id, tagline))
    return {"full_matches": full, "total_matches": total,
            "pct": round(100 * full / total, 1) if total else 0.0,
            "pending_deep_backfill": total - full}


# ---------------------------------------------------------------------------
# Metas de treino
# ---------------------------------------------------------------------------
@app.get("/me/goals")
def list_goals(user=Depends(current_user)):
    if not user.get("puuid"):
        return {"goals": [], "suggestions": []}
    return {"goals": progress.evaluate_goals(user["puuid"]),
            "suggestions": progress.suggest_goals(user["puuid"])}


@app.post("/me/goals")
def add_goal(body: GoalIn, user=Depends(current_user)):
    if not user.get("puuid"):
        raise HTTPException(400, "Sua conta ainda não tem dados vinculados.")
    if body.metric not in progress.GOAL_SPECS:
        raise HTTPException(400, f"Métrica '{body.metric}' não é acompanhada. "
                                 f"Opções: {', '.join(progress.GOAL_SPECS)}.")
    spec = progress.GOAL_SPECS[body.metric]
    baseline = body.baseline
    if baseline is None:
        baseline = progress._current_metric_values(user["puuid"]).get(body.metric)
    db.create_goal({
        "puuid": user["puuid"], "metric": body.metric,
        "label": body.label or spec["label"],
        "baseline": baseline, "target": body.target,
        "direction": body.direction or spec.get("direction", "up"),
        "status": "active", "created_raw": int(time.time()), "achieved_raw": None,
    })
    return {"status": "ok"}


@app.delete("/me/goals/{goal_id}")
def remove_goal(goal_id: int, user=Depends(current_user)):
    if not user.get("puuid"):
        raise HTTPException(400, "Sua conta ainda não tem dados vinculados.")
    db.delete_goal(goal_id, user["puuid"])
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Relatório semanal do grupo
# ---------------------------------------------------------------------------
@app.get("/group/weekly")
def group_weekly(user=Depends(current_user)):
    return progress.weekly_report(db.get_roster())


# ---------------------------------------------------------------------------
# Opt-out (PLANO_ANALYTICS §7) — direito ao esquecimento
# ---------------------------------------------------------------------------
@app.delete("/me")
def delete_me(body: DeleteMeIn, user=Depends(current_user)):
    """Apaga a conta e TODO dado pessoal associado. Irreversível.

    Exige senha + confirmação textual: é destrutivo e não tem desfazer."""
    if body.confirm != "APAGAR":
        raise HTTPException(400, "Confirmação inválida: envie confirm='APAGAR'.")
    if not auth.verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Senha incorreta.")
    purged = {}
    if user.get("puuid"):
        purged = db.purge_puuid(user["puuid"])
    db.delete_user_by_id(user["id"])
    orphans = db.orphan_matches_cleanup()
    logger.info("Opt-out do usuário %s concluído.", user["username"])
    return {"status": "ok", "purged": purged, "orphan_matches_removed": orphans}


@app.delete("/roster/{riot_id}/{tagline}")
def remove_tracked(riot_id: str, tagline: str, user=Depends(current_user)):
    """Remove uma pessoa rastreada e apaga os dados dela."""
    m = db.get_roster_member_by_riot(riot_id, tagline.lstrip("#"))
    if not m:
        raise HTTPException(404, "Jogador não está no grupo.")
    if m.get("kind") != "tracked":
        raise HTTPException(400, "Esse jogador tem login próprio — só ele pode se remover "
                                 "(DELETE /me).")
    purged = db.purge_puuid(m["puuid"])
    orphans = db.orphan_matches_cleanup()
    return {"status": "ok", "purged": purged, "orphan_matches_removed": orphans}


# ---------------------------------------------------------------------------
# Rotação de mapas (admin) + plano de fila
# ---------------------------------------------------------------------------
def require_admin(user=Depends(current_user)):
    if not user.get("is_admin"):
        raise HTTPException(403, "Só um administrador do grupo pode mudar isto.")
    return user


@app.get("/config/map-pool")
def get_map_pool(user=Depends(current_user)):
    """Rotação ativa. Lista vazia = sem filtro (todos os mapas)."""
    return {"pool": analytics.get_map_pool(), "all_maps": analytics.all_maps()}


@app.put("/admin/map-pool")
def put_map_pool(body: MapPoolIn, user=Depends(require_admin)):
    known = set(analytics.all_maps())
    invalid = [m for m in body.maps if m not in known]
    if invalid:
        raise HTTPException(400, f"Mapa desconhecido: {', '.join(invalid)}.")
    return {"pool": analytics.set_map_pool(body.maps)}


@app.get("/group/queue-plan")
def group_queue_plan(user=Depends(current_user)):
    """Para cada mapa da rotação: filar, evitar, e quem leva qual agente."""
    friend_puuids, info_by = _roster_info()
    return analytics.queue_plan(friend_puuids, info_by)


@app.get("/group/head-to-head/{ra}/{ta}/{rb}/{tb}")
def group_head_to_head(ra: str, ta: str, rb: str, tb: str, user=Depends(current_user)):
    ua = db.get_roster_member_by_riot(ra, ta.lstrip("#"))
    ub = db.get_roster_member_by_riot(rb, tb.lstrip("#"))
    if not ua or not ub or not ua.get("puuid") or not ub.get("puuid"):
        raise HTTPException(404, "Jogador não encontrado.")
    if not ua.get("consent") or not ub.get("consent"):
        raise HTTPException(403, "Jogador não autorizou exibir dados.")
    return analytics.head_to_head(ua["puuid"], ub["puuid"], ua["username"], ub["username"])


@app.get("/validate/metrics")
def validate_metrics(limit: int = Query(default=5, le=20), user=Depends(current_user)):
    """Recomputes stored metrics from raw match data and surfaces discrepancies."""
    from . import metrics as M
    match_ids = db.recent_match_ids(limit)
    results = []
    for mid in match_ids:
        raw = db.get_raw_match(mid)
        if not raw:
            continue
        stored = db.derived_for_puuid_match(mid)
        validations = []
        for row in stored:
            report = M.validate_match_metrics(raw, row["puuid"])
            if report:
                validations.append(report)
        results.append({
            "match_id": mid,
            "players_count": len(stored),
            "validations": validations,
            "issues_found": sum(1 for v in validations if not v.get("clean")),
        })
    return results
