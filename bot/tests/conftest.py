"""Testes do bot: reaproveitam a infra do backend (SQLite/Postgres descartável,
HenrikDev falsa, fixtures v4 anonimizadas). O conftest do backend precisa ser
importado ANTES de qualquer módulo app.* — é ele que prepara o ambiente."""
import importlib.util
import sys
from pathlib import Path

import pytest

BOT = Path(__file__).resolve().parent.parent
BACKEND = BOT.parent / "backend"
sys.path.insert(0, str(BOT))
sys.path.insert(0, str(BACKEND))

_spec = importlib.util.spec_from_file_location("backend_conftest", BACKEND / "tests" / "conftest.py")
backend_conftest = importlib.util.module_from_spec(_spec)
sys.modules["backend_conftest"] = backend_conftest
_spec.loader.exec_module(backend_conftest)

# fixtures do backend, reexportadas
_database = backend_conftest._database
clean_db = backend_conftest.clean_db
henrik = backend_conftest.henrik
load_matches = backend_conftest.load_matches
ADMIN_PUUID = backend_conftest.ADMIN_PUUID
FRIEND_PUUID = backend_conftest.FRIEND_PUUID

ADMIN_DISCORD = "111111111111111111"


class Backend:
    def __init__(self):
        from x9.backend import announce, botapi, db
        self.announce, self.botapi, self.db = announce, botapi, db


@pytest.fixture()
def backend(clean_db, henrik):
    """Admin + Amigo no grupo, 4 partidas ingeridas, Admin vinculado no Discord."""
    from app import db, ingest
    db.create_user("admin", "x", "Admin", "0001", "na", puuid=ADMIN_PUUID,
                   is_admin=1, status="approved")
    db.create_user("amigo", "x", "Amigo", "0002", "na", puuid=FRIEND_PUUID, status="approved")
    ingest.ingest_all(deep_budget=0)
    db.link_discord(ADMIN_DISCORD, ADMIN_PUUID)
    return Backend()
