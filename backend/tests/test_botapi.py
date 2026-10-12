"""botapi (contrato das tools do X9), espelho HTTP /bot/v1 e anúncio no Discord."""
import json
import re

import pytest

from conftest import load_matches, register

ADMIN_DISCORD = "111111111111111111"
FRIEND_DISCORD = "222222222222222222"


def _grupo(client):
    """Admin + Amigo aprovados, com as 4 partidas das fixtures ingeridas."""
    h_admin, _ = register(client, "admin", "Admin", "0001")
    register(client, "amigo", "Amigo", "0002")
    uid = client.get("/admin/users?status=pending", headers=h_admin).json()[0]["id"]
    client.post(f"/admin/users/{uid}/approve", headers=h_admin)
    return h_admin


@pytest.fixture()
def grupo(client):
    _grupo(client)
    from app import botapi
    botapi.link_member(ADMIN_DISCORD, "Admin#0001")
    return botapi


def _by_date():
    return sorted(load_matches(), key=lambda m: m["metadata"]["started_at"], reverse=True)


def _outsiders():
    """Nomes e puuids de quem NÃO é do grupo — nunca podem vazar."""
    names, puuids = set(), set()
    for m in load_matches():
        for p in m["players"]:
            if p["name"] not in ("Admin", "Amigo"):
                names.add(p["name"])
                puuids.add(p["puuid"])
    return names, puuids


ALL_CALLS = [
    ("listar_jogadores", {}),
    ("partidas_recentes", {}),
    ("partidas_recentes", {"jogador": "Amigo", "limite": 2}),
    ("resumo_partida", {}),
    ("resumo_partida", {"partida": "penultima"}),
    ("narrar_round", {"round": 1}),
    ("narrar_round", {"round": 5, "partida": "2"}),
    ("buscar_rounds", {"evento": "first_blood"}),
    ("buscar_rounds", {"evento": "clutch", "partida": "3"}),
    ("momentos_chave", {}),
    ("analisar_round", {"round": 3}),
    ("desempenho_jogador", {"jogador": "admin"}),
    ("tendencia_jogador", {"jogador": "amigo", "janela": 5}),
    ("mapas_e_agentes", {"jogador": "eu"}),
    ("comparar_jogadores", {"jogador_a": "admin", "jogador_b": "amigo"}),
    ("ranking_grupo", {}),
    ("resumo_semanal", {}),
]


def test_catalogo_bate_com_as_funcoes(grupo):
    cat = grupo.tool_catalog()
    assert {t["name"] for t in cat} == set(grupo.TOOLS)
    for t in cat:
        assert t["description"]
        assert t["parameters"]["type"] == "object"
        assert set(t["parameters"]["required"]) <= set(t["parameters"]["properties"])


def test_todas_as_tools_respondem_sem_erro(grupo):
    for name, args in ALL_CALLS:
        out = grupo.call_tool(name, args, speaker_discord_id=ADMIN_DISCORD)
        assert "erro" not in out, (name, args, out)
        json.dumps(out, ensure_ascii=False)  # serializável para o LLM


def test_privacidade_quem_nao_e_do_grupo_so_aparece_pelo_agente(grupo):
    names, puuids = _outsiders()
    blob = json.dumps([grupo.call_tool(n, a, speaker_discord_id=ADMIN_DISCORD)
                       for n, a in ALL_CALLS], ensure_ascii=False)
    for n in names:
        assert not re.search(rf"\b{re.escape(n)}\b", blob), n
    for p in puuids:
        assert p not in blob
    assert "(inimigo)" in blob and "(aliado)" in blob


def test_resolucao_de_jogador(grupo):
    ctx = grupo._Ctx(ADMIN_DISCORD)
    admin = ctx.resolve("Admin#0001")
    assert ctx.resolve("admin") == admin
    assert ctx.resolve("adm") == admin          # prefixo
    assert ctx.resolve("Ádmin") == admin        # acento
    assert ctx.resolve("eu") == admin           # quem fala (vínculo do Discord)
    assert ctx.resolve(f"<@{ADMIN_DISCORD}>") == admin
    assert ctx.resolve("amgo") == ctx.resolve("Amigo")  # erro de digitação
    with pytest.raises(grupo.ToolError, match="Jogadores: admin, amigo"):
        ctx.resolve("Fulano")
    with pytest.raises(grupo.ToolError, match="vincular"):
        grupo._Ctx(FRIEND_DISCORD).resolve("eu")


def test_referencia_de_partida(grupo):
    ordem = _by_date()
    assert grupo.call_tool("resumo_partida", {})["mapa"] == ordem[0]["metadata"]["map"]["name"]
    assert grupo.call_tool("resumo_partida", {"partida": "penúltima"})["mapa"] == \
        ordem[1]["metadata"]["map"]["name"]
    assert grupo.call_tool("resumo_partida", {"partida": 3})["mapa"] == \
        ordem[2]["metadata"]["map"]["name"]
    mid = ordem[3]["metadata"]["match_id"]
    assert grupo.call_tool("resumo_partida", {"partida": mid})["match_id"] == mid
    assert "erro" in grupo.call_tool("resumo_partida", {"partida": 9})
    assert "erro" in grupo.call_tool("resumo_partida", {"partida": "nao-existe"})


