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
DISCORD_GUILD_ID = _int("DISCORD_GUILD_ID")          # sync instantâneo dos /comandos
ANNOUNCE_CHANNEL_ID = _int("ANNOUNCE_CHANNEL_ID")    # canal do placar

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()

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
