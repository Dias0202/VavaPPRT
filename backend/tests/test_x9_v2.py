"""X9 v2 (docs/PLANO_X9_V2.md): tools novas, catálogo enxuto, motor de fatos,
cache em memória, fofoca com claim, apostas/votos/lore e poller em fases."""
import json
import re
import time

import pytest

from conftest import ADMIN_PUUID, FRIEND_PUUID, load_matches
from test_botapi import ADMIN_DISCORD, _by_date, _FakeResp, _outsiders, grupo  # noqa: F401

NEW_CALLS = [
    ("fatos_partida", {}),
    ("fatos_partida", {"partida": "2"}),
    ("analisar_partida", {}),
    ("analisar_partida", {"partida": "penultima"}),
    ("elo_e_rr", {}),
    ("elo_e_rr", {"jogador": "amigo"}),
    ("noite_de_hoje", {}),
    ("pre_fila", {"jogadores": "admin, amigo"}),
    ("sinergia_grupo", {}),
    ("armas_jogador", {"jogador": "eu"}),
    ("contra_o_lobby", {"jogador": "admin"}),
    ("metas_jogador", {"jogador": "amigo"}),
    ("recordes", {}),
    ("recordes", {"jogador": "admin"}),
    # catálogo enxuto do LLM
    ("partida", {"aspecto": "por_que"}),
    ("partida", {"aspecto": "fatos", "partida": "3"}),
    ("partida", {}),
    ("round", {"round": 2}),
    ("round", {"round": 2, "modo": "analisar"}),
    ("jogador", {"jogador": "amigo", "aspecto": "tendencia", "janela": 5}),
    ("jogador", {"jogador": "admin", "aspecto": "mapas_agentes"}),
    ("jogador", {"jogador": "admin", "aspecto": "vs_lobby"}),
    ("grupo", {"aspecto": "sinergia"}),
    ("grupo", {"aspecto": "elo"}),
    ("grupo", {}),
]


def test_tools_novas_respondem_sem_erro_e_sem_vazar_adversario(grupo):
    outs = []
    for name, args in NEW_CALLS:
        out = grupo.call_tool(name, args, speaker_discord_id=ADMIN_DISCORD)
        assert "erro" not in out, (name, args, out)
        outs.append(out)
    blob = json.dumps(outs, ensure_ascii=False)
    names, puuids = _outsiders()
    for n in names:
        assert not re.search(rf"\b{re.escape(n)}\b", blob), n
    for p in puuids:
        assert p not in blob
    assert ADMIN_PUUID not in blob and FRIEND_PUUID not in blob


def test_catalogo_enxuto_do_llm(grupo):
    cat = grupo.llm_catalog()
    assert len(cat) <= 9
    assert len(json.dumps(cat, ensure_ascii=False)) < len(json.dumps(grupo.tool_catalog(),
                                                                      ensure_ascii=False)) * 0.6
    for t in cat:
        assert set(t["parameters"]["required"]) <= set(t["parameters"]["properties"])
    err = grupo.call_tool("partida", {"aspecto": "horoscopo"})
    assert "Opções" in err["erro"]
    assert grupo.call_tool("round", {})["erro"].startswith("Faltou")


def test_analise_da_partida_fecha_com_o_placar(grupo):
    res = grupo.call_tool("resumo_partida", {})
    an = grupo.call_tool("analisar_partida", {})
    assert an["placar"] == res["placar"]
    fat = {f["fator"]: f["resumo"] for f in an["fatores"]}
    ours, theirs = map(int, res["placar"].split("x"))
    lados = re.findall(r"(\d+)V (\d+)D", fat["lados"])
    assert sum(int(w) for w, _ in lados) == ours
    assert sum(int(l) for _, l in lados) == theirs
    # vitória: ordena do que mais ajudou (impacto crescente)
    imp = [f["impacto"] for f in an["fatores"]]
    assert imp == sorted(imp) if an["resultado"] == "vitória" else imp == sorted(imp, reverse=True)


def test_pre_fila_aceita_lista_e_resolve_nomes(grupo):
    out = grupo.call_tool("pre_fila", {"jogadores": "adm e Amigo"})
    assert out["jogadores"] == ["admin", "amigo"]
    assert "erro" in grupo.call_tool("pre_fila", {"jogadores": "Fulano"})


# ---------------------------------------------------------------------------
# Motor de fatos
# ---------------------------------------------------------------------------
def _fake_history(mid, puuid, acs_values, base_raw, won=0, agent="Jett", kills=10):
    rows = []
    for i, acs in enumerate(acs_values):
        rows.append({"match_id": f"h{i}", "puuid": puuid, "score": acs * 20, "rounds": 20,
                     "kills": kills, "deaths": 15, "won": won, "agent": agent, "map": "Bind",
                     "started_raw": base_raw - (i + 1) * 86400 * 2})
    return rows


