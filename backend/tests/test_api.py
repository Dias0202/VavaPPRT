"""Fluxos da API de ponta a ponta: aprovação, permissões e todas as rotas."""
from conftest import FRIEND_PUUID, load_matches, register


def test_health_sem_token(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database_ok"] is True
    assert body["admin_configured"] is True


def test_admin_nasce_aprovado_e_ingere(client):
    h, tok = register(client, "admin", "Admin", "0001")
    assert tok["status"] == "approved"
    me = client.get("/me", headers=h).json()
    assert me["is_admin"] is True and me["status"] == "approved"
    ov = client.get("/players/Admin/0001/overview", headers=h).json()
    assert ov["matches"] == len(load_matches())
    assert ov["deep_matches"] == ov["matches"]


def test_cadastro_pendente_nao_ve_nada(client, henrik):
    h_admin, _ = register(client, "admin", "Admin", "0001")
    h, tok = register(client, "amigo", "Amigo", "0002")
    assert tok["status"] == "pending"
    assert client.get("/me", headers=h).json()["status"] == "pending"
    assert client.get("/players", headers=h).status_code == 403
    assert client.get("/players/Admin/0001/overview", headers=h).status_code == 403
    # pendente não entra no roster nem dispara download
    roster = client.get("/roster", headers=h_admin).json()
    assert [m["riot_id"] for m in roster] == ["Admin"]


def test_aprovacao_libera_e_baixa_historico(client):
    h_admin, _ = register(client, "admin", "Admin", "0001")
    h, _ = register(client, "amigo", "Amigo", "0002")
    pend = client.get("/admin/users?status=pending", headers=h_admin).json()
    assert [u["username"] for u in pend] == ["amigo"]
    assert "password_hash" not in pend[0]

    # membro comum não aprova ninguém
    assert client.post(f"/admin/users/{pend[0]['id']}/approve", headers=h).status_code == 403

    r = client.post(f"/admin/users/{pend[0]['id']}/approve", headers=h_admin)
    assert r.status_code == 200 and r.json()["changed"] is True
    assert client.get("/players", headers=h).status_code == 200
    ov = client.get("/players/Amigo/0002/overview", headers=h).json()
    assert ov["matches"] >= 1


def test_recusa_apaga_o_cadastro(client):
    h_admin, _ = register(client, "admin", "Admin", "0001")
    register(client, "amigo", "Amigo", "0002")
    uid = client.get("/admin/users?status=pending", headers=h_admin).json()[0]["id"]
    assert client.post(f"/admin/users/{uid}/reject", headers=h_admin).status_code == 200
    r = client.post("/auth/login", json={"username": "amigo", "password": "senha123"})
    assert r.status_code == 401


def test_rastreado_membro_adiciona_so_admin_remove(client):
    h_admin, _ = register(client, "admin", "Admin", "0001")
    h, _ = register(client, "membro", "Amigo", "0002")
    uid = client.get("/admin/users?status=pending", headers=h_admin).json()[0]["id"]
    client.post(f"/admin/users/{uid}/approve", headers=h_admin)

    # Um jogador das fixtures que não é ninguém do grupo vira rastreado.
    other = next(p for p in load_matches()[0]["players"]
                 if p["name"] not in ("Admin", "Amigo"))
    r = client.post("/roster", headers=h, json={"riot_id": other["name"], "tagline": other["tag"]})
    assert r.status_code == 200, r.text
    assert client.post("/roster", headers=h, json={"riot_id": other["name"],
                                                    "tagline": other["tag"]}).status_code == 409

    path = f"/roster/{other['name']}/{other['tag']}"
    assert client.delete(path, headers=h).status_code == 403
    assert client.delete(path, headers=h_admin).status_code == 200
    names = [m["riot_id"] for m in client.get("/roster", headers=h_admin).json()]
    assert other["name"] not in names


def test_riot_id_inexistente(client):
    r = client.post("/auth/register", json={"username": "fantasma", "password": "senha123",
                                           "riot_id": "Ninguem", "tagline": "000"})
    assert r.status_code == 404


def _grupo(client):
    """Admin + Amigo aprovados, com as partidas das fixtures ingeridas."""
    h_admin, _ = register(client, "admin", "Admin", "0001")
    register(client, "amigo", "Amigo", "0002")
    uid = client.get("/admin/users?status=pending", headers=h_admin).json()[0]["id"]
    # O amigo entra DEPOIS das partidas já estarem no banco: a aprovação
    # precisa religá-lo a elas (regressão do match_exists).
    client.post(f"/admin/users/{uid}/approve", headers=h_admin)
    return h_admin


ROTAS_JOGADOR = ["overview", "maps", "agents", "weapons", "heatmap", "insights", "recent",
                 "trend", "sessions", "rank", "postplant", "utility", "coverage", "spatial",
                 "vs-lobby"]
ROTAS_GRUPO = ["/players", "/roster", "/leaderboard", "/group/synergy", "/group/squads",
               "/group/synergy-matrix", "/group/correlation", "/group/weekly",
               "/group/queue-plan", "/config/map-pool", "/me/goals", "/status/ingest",
               "/validate/metrics"]


def test_todas_as_rotas_respondem(client):
    h = _grupo(client)
    for rota in ROTAS_JOGADOR:
        r = client.get(f"/players/Admin/0001/{rota}", headers=h)
        assert r.status_code == 200, (rota, r.text)
    for rota in ROTAS_GRUPO:
        r = client.get(rota, headers=h)
        assert r.status_code == 200, (rota, r.text)


def test_grupo_dupla_e_correlacao(client):
    h = _grupo(client)
    squads = client.get("/group/squads", headers=h).json()
    assert squads and squads[0]["size"] == 2 and squads[0]["games"] == 3

    duo = client.get("/group/duo/Admin/0001/Amigo/0002", headers=h).json()
    assert duo["together"]["games"] == 3
    assert duo["trades"]["b_deaths"] > 0
    assert 0 <= (duo["trades"]["a_avenges_b_pct"] or 0) <= 100

    members = {"members": [{"riot_id": "Admin", "tagline": "0001"},
                           {"riot_id": "Amigo", "tagline": "0002"}]}
    corr = client.post("/group/correlation", headers=h, json=members).json()
    assert corr["stacks_analyzed"] == 3
    od = corr["opening_duel"]
    assert od["won"]["rounds"] + od["lost"]["rounds"] == corr["rounds_analyzed"]

    # sem membros: o mesmo stack (é o único com 2+)
    assert client.get("/group/correlation", headers=h).json()["stacks_analyzed"] == 3

    detail = client.post("/group/detail", headers=h, json=members).json()
    assert detail["together"]["games"] == 3

    h2h = client.get("/group/head-to-head/Admin/0001/Amigo/0002", headers=h).json()
    assert h2h["together"]["games"] == 3


def test_pre_fila_escalacao_sem_repetir_agente(client):
    h = _grupo(client)
    body = {"members": [{"riot_id": "Admin", "tagline": "0001"},
                        {"riot_id": "Amigo", "tagline": "0002"}]}
    r = client.post("/group/pre-queue", headers=h, json=body)
    assert r.status_code == 200
    for mp in r.json()["maps"]:
        agents = [x["agent"] for x in mp["lineup"] if x["agent"]]
        assert len(agents) == len(set(agents))
        assert len(mp["lineup"]) == 2


def test_detalhe_de_partida(client):
    h = _grupo(client)
    mid = load_matches()[0]["metadata"]["match_id"]
    d = client.get(f"/matches/{mid}", headers=h).json()
    assert d["full"] is True and len(d["players"]) == 10
    assert len(d["rounds_detail"]) == d["rounds"]
    friends = [p for p in d["players"] if p["friend"]]
    assert {p["riot_id"] for p in friends} == {"Admin", "Amigo"}
    # adversário: só nome, sem tag/puuid (sem scouting)
    others = [p for p in d["players"] if not p["friend"]]
    assert all(p["tag"] is None and "puuid" not in p for p in others)
    assert client.get("/matches/nao-existe", headers=h).status_code == 404


def test_vs_lobby(client):
    h = _grupo(client)
    d = client.get("/players/Admin/0001/vs-lobby", headers=h).json()
    assert d["lobby"]["matches"] == len(load_matches())
    assert 1 <= d["lobby"]["avg_place"] <= 10
    assert d["rank"]["tier"] == 15


def test_opt_out(client):
    h = _grupo(client)
    r = client.request("DELETE", "/me", headers=h, json={"password": "senha123", "confirm": "APAGAR"})
    assert r.status_code == 200
    assert client.get("/me", headers=h).status_code == 401


def test_status_ingest_mostra_execucao(client, clean_db):
    from app.ingest import ingest_all
    h = _grupo(client)
    res = ingest_all()
    assert res["players"] == 2
    st = client.get("/status/ingest", headers=h).json()
    assert st["runs"][0]["ok"] == 1
    assert st["last_ok_raw"]
    assert all(p["last_ingested_raw"] for p in st["players"])
    assert {p["riot_id"] for p in st["players"]} == {"Admin", "Amigo"}
    assert FRIEND_PUUID not in str(st)  # status não vaza puuid


def test_apelido_do_rastreado_e_troca_de_senha(client):
    h_admin, _ = register(client, "admin", "Admin", "0001")
    other = next(p for p in load_matches()[0]["players"]
                 if p["name"] not in ("Admin", "Amigo"))
    r = client.post("/roster", headers=h_admin,
                    json={"riot_id": other["name"], "tagline": other["tag"], "label": "Luan"})
    assert r.status_code == 200
    labels = {m["riot_id"]: m["label"] for m in client.get("/roster", headers=h_admin).json()}
    assert labels[other["name"]] == "Luan"

    assert client.put("/me/password", headers=h_admin,
                      json={"current": "errada", "new": "novasenha"}).status_code == 401
    assert client.put("/me/password", headers=h_admin,
                      json={"current": "senha123", "new": "novasenha"}).status_code == 200
    assert client.post("/auth/login", json={"username": "admin", "password": "novasenha"}).status_code == 200
