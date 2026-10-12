"""Ponte para o backend: o bot importa app.botapi direto (docs/BOT_X9.md §1)."""
import sys

from . import config  # noqa: F401  (carrega o .env do bot antes do backend)

_BACKEND = str(config.REPO_DIR / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app import announce, botapi, db  # noqa: E402

__all__ = ["announce", "botapi", "db"]