def test_fato_pior_acs_e_sequencia_e_estreia(grupo):
    from app import botapi, storylines
    ctx = botapi._Ctx()
    mid = _by_date()[0]["metadata"]["match_id"]
    mv = botapi._Match(botapi._raw(mid), ctx)
    me = next(p for p in mv.team_of if p in ctx.friends)
    line = storylines.player_line(mv, me)
    started = botapi._row(mid)["started_raw"]
    this = {"match_id": mid, "puuid": me, "score": line["score"], "rounds": mv.n_rounds,
            "kills": line["kills"], "deaths": line["deaths"], "won": 0, "agent": line["agent"],
            "map": mv.map, "started_raw": started}
    # 20 partidas antes, todas com ACS bem maior e outro agente, todas derrotas
    hist = {me: [this] + _fake_history(mid, me, [line["acs"] + 80] * 20, started,
                                        won=0, agent="Omen", kills=line["kills"] + 10)}
    hooks = storylines.match_hooks(mv, ctx, hist, limit=20)
    tipos = {h["tipo"]: h for h in hooks}
    assert "pior_da_vida" in tipos and str(line["acs"]) in tipos["pior_da_vida"]["texto"]
    assert "estreia" in tipos and line["agent"] in tipos["estreia"]["texto"]
    if mv.final_score()[0] < mv.final_score()[1]:
        assert any(h["tipo"] == "sequencia" and "21ª derrota" in h["texto"] for h in hooks)
    notas = [h["nota"] for h in hooks]
    assert notas == sorted(notas, reverse=True)


def test_fato_de_elo(grupo, clean_db):
    from app import botapi
    ctx = botapi._Ctx()
    mid = _by_date()[0]["metadata"]["match_id"]
    mv = botapi._Match(botapi._raw(mid), ctx)
    me = next(p for p in mv.team_of if p in ctx.friends)
    started = botapi._row(mid)["started_raw"]
    clean_db.save_rank_history([
        {"puuid": me, "match_id": "antes", "date_raw": started - 3600, "tier": 12,
         "tier_name": "Gold 1", "rr": 95, "rr_change": 15},
        {"puuid": me, "match_id": mid, "date_raw": started, "tier": 13, "tier_name": "Gold 2",
         "rr": 12, "rr_change": 17}])
    botapi.bump_version()
    hooks = botapi.call_tool("fatos_partida", {"partida": mid})["fatos"]
    assert any(h["tipo"] == "elo" and "SUBIU pra Gold 2" in h["fato"] for h in hooks)
    from app import announce
    emb = announce.build_embed(mid, ctx)
    assert any("+17 RR" in f["value"] and "subiu pra Gold 2" in f["value"] for f in emb["fields"])


def test_noite_e_apelidos():
    from app import storylines
    now = 1_800_000_000
    a, b = "pa", "pb"

    def row(i, pu, won, kills, deaths, fd, hs):
        return {"match_id": f"m{i}", "puuid": pu, "won": won, "kills": kills, "deaths": deaths,
                "score": kills * 300, "rounds": 20, "first_deaths": fd, "first_bloods": 1,
                "headshots": hs, "bodyshots": 100 - hs, "legshots": 0, "map": "Bind",
                "agent": "Jett", "full_data": 1, "started_raw": now - i * 2400}
    rows = {a: [row(i, a, i % 2, 20 if i > 2 else 8, 12, 5, 30) for i in range(6)],
            b: [row(i, b, i % 2, 12, 14, 1, 10) for i in range(6)]}
    # partida antiga, de outra sessão
    rows[a].append(row(20, a, 1, 30, 5, 0, 30))
    n = storylines.night_summary(rows, {a: "Ana", b: "Beto"},
                                 [{"puuid": a, "match_id": "m0", "rr_change": -20},
                                  {"puuid": a, "match_id": "m1", "rr_change": 18}], now=now)
    assert n["tem_noite"] and n["partidas"] == 6 and n["vitorias"] == 3
    assert len(n["resultados"]) == 6
    ana = next(p for p in n["jogadores"] if p["nome"] == "Ana")
    assert ana["rr"] == -2 and "caiu" in ana["tilt"]
    nicks = {x["apelido"]: x["nome"] for x in storylines.nicknames(rows, {a: "Ana", b: "Beto"})}
    assert nicks.get("Rei do first death") == "Ana"
    assert nicks.get("Mão de alface") == "Beto"
    assert storylines.night_summary({}, {}) == {"tem_noite": False}