def test_resumo_partida_do_ponto_de_vista_do_grupo(grupo):
    sunset = next(m for m in load_matches() if m["metadata"]["map"]["name"] == "Sunset")
    out = grupo.call_tool("resumo_partida", {"partida": sunset["metadata"]["match_id"]})
    # Admin e Amigo jogaram de Blue, que perdeu 13 x 15.
    assert out["placar"] == "13x15" and out["resultado"] == "derrota"
    assert len(out["placar_lobby"]) == 10
    assert {b["nome"] for b in out["placar_lobby"] if b["amigo"]} == {"admin", "amigo"}
    assert out["mvp_do_grupo"] in ("admin", "amigo")
    assert [b["pos_lobby"] for b in out["placar_lobby"]] == list(range(1, 11))


def test_narrar_round(grupo):
    out = grupo.call_tool("narrar_round", {"round": 1})
    assert out["round"] == 1 and out["placar_antes"] == "0x0"
    assert out["pistol"] is True
    assert out["kills"] and out["kills"][0]["abertura"] is True
    assert not any(k["abertura"] for k in out["kills"][1:])
    tempos = [int(k["t"].split(":")[0]) * 60 + int(k["t"].split(":")[1]) for k in out["kills"]]
    assert tempos == sorted(tempos)
    assert {"nós", "eles"} == set(out["economia"])
    assert "erro" in grupo.call_tool("narrar_round", {"round": 0})
    assert "erro" in grupo.call_tool("narrar_round", {"round": 99})
    assert "erro" in grupo.call_tool("narrar_round", {})  # round é obrigatório


def test_placar_round_a_round_fecha_com_o_final(grupo):
    total = grupo.call_tool("resumo_partida", {})
    n = total["rounds"]
    last = grupo.call_tool("narrar_round", {"round": n})
    assert last["placar_depois"] == total["placar"]


def test_buscar_rounds(grupo):
    res = grupo.call_tool("resumo_partida", {})
    won = grupo.call_tool("buscar_rounds", {"evento": "vencidos"})
    lost = grupo.call_tool("buscar_rounds", {"evento": "perdidos"})
    assert won["total"] + lost["total"] == res["rounds"]
    assert won["total"] == int(res["placar"].split("x")[0])
    pistols = grupo.call_tool("buscar_rounds", {"evento": "pistol"})
    assert [r["round"] for r in pistols["rounds"]] == [1, 13]
    assert "erro" in grupo.call_tool("buscar_rounds", {"evento": "dancinha"})
    # filtrado por jogador: só eventos dele
    fb = grupo.call_tool("buscar_rounds", {"evento": "first_death", "jogador": "amigo"})
    assert all(r["detalhe"].startswith("amigo morreu") for r in fb["rounds"])


def test_momentos_e_analise(grupo):
    mk = grupo.call_tool("momentos_chave", {})
    assert "sequencias" in mk and isinstance(mk["momentos"], list)
    an = grupo.call_tool("analisar_round", {"round": 2})
    assert any(f.startswith("Economia:") for f in an["fatores"])
    assert an["vencedor"] in ("nós", "eles")


def test_partida_so_com_resumo_baixa_sob_demanda(grupo, henrik, clean_db):
    mid = _by_date()[0]["metadata"]["match_id"]
    conn = clean_db.get_connection()
    try:
        conn.cursor().execute("UPDATE matches SET raw_gz = NULL WHERE match_id = "
                              + ("?" if clean_db._is_sqlite(conn) else "%s"), (mid,))
        conn.commit()
    finally:
        conn.close()
    assert grupo.call_tool("resumo_partida", {})["detalhe_completo"] is False
    henrik.calls.clear()
    out = grupo.call_tool("narrar_round", {"round": 1})
    assert "erro" not in out and henrik.calls == ["match"]
    assert clean_db.get_raw_match(mid) is not None


def test_partida_so_com_resumo_e_henrik_no_limite(grupo, henrik, clean_db, monkeypatch):
    mid = _by_date()[0]["metadata"]["match_id"]
    conn = clean_db.get_connection()
    try:
        conn.cursor().execute("UPDATE matches SET raw_gz = NULL WHERE match_id = "
                              + ("?" if clean_db._is_sqlite(conn) else "%s"), (mid,))
        conn.commit()
    finally:
        conn.close()
    from app import valorant_api
    monkeypatch.setattr(valorant_api, "fetch_match_v4",
                        lambda *a, **k: {"error": "rate_limit", "status": 429})
    out = grupo.call_tool("narrar_round", {"round": 1})
    assert "limite" in out["erro"]


