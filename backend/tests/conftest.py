"""Infra de testes.

Roda contra SQLite (padrão) ou Postgres (TEST_DATABASE_URL). No Postgres cada
sessão usa um schema descartável, então dá para apontar para qualquer banco
de teste sem sujar nada.

A HenrikDev é sempre falsa: as partidas vêm de fixtures anonimizadas.
"""
import gzip
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Precisa acontecer ANTES de importar app.*: config lê o ambiente no import e
# o load_dotenv não sobrescreve variáveis já definidas.
_PG = os.environ.get("TEST_DATABASE_URL", "").strip()
_SCHEMA = f"test_{uuid.uuid4().hex[:10]}"
if _PG:
    sep = "&" if "?" in _PG else "?"
    os.environ["DATABASE_URL"] = f"{_PG}{sep}options=-csearch_path%3D{_SCHEMA}"
else:
    os.environ["DATABASE_URL"] = ""
    os.environ["SQLITE_PATH"] = os.path.join(tempfile.mkdtemp(prefix="vava-test-"), "test.db")
os.environ["HENRIKDEV_API_KEY"] = "test-key"
os.environ["ADMIN_RIOT_ID"] = "Admin#0001"
os.environ["JWT_SECRET"] = "segredo-de-teste-" + uuid.uuid4().hex

FIXTURES = ROOT / "tests" / "fixtures"
ADMIN_PUUID = "00000000-0000-4000-8000-000000000001"
FRIEND_PUUID = "00000000-0000-4000-8000-000000000002"


def load_matches():
    return json.loads(gzip.decompress((FIXTURES / "matches_v4.json.gz").read_bytes()))


@pytest.fixture(scope="session", autouse=True)
def _database():
    if _PG:
        import psycopg2
        conn = psycopg2.connect(_PG)
        conn.autocommit = True
        conn.cursor().execute(f"CREATE SCHEMA {_SCHEMA}")
        conn.close()
    yield
    from app import db
    db.close_pool()
    if _PG:
        import psycopg2
        conn = psycopg2.connect(_PG)
        conn.autocommit = True
        conn.cursor().execute(f"DROP SCHEMA {_SCHEMA} CASCADE")
        conn.close()


TABLES = ["users", "tracked_players", "app_config", "matches", "match_players",
          "player_match_derived", "player_round_stats", "kills", "spatial_events",
          "rank_history", "rank_profile", "rank_season", "player_goals",
          "ingest_runs", "ingest_state", "cache_telemetry", "discord_links",
          "bot_announcements"]


@pytest.fixture()
def clean_db():
    from app import db
    db.init_db()
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        for t in TABLES:
            cur.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()
    yield db


class FakeHenrik:
    """Substitui app.valorant_api com respostas montadas das fixtures."""

    def __init__(self, matches):
        self.matches = matches
        self.accounts = {}
        for m in matches:
            for p in m["players"]:
                self.accounts[(p["name"].lower(), p["tag"].lower())] = p["puuid"]
        self.calls = []
        self.full_by_id = {m["metadata"]["match_id"]: m for m in matches}

    def _player_matches(self, name, tag):
        pu = self.accounts.get((name.lower(), tag.lower()))
        return [m for m in self.matches if any(p["puuid"] == pu for p in m["players"])]

    def fetch_account(self, name, tag):
        self.calls.append("account")
        pu = self.accounts.get((name.lower(), tag.lower()))
        if not pu:
            return {"error": "not_found", "status": 404}
        return {"data": {"puuid": pu, "region": "na", "name": name, "tag": tag}}

    def fetch_matches_v4(self, name, tag, region=None, platform="pc", mode=None):
        self.calls.append("v4")
        return {"data": self._player_matches(name, tag)}

    def fetch_match_v4(self, match_id, region=None):
        self.calls.append("match")
        m = self.full_by_id.get(match_id)
        return {"data": m} if m else {"error": "not_found", "status": 404}

    def fetch_lifetime(self, name, tag, region=None, page=1, size=20):
        self.calls.append("lifetime")
        return {"data": []}

    def fetch_mmr_history(self, name, tag, region=None):
        self.calls.append("mmr-history")
        return {"data": []}

    def fetch_mmr(self, name, tag, region=None):
        self.calls.append("mmr")
        return {"data": {"current_data": {"currenttier": 15, "currenttierpatched": "Platinum 1",
                                          "ranking_in_tier": 40, "elo": 1240},
                         "highest_rank": {"tier": 16, "patched_tier": "Platinum 2",
                                          "season": "e10a1"},
                         "by_season": {}}}


@pytest.fixture()
def henrik(monkeypatch):
    from app import valorant_api
    fake = FakeHenrik(load_matches())
    for name in ("fetch_account", "fetch_matches_v4", "fetch_match_v4", "fetch_lifetime",
                 "fetch_mmr_history", "fetch_mmr"):
        monkeypatch.setattr(valorant_api, name, getattr(fake, name))
    monkeypatch.setattr("app.ingest.time.sleep", lambda s: None)
    return fake


@pytest.fixture()
def client(clean_db, henrik):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def register(client, username, riot, tag, password="senha123"):
    r = client.post("/auth/register", json={"username": username, "password": password,
                                           "riot_id": riot, "tagline": tag})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()
