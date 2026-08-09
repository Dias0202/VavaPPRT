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

from . import config, db
from .ingest import ingest_all, backfill_all, deep_backfill_all, recompute_all


def main():
    if "recompute" in _sys.argv[1:]:
        # Só lê o raw já gravado — não fala com a Henrik, não precisa de chave.
        db.init_db()
        r = recompute_all()
        print(f"RECOMPUTE: {r['recomputed']} partidas recalculadas, {r['skipped']} ignoradas.")
        return
    if not config.HENRIKDEV_API_KEY:
        print("ERRO: HENRIKDEV_API_KEY não definida — a HenrikDev recusa (401) "
              "toda requisição sem chave. Nada a ingerir.", file=sys.stderr)
        raise SystemExit(1)
    db.init_db()
    args = _sys.argv[1:]
    n = next((int(a) for a in args if a.isdigit()), None)

    if "deep" in args:
        # Re-hidrata partidas de resumo em completas. 1 chamada por partida a
        # 30 req/min: ~2s cada. Sob demanda, nunca no cron de 10min.
        result = deep_backfill_all(limit=n or 50)
        print(f"DEEP: {result['players']} usuários, {result['upgraded']} partidas "
              f"promovidas a completas, {result['failed']} falhas.")
    elif "backfill" in args:
        # backfill de resumo (histórico antigo). Rodar pontualmente, não no cron.
        result = backfill_all(pages=n or 3)
        print(f"BACKFILL: {result['players']} usuários, {result['backfilled']} partidas-resumo.")
    else:
        result = ingest_all()
        print(f"OK: {result['players']} usuários, {result['new_matches']} partidas novas.")


if __name__ == "__main__":
    main()
