"""Entrypoint do poller (rodado pelo GitHub Actions).

Uso:
    python -m app.poll                 # partidas novas + RR + deep backfill em rodízio
    python -m app.poll backfill 5      # backfill de resumo (lifetime), N páginas
    python -m app.poll deep 50         # backfill profundo em lote, N partidas/jogador
    python -m app.poll recompute       # recalcula métricas do raw_gz (sem API)

Sem DATABASE_URL o script ABORTA: em CI isso significaria gravar num SQLite
descartável e reportar sucesso — foi exatamente assim que o poller passou
semanas "verde" sem gravar nada. Para usar o SQLite local de propósito:
    python -m app.poll --allow-sqlite
"""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

from . import announce, config, db
from .ingest import ingest_all, backfill_all, deep_backfill_all, recompute_all


def _fail(msg):
    print(f"ERRO: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    allow_sqlite = "--allow-sqlite" in args
    args = [a for a in args if a != "--allow-sqlite"]

    if not config.DATABASE_URL and not allow_sqlite:
        _fail("DATABASE_URL não definida — sem ela o poller gravaria num SQLite "
              "descartável. Defina o secret DATABASE_URL (ou rode com --allow-sqlite em dev).")

    if "recompute" in args:
        # Só lê o raw já gravado — não fala com a Henrik, não precisa de chave.
        db.init_db()
        r = recompute_all()
        print(f"RECOMPUTE: {r['recomputed']} partidas recalculadas, {r['skipped']} ignoradas.")
        return
    if not config.HENRIKDEV_API_KEY:
        _fail("HENRIKDEV_API_KEY não definida — a HenrikDev recusa (401) toda "
              "requisição sem chave. Nada a ingerir.")
    db.init_db()
    n = next((int(a) for a in args if a.isdigit()), None)

    if "deep" in args:
        result = deep_backfill_all(limit=n or 50)
        print(f"DEEP: {result['players']} usuários, {result['upgraded']} partidas "
              f"promovidas a completas, {result['failed']} falhas.")
    elif "backfill" in args:
        result = backfill_all(pages=n or 3)
        print(f"BACKFILL: {result['players']} usuários, {result['backfilled']} partidas-resumo.")
    else:
        # O placar sai logo depois da ingestão (antes do MMR e do deep backfill)
        # e é reescrito com o RR quando ele chega.
        posted = []
        hooks = {}
        if config.DISCORD_WEBHOOK_URL:
            hooks = {"on_new": lambda: announce.run(posted_ids=posted),
                     "after_rank": lambda: announce.refresh_rr(posted)}
        result = ingest_all(**hooks)
        print(f"OK: {result['players']} jogadores ({result.get('skipped', 0)} pulados), "
              f"{result['new_matches']} partidas novas, "
              f"{result.get('deep_upgraded', 0)} re-hidratadas.")
        if config.DISCORD_WEBHOOK_URL:
            # Rede de segurança: o que ficou pendente (webhook fora do ar antes,
            # partida que entrou pelo site). Bônus: não deixa o poller vermelho.
            try:
                n_ann = len(posted) + announce.run()
                print(f"DISCORD: {n_ann} partida(s) anunciada(s).")
            except Exception as e:
                print(f"  aviso: anúncio no Discord falhou: {e}")
        errs = result.get("errors") or []
        for e in errs:
            print(f"  aviso: {e}")
        # Todo jogador falhou = API fora / chave inválida: o job precisa ficar vermelho.
        if result["players"] and len([e for e in errs if "#" in e]) >= result["players"] \
                and not result["new_matches"]:
            _fail("nenhum jogador pôde ser ingerido nesta execução.")


if __name__ == "__main__":
    main()