def test_briefing_e_material(grupo):
    text = grupo.briefing()
    assert 'partida="1"' in text and "Grupo: admin" in text
    assert len(text) < 3000
    names, _ = _outsiders()
    assert not any(re.search(rf"\b{re.escape(n)}\b", text) for n in names)
    mid = _by_date()[0]["metadata"]["match_id"]
    m = grupo.material_fofoca(mid)
    assert m["partida"]["mapa"] and "match_id" not in m["partida"]
    assert m["amigos"] and all("puuid" not in a for a in m["amigos"])
    assert isinstance(m["fatos"], list)


def test_lore_momentos_e_validade(grupo, clean_db):
    now = int(time.time())
    clean_db.add_lore("apelido:teste", ADMIN_PUUID, "apelido", "admin é o \"Teste\"", now + 60)
    clean_db.add_lore("apelido:teste", FRIEND_PUUID, "apelido", "amigo é o \"Teste\"", now + 60)
    clean_db.add_lore("velho", None, "piada", "expirada", now - 1)
    grupo.invalidate_cache()
    assert grupo.lore_lines() == ['amigo é o "Teste"']  # título mudou de dono; expirada some
    mid = _by_date()[0]["metadata"]["match_id"]
    hooks = [{"tipo": "ace", "texto": "ACE do admin no round 7", "puuid": ADMIN_PUUID, "nota": 3}]
    grupo.atualizar_lore(mid, hooks)
    assert any("ACE do admin" in x for x in grupo.lore_lines())
    clean_db.purge_puuid(ADMIN_PUUID)
    assert not any(r["puuid"] == ADMIN_PUUID for r in clean_db.active_lore())


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def test_cache_corta_idas_ao_banco_e_versao_renova(grupo, clean_db, monkeypatch):
    calls = {"n": 0}
    orig = clean_db.get_connection

    def counting():
        calls["n"] += 1
        return orig()
    monkeypatch.setattr(clean_db, "get_connection", counting)
    grupo.call_tool("resumo_partida", {})
    first = calls["n"]
    calls["n"] = 0
    grupo.call_tool("resumo_partida", {})
    assert calls["n"] == 0 < first  # 2ª vez: nada vai ao banco
    calls["n"] = 0
    grupo.linked_label(ADMIN_DISCORD)
    assert calls["n"] == 0

    # o banco mudou por fora: o cache segura até alguém avisar (ou o prazo vencer)
    before = [p["match_id"] for p in grupo.call_tool("partidas_recentes", {"limite": 15})["partidas"]]
    conn = orig()
    try:
        conn.cursor().execute("DELETE FROM matches WHERE match_id = "
                              + ("?" if clean_db._is_sqlite(conn) else "%s"), (before[0],))
        conn.commit()
    finally:
        conn.close()
    cached = [p["match_id"] for p in grupo.call_tool("partidas_recentes", {"limite": 15})["partidas"]]
    assert cached == before
    grupo.bump_version()
    fresh = [p["match_id"] for p in grupo.call_tool("partidas_recentes", {"limite": 15})["partidas"]]
    assert fresh == before[1:]


def test_vincular_e_desvincular_invalidam_o_cache(grupo):
    assert grupo.linked_label("999") is None
    grupo.link_member("999", "amigo")
    assert grupo.linked_label("999") == "amigo"
    assert grupo.unlink_member("999") == 1
    assert grupo.linked_label("999") is None


# ---------------------------------------------------------------------------
# Anúncio: claim da fofoca, cronômetro, apostas e votos
# ---------------------------------------------------------------------------
def test_claim_da_fofoca_e_unico_e_volta_depois_de_falha(grupo, clean_db):
    mid = _by_date()[0]["metadata"]["match_id"]
    assert clean_db.claim_announcement(mid, "bot")
    clean_db.update_announcement(mid, channel_id="1", message_id="2")
    now = int(time.time())
    assert clean_db.claim_gossip(mid, now) is True
    assert clean_db.claim_gossip(mid, now) is False          # 2º processo perde
    assert clean_db.announcements_pending_gossip(0, now) == []
    clean_db.release_gossip(mid, text="já gerada", voice="voz", fmt="plantao")
    a = clean_db.get_announcement(mid)
    assert a["gossip_raw"] is None and a["gossip_tries"] == 1 and a["gossip_text"] == "já gerada"
    assert clean_db.claim_gossip(mid, now)
    # claim abandonado (bot caiu no meio) volta a ser pendente depois do prazo
    later = now + clean_db.GOSSIP_STALE_S + 1
    assert [p["match_id"] for p in clean_db.announcements_pending_gossip(0, later)] == [mid]
    assert clean_db.claim_gossip(mid, later)


