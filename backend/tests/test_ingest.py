"""Poller: orçamento de cota, deep backfill em rodízio e falhas barulhentas."""
import pytest

from app import config, db, ingest
from conftest import ADMIN_PUUID, load_matches, register


def test_poll_sem_banco_aborta(monkeypatch):
    from app import poll
    monkeypatch.setattr(config, "DATABASE_URL", "")
    with pytest.raises(SystemExit) as e:
        poll.main([])
    assert e.value.code == 1


def test_ingestao_idempotente(client, henrik):
    register(client, "admin", "Admin", "0001")
    n = len(db.derived_for_puuid(ADMIN_PUUID))
    assert n == len(load_matches())
    assert ingest.ingest_all()["new_matches"] == 0
    assert len(db.derived_for_puuid(ADMIN_PUUID)) == n


def test_mmr_so_quando_precisa(client, henrik):
    register(client, "admin", "Admin", "0001")
    henrik.calls.clear()
    ingest.ingest_all()  # nada novo e mmr recém-baixado no cadastro
    assert "mmr" not in henrik.calls and "mmr-history" not in henrik.calls
    db.update_ingest_state(ADMIN_PUUID, last_mmr_raw=0)
    henrik.calls.clear()
    ingest.ingest_all()
    assert "mmr" in henrik.calls


def test_deep_backfill_promove_resumo(client, henrik):
    register(client, "admin", "Admin", "0001")
    # rebaixa uma partida a "resumo", como se viesse do lifetime
    mid = load_matches()[1]["metadata"]["match_id"]
    conn = db.get_connection()
    try:
        ph = db._ph(conn)
        conn.cursor().execute(f"UPDATE player_match_derived SET full_data = 0 WHERE match_id = {ph}", (mid,))
        conn.cursor().execute(f"UPDATE matches SET raw_gz = NULL WHERE match_id = {ph}", (mid,))
        conn.commit()
    finally:
        conn.close()
    assert db.deep_coverage(ADMIN_PUUID)[0] == len(load_matches()) - 1

    res = ingest.ingest_all(deep_budget=5)
    assert res["deep_upgraded"] == 1
    assert db.deep_coverage(ADMIN_PUUID)[0] == len(load_matches())


def test_deep_rotation_para_no_rate_limit(client, henrik, monkeypatch):
    register(client, "admin", "Admin", "0001")
    conn = db.get_connection()
    try:
        conn.cursor().execute("UPDATE player_match_derived SET full_data = 0")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr("app.valorant_api.fetch_match_v4",
                        lambda mid, region=None: {"error": "rate_limit", "status": 429})
    roster = db.get_roster()
    r = ingest.deep_rotation(roster, {u["puuid"] for u in roster}, budget=10)
    assert r == {"upgraded": 0, "failed": 0, "rate_limited": True}


def test_falha_de_api_vira_erro_registrado(client, henrik, monkeypatch):
    register(client, "admin", "Admin", "0001")
    monkeypatch.setattr("app.valorant_api.fetch_matches_v4",
                        lambda *a, **k: {"error": "http_503", "status": 503})
    res = ingest.ingest_all()
    assert res["errors"]
    run = db.recent_ingest_runs(1)[0]
    assert run["ok"] == 0 and run["errors"]
    assert db.get_ingest_state(ADMIN_PUUID)["last_error"].startswith("v4")