def test_vinculo_discord_e_opt_out(grupo, clean_db):
    assert grupo.linked_label(ADMIN_DISCORD) == "admin"
    assert grupo.list_links() == [{"discord_id": ADMIN_DISCORD, "nome": "admin"}]
    pu = clean_db.discord_links()[ADMIN_DISCORD]
    clean_db.purge_puuid(pu)
    assert clean_db.discord_links() == {}
    assert clean_db.unlink_discord(ADMIN_DISCORD) == 0


def test_tool_inexistente_e_falha_interna(grupo, monkeypatch):
    assert "erro" in grupo.call_tool("hackear_riot", {})
    monkeypatch.setitem(grupo.TOOLS, "ranking_grupo",
                        (lambda ctx: 1 / 0, "x", grupo._obj({})))
    assert grupo.call_tool("ranking_grupo", {}) == {"erro": "Deu ruim aqui dentro ao buscar isso."}


# ---------------------------------------------------------------------------
# Espelho HTTP
# ---------------------------------------------------------------------------
def test_espelho_http(client, monkeypatch):
    _grupo(client)
    from app import config
    monkeypatch.setattr(config, "BOT_API_TOKEN", "")
    assert client.get("/bot/v1/tools").status_code == 503
    monkeypatch.setattr(config, "BOT_API_TOKEN", "segredo-do-bot")
    assert client.get("/bot/v1/tools").status_code == 401
    assert client.get("/bot/v1/tools", headers={"Authorization": "Bearer errado"}).status_code == 401
    h = {"Authorization": "Bearer segredo-do-bot"}
    cat = client.get("/bot/v1/tools", headers=h).json()
    assert "narrar_round" in {t["name"] for t in cat}
    r = client.post("/bot/v1/tools/narrar_round", headers=h, json={"args": {"round": 2}})
    assert r.status_code == 200 and r.json()["round"] == 2
    assert client.post("/bot/v1/tools/nada", headers=h, json={}).status_code == 404
    assert client.post("/bot/v1/tools/ranking_grupo", headers=h,
                       json={"args": [1]}).status_code == 400


# ---------------------------------------------------------------------------
# Anúncio no Discord (webhook do poller)
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


def test_anuncio_posta_cada_partida_uma_vez(grupo, clean_db, monkeypatch):
    from app import announce
    posts = []

    def fake_post(url, json=None, timeout=None):
        posts.append((url, json))
        return _FakeResp(200, {"id": str(900 + len(posts)), "channel_id": "777"})

    monkeypatch.setattr(announce.requests, "post", fake_post)
    clean_db.set_config(announce.SINCE_KEY, 0)
    assert announce.run("https://discord.test/hook") == 4
    assert all(u.endswith("?wait=true") for u, _ in posts)
    titles = [p["embeds"][0]["title"] for _, p in posts]
    assert any("DERROTA em Sunset — 13 × 15" in t for t in titles)
    # mais antigas primeiro
    assert "Summit" in titles[0]
    fields = posts[0][1]["embeds"][0]["fields"]
    assert {f["name"].split(" ")[0] for f in fields} == {"admin", "amigo"}
    names, _ = _outsiders()
    assert not any(n in json.dumps(posts) for n in names)

    assert announce.run("https://discord.test/hook") == 0  # nada novo
    pend = clean_db.announcements_pending_gossip(0)
    assert len(pend) == 4 and all(p["channel_id"] == "777" for p in pend)


def test_anuncio_primeira_vez_nao_despeja_historico(grupo, clean_db, monkeypatch):
    from app import announce
    monkeypatch.setattr(announce.requests, "post",
                        lambda *a, **k: pytest.fail("não devia postar partida antiga"))
    assert announce.run("https://discord.test/hook") == 0
    assert int(clean_db.get_config(announce.SINCE_KEY)) > 0


def test_anuncio_respeita_claim_e_libera_em_falha(grupo, clean_db, monkeypatch):
    from app import announce
    clean_db.set_config(announce.SINCE_KEY, 0)
    first = _by_date()[-1]["metadata"]["match_id"]
    assert clean_db.claim_announcement(first, "bot") is True
    assert clean_db.claim_announcement(first, "poller") is False

    monkeypatch.setattr(announce.requests, "post", lambda *a, **k: _FakeResp(500, {}))
    assert announce.run("https://discord.test/hook") == 0
    # o claim do bot ficou; o que falhou foi liberado para a próxima execução
    assert clean_db.get_announcement(first)["claimed_by"] == "bot"
    assert len(clean_db.unannounced_group_matches(
        sorted(grupo._Ctx().friends), 0)) == 3


def test_anuncio_sem_webhook_nao_faz_nada(grupo):
    from app import announce
    assert announce.run("") == 0