def test_apostas_e_votos(clean_db):
    clean_db.init_db()
    clean_db.place_bet("1", "vitoria", now=100)
    clean_db.place_bet("2", "derrota", now=100)
    clean_db.place_bet("1", "derrota", now=150)    # troca o palpite
    clean_db.place_bet("3", "vitoria", now=500)    # depois do início: fica para a próxima
    out = clean_db.settle_bets("m1", started_raw=400, won=False, now=600)
    assert sorted((b["discord_id"], b["hit"]) for b in out) == [("1", 1), ("2", 1)]
    assert [b["discord_id"] for b in clean_db.open_bets()] == ["3"]
    clean_db.settle_bets("m2", started_raw=700, won=False, now=800)
    rank = {r["discord_id"]: (r["acertos"], r["total"]) for r in clean_db.bet_ranking()}
    assert rank == {"1": (1, 1), "2": (1, 1), "3": (0, 1)}

    clean_db.cast_mvp_vote("m1", "1", "pa")
    clean_db.cast_mvp_vote("m1", "2", "pa")
    clean_db.cast_mvp_vote("m1", "1", "pb")        # muda o voto
    assert clean_db.mvp_votes("m1") == {"pa": 1, "pb": 1}


def test_anuncio_guarda_cronometro_e_reescreve_rr(grupo, clean_db, monkeypatch):
    from app import announce
    posts, patches = [], []
    monkeypatch.setattr(announce.requests, "post", lambda url, json=None, timeout=None: (
        posts.append(json), _FakeResp(200, {"id": str(900 + len(posts)), "channel_id": "7"}))[1])
    monkeypatch.setattr(announce.requests, "patch", lambda url, json=None, timeout=None: (
        patches.append((url, json)), _FakeResp(200, {}))[1])
    clean_db.set_config(announce.SINCE_KEY, 0)
    posted = []
    assert announce.run("https://discord.test/api/webhooks/1/tok", posted_ids=posted) == 4
    a = clean_db.get_announcement(posted[0][0])
    assert a["ended_raw"] > clean_db.get_match_row(posted[0][0])["started_raw"]
    assert a["posted_raw"] >= a["ended_raw"]
    assert announce.refresh_rr(posted, "https://discord.test/api/webhooks/1/tok") == 0  # sem RR
    mid, msg = posted[-1]
    clean_db.save_rank_history([{"puuid": ADMIN_PUUID, "match_id": mid, "date_raw": 1,
                                 "tier": 12, "tier_name": "Gold 1", "rr": 40, "rr_change": -19}])
    assert announce.refresh_rr(posted, "https://discord.test/api/webhooks/1/tok?thread_id=5") == 1
    url, body = patches[0]
    assert url == f"https://discord.test/api/webhooks/1/tok/messages/{msg}?thread_id=5"
    assert any("-19 RR" in f["value"] for f in body["embeds"][0]["fields"])


# ---------------------------------------------------------------------------
# Poller em fases
# ---------------------------------------------------------------------------
@pytest.fixture()
def roster(clean_db, henrik):
    clean_db.create_user("admin", "x", "Admin", "0001", "na", puuid=ADMIN_PUUID,
                         is_admin=1, status="approved")
    clean_db.create_user("amigo", "x", "Amigo", "0002", "na", puuid=FRIEND_PUUID,
                         status="approved")
    return henrik


def test_poller_anuncia_antes_do_mmr_e_do_deep(roster):
    from app import ingest
    order = []
    roster.calls.clear()
    ingest.ingest_all(on_new=lambda: order.append(("anuncio", list(roster.calls))),
                      after_rank=lambda: order.append(("rr", list(roster.calls))))
    (k1, calls1), (k2, calls2) = order
    assert k1 == "anuncio" and set(calls1) == {"v4"}          # só partidas até aqui
    assert k2 == "rr" and "mmr-history" in calls2 and "mmr" in calls2


def test_poller_pula_quem_o_bot_acabou_de_checar(roster, clean_db):
    from app import ingest
    clean_db.update_ingest_state(ADMIN_PUUID, last_ingested_raw=int(time.time()))
    roster.calls.clear()
    r = ingest.ingest_all(skip_recent_s=240)
    assert r["skipped"] == 1 and roster.calls.count("v4") == 1


def test_checagem_leve_pede_poucas_partidas(roster, clean_db):
    from app import ingest
    member = next(m for m in clean_db.get_roster() if m["puuid"] == ADMIN_PUUID)
    got = []
    assert ingest.ingest_user(member, {ADMIN_PUUID, FRIEND_PUUID}, collect=got, size=1) == 1
    newest = max((m for m in load_matches()
                  if any(p["puuid"] == ADMIN_PUUID for p in m["players"])),
                 key=lambda m: m["metadata"]["started_at"])
    assert got == [newest["metadata"]["match_id"]]
