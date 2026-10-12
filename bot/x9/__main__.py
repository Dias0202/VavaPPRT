"""python -m x9 — sobe o bot. python -m x9 check — valida a configuração."""
import asyncio
import logging
import sys

from . import config


def build_provider():
    from .llm import FallbackProvider, GeminiProvider, GroqProvider

    providers = []
    if config.GEMINI_API_KEY:
        providers.append(GeminiProvider(config.GEMINI_API_KEY, config.GEMINI_MODEL))
    if config.GROQ_API_KEY:
        providers.append(GroqProvider(config.GROQ_API_KEY, config.GROQ_MODEL))
    return FallbackProvider(providers)


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
    p = build_provider()
    try:
        r = await p.chat("Responda só: ok", [{"role": "user", "content": "ping"}])
        return f"LLM ok ({r.provider}): {r.text[:40]}"
    except LLMError as e:
        return f"LLM FALHOU: {e}"


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
    from .stt import Transcriber

    class Backend:
        pass

    backend = Backend()
    backend.botapi, backend.db, backend.announce = botapi, db, announce
    db.init_db()
    brain = Brain(build_provider(), botapi, memory_turns=config.MEMORY_TURNS,
                  memory_ttl_s=config.MEMORY_TTL_S)
    bot = X9Bot(brain, backend, Transcriber(config.WHISPER_MODEL, config.WHISPER_DEVICE))
    bot.run(config.DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
