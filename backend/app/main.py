"""API web (FastAPI). Serve EXCLUSIVAMENTE do banco — o poller alimenta os dados.

Análise exibida só para amigos cadastrados/consentidos; dados de adversário só
entram de forma agregada (contexto de rank), nunca como perfil navegável.
"""
import logging

from fastapi import FastAPI, Depends, HTTPException, Header, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

from . import config, db, auth, analytics, ingest
from . import valorant_api as vapi
from .schemas import RegisterIn, LoginIn, TokenOut, MeOut, AddPlayerIn, GroupDetailIn

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
    return {"status": "ok"}


@app.post("/auth/register", response_model=TokenOut)
def register(body: RegisterIn):
    username = body.username.strip()
    riot_id = body.riot_id.strip()
    tagline = body.tagline.strip().lstrip("#")
    if db.username_exists(username):
        raise HTTPException(409, "Nome de usuário já existe.")
    if db.riot_id_exists(riot_id, tagline):
        raise HTTPException(409, "Esse Riot ID já está cadastrado.")

    acc = vapi.fetch_account(riot_id, tagline)
    puuid = None
    if isinstance(acc, dict):
        if acc.get("status") == 404:
            raise HTTPException(404, "Riot ID não encontrado no Valorant.")
        puuid = (acc.get("data") or {}).get("puuid")

    db.create_user(username, auth.hash_password(body.password), riot_id, tagline,
                   body.region.strip() or "latam", puuid=puuid)
    user = db.get_user_by_username(username)
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

    acc = vapi.fetch_account(riot_id, tagline)
    if not isinstance(acc, dict) or acc.get("status") == 404:
        raise HTTPException(404, "Riot ID não encontrado no Valorant.")
    puuid = (acc.get("data") or {}).get("puuid")
    if not puuid:
        raise HTTPException(502, "Não foi possível validar o jogador agora. Tente de novo.")
    # região real da conta, se vier
    region = (acc.get("data") or {}).get("region") or region
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
