"""Entrypoint do poller (rodado pelo cron do GitHub Actions).

Uso:  python -m app.poll
Lê DATABASE_URL e HENRIKDEV_API_KEY do ambiente, garante o schema e ingere as
partidas novas de todos os usuários.
"""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

import sys as _sys

from . import db
from .ingest import ingest_all, backfill_all


def main():
    db.init_db()
    if "backfill" in _sys.argv[1:]:
        # backfill de resumo (histórico antigo). Rodar pontualmente, não no cron.
        pages = 3
        for a in _sys.argv[1:]:
            if a.isdigit():
                pages = int(a)
        result = backfill_all(pages=pages)
        print(f"BACKFILL: {result['users']} usuários, {result['backfilled']} partidas-resumo.")
    else:
        result = ingest_all()
        print(f"OK: {result['users']} usuários, {result['new_matches']} partidas novas.")


if __name__ == "__main__":
    main()
