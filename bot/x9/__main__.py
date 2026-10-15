"""python -m x9 — sobe o bot. python -m x9 check — valida a configuração."""
import asyncio
import logging
import sys

from . import config


def build_routes():
    """{rota: FallbackProvider} com a saúde (castigo) compartilhada entre as rotas."""
    from .llm import FallbackProvider, GeminiProvider, GroqProvider, ProviderHealth

    pool = {}
    if config.GEMINI_API_KEY:
        pool["gemini"] = GeminiProvider(config.GEMINI_API_KEY, config.GEMINI_MODEL)
    if config.GROQ_API_KEY:
        pool["groq"] = GroqProvider(config.GROQ_API_KEY, config.GROQ_MODEL)
        if config.GROQ_FALLBACK_MODEL and config.GROQ_FALLBACK_MODEL != config.GROQ_MODEL:
            pool["groq-small"] = GroqProvider(
                config.GROQ_API_KEY, config.GROQ_FALLBACK_MODEL,
                name=f"groq/{config.GROQ_FALLBACK_MODEL.split('/')[-1]}")
    if not pool:
        raise ValueError("Nenhum provedor de LLM configurado.")
    gossip_pool = dict(pool)
    if "gemini" in pool and config.GEMINI_GOSSIP_THINKING:
        # mesma chave e mesmo nome (castigo compartilhado), um pouco de "pensar" na fofoca
        gossip_pool["gemini"] = GeminiProvider(config.GEMINI_API_KEY, config.GEMINI_MODEL,
                                               thinking_budget=config.GEMINI_GOSSIP_THINKING)
    health = ProviderHealth()

    def route(order, src):
        chosen = [src[k] for k in order if k in src]
        chosen += [p for k, p in src.items() if p not in chosen]  # o resto vira reserva
        return FallbackProvider(chosen, health=health)

    return {"texto": route(config.LLM_ORDER_TEXT, pool),
            "voz": route(config.LLM_ORDER_VOICE, pool),
            "fofoca": route(config.LLM_ORDER_GOSSIP, gossip_pool)}


def build_provider():
    return build_routes()["texto"]


def problems():
    out = []
    if not config.DISCORD_TOKEN:
        out.append("DISCORD_TOKEN vazio (Developer Portal → Bot → Reset Token).")
    if not (config.GEMINI_API_KEY or config.GROQ_API_KEY):
        out.append("Nenhum LLM: defina GEMINI_API_KEY e/ou GROQ_API_KEY.")
    from .backend import botapi  # noqa: F401
    from app import config as bconf
    if not bconf.DATABASE_URL:
        out.append("DATABASE_URL vazio: o bot leria o SQLite local de dev, não o Supabase.")
    if not config.ANNOUNCE_CHANNEL_ID:
        out.append("ANNOUNCE_CHANNEL_ID vazio: sem placar de fallback (a fofoca ainda sai nas "
                   "mensagens do webhook).")
    return out


async def check_llm():
    from .llm import LLMError
    out = []
    for name, p in build_routes().items():
        try:
            r = await p.chat("Responda só: ok", [{"role": "user", "content": "ping"}])
            out.append(f"LLM {name} ok ({r.provider}): {r.text[:40]}")
        except LLMError as e:
            out.append(f"LLM {name} FALHOU: {e}")
    return "\n".join(out)


def setup_logging():
    """Console + bot/logs/x9.log (rotativo, 5 × 2 MB): rodando oculto pela
    tarefa agendada, o arquivo é o único lugar onde dá para ver o que houve."""
    from logging.handlers import RotatingFileHandler

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if sys.stderr is not None:  # pythonw (tarefa agendada) não tem console
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)
    logs = config.BOT_DIR / "logs"
    logs.mkdir(exist_ok=True)
    fh = RotatingFileHandler(logs / "x9.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    # O fork do voice-recv loga 1 linha/s de RTCP "inesperado" durante a call.
    logging.getLogger("discord.ext.voice_recv.reader").setLevel(logging.WARNING)
    logging.getLogger("discord.ext.voice_recv.gateway").setLevel(logging.WARNING)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    setup_logging()
    probs = problems()
    if argv[:1] == ["check"]:
        for p in probs:
            print("⚠️ ", p)
        print(asyncio.run(check_llm()))
        from .backend import db
        print(f"Banco ok: {len(db.get_roster())} jogador(es) no roster.")
        return
    fatal = [p for p in probs if p.startswith(("DISCORD_TOKEN", "Nenhum LLM"))]
    for p in probs:
        logging.getLogger("x9").warning(p)
    if fatal:
        raise SystemExit(1)

    from .backend import announce, botapi, db
    from .brain import Brain
    from .discord_bot import X9Bot
    from .moments import Moments
    from .stt import Transcriber
    from .telemetry import Telemetry

    class Backend:
        pass

    backend = Backend()
    backend.botapi, backend.db, backend.announce = botapi, db, announce
    db.init_db()
    telemetry = Telemetry()
    live, local_tools, context = None, {}, []
    if config.LIVE_ENABLED:
        from .live import PLAYING_TOOL_DESC, PLAYING_TOOL_SCHEMA, LiveTracker
        live = LiveTracker(backend, None, base_every=config.LIVE_BASE_EVERY_S,
                           playing_every=config.LIVE_PLAYING_EVERY_S,
                           min_gap=config.LIVE_MIN_GAP_S, fetch_size=config.LIVE_FETCH_SIZE or None)
        local_tools["quem_ta_jogando"] = (lambda args: live.playing_now(),
                                          PLAYING_TOOL_DESC, PLAYING_TOOL_SCHEMA)
        context.append(live.playing_line)
    routes = build_routes()
    brain = Brain(routes["texto"], botapi, memory_turns=config.MEMORY_TURNS,
                  memory_ttl_s=config.MEMORY_TTL_S, local_tools=local_tools, routes=routes,
                  context_providers=context, telemetry=telemetry)
    moments = Moments(store=db) if live else None
    bot = X9Bot(brain, backend,
                Transcriber(config.WHISPER_MODEL, config.WHISPER_DEVICE, config.WHISPER_COMPUTE),
                live=live, moments=moments, telemetry=telemetry)
    bot.run(config.DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
