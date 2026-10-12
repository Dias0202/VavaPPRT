"""Configuração do bot X9 (bot/.env). Carregado ANTES do backend: o load_dotenv
não sobrescreve variável já definida, então o .env do bot prevalece."""
import os
from pathlib import Path

from dotenv import load_dotenv

BOT_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BOT_DIR.parent
load_dotenv(BOT_DIR / ".env")


def _int(name, default=None):
    v = os.getenv(name, "").strip()
    return int(v) if v.isdigit() else default


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
# Servidores do grupo (vírgula-separados). Os /comandos são globais; aqui o
# bot só limpa cópias antigas por servidor, que apareceriam duplicadas.
DISCORD_GUILD_IDS = [int(x) for x in os.getenv("DISCORD_GUILD_ID", "").replace(" ", "").split(",")
                     if x.isdigit()]
ANNOUNCE_CHANNEL_ID = _int("ANNOUNCE_CHANNEL_ID")    # canal do placar

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
# Cota por minuto (TPM) do free tier é por modelo: quando o principal estoura,
# o reserva responde na hora em vez de o bot esperar o minuto virar. Vazio desliga.
GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b").strip()

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small").strip()
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto").strip()
TTS_ENGINE = os.getenv("TTS_ENGINE", "auto").strip()          # auto | edge | piper
TTS_VOICE = os.getenv("TTS_VOICE", "pt-BR-AntonioNeural").strip()  # voz do edge-tts
PIPER_VOICE = os.getenv("PIPER_VOICE", "pt_BR-faber-medium").strip()

MEMORY_TURNS = _int("MEMORY_TURNS", 10)
MEMORY_TTL_S = _int("MEMORY_TTL_S", 900)
GOSSIP_POLL_S = _int("GOSSIP_POLL_S", 120)
GOSSIP_MAX_AGE_H = _int("GOSSIP_MAX_AGE_H", 48)
# Partida sem dono depois de N min do início: o poller não anunciou (webhook
# desligado ou fora do ar) e o bot posta o placar ele mesmo.
BOT_CLAIM_AFTER_MIN = _int("BOT_CLAIM_AFTER_MIN", 80)
BOT_POSTS_SCOREBOARD = os.getenv("BOT_POSTS_SCOREBOARD", "1").strip() != "0"

# Ao vivo: presença do Discord + checagens rápidas na HenrikDev (x9/live.py).
# Precisa de Presence Intent e Server Members Intent ligados no Developer Portal.
LIVE_ENABLED = os.getenv("LIVE_ENABLED", "1").strip() != "0"
LIVE_BASE_EVERY_S = _int("LIVE_BASE_EVERY_S", 300)       # varredura de todos
LIVE_PLAYING_EVERY_S = _int("LIVE_PLAYING_EVERY_S", 120)  # quem está jogando
