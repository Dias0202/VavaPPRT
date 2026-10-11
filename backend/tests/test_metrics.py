"""Motor de métricas sobre partidas v4 reais (anonimizadas)."""
from collections import Counter

import pytest

from app import metrics as M
from conftest import ADMIN_PUUID, load_matches

MATCHES = load_matches()


@pytest.mark.parametrize("match", MATCHES, ids=lambda m: m["metadata"]["match_id"][-4:])
def test_um_first_kill_por_round(match):
    kills = M.annotate_kills(match)
    firsts = Counter(k["round"] for k in kills if k["is_first"])
    rounds_with_kills = {k["round"] for k in kills}
    assert set(firsts) == rounds_with_kills
    assert all(v == 1 for v in firsts.values())


@pytest.mark.parametrize("match", MATCHES, ids=lambda m: m["metadata"]["match_id"][-4:])
def test_derivadas_batem_com_o_placar(match):
    """Kills/mortes derivadas = placar oficial; KAST e rounds dentro dos limites."""
    kills = M.annotate_kills(match)
    attackers = M.infer_attacker_by_round(match)
    n_rounds = len(match["rounds"])
    for p in match["players"]:
        d = M.build_player_derived(match, p["puuid"], kills, attackers)
        assert d["kills"] == p["stats"]["kills"]
        assert d["deaths"] == p["stats"]["deaths"]
        assert d["rounds"] == n_rounds
        assert 0 <= d["kast_rounds"] <= n_rounds
        assert d["atk_rounds"] + d["def_rounds"] <= n_rounds
        assert d["first_bloods"] <= n_rounds and d["first_deaths"] <= n_rounds
        assert d["deaths_traded"] <= d["deaths"]
        report = M.validate_match_metrics(match, p["puuid"])
        assert report.get("clean", True), report


def test_round_rows_do_admin():
    match = MATCHES[0]
    rows = M.player_round_rows(match, ADMIN_PUUID)
    assert len(rows) == len(match["rounds"])
    assert sum(r["kills"] for r in rows) == next(
        p for p in match["players"] if p["puuid"] == ADMIN_PUUID)["stats"]["kills"]
    assert {r["buy_type"] for r in rows} <= {"eco", "force", "full", "unknown"}


def test_classify_buy():
    assert M.classify_buy(None) == "unknown"
    assert M.classify_buy(800) == "eco"
    assert M.classify_buy(2500) == "force"
    assert M.classify_buy(4700) == "full"


def test_partidas_da_fixture_sao_padrao():
    assert all(M.is_standard(m) for m in MATCHES)
