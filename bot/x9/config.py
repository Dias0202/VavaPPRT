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


def _float(name, default):
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _flag(name, default=True):
    v = os.getenv(name, "").strip()
    return default if not v else v not in ("0", "false", "no", "nao", "não")


def _list(name, default):
    return [x.strip() for x in (os.getenv(name, "") or default).split(",") if x.strip()]


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
# Rotas: ordem dos provedores por tarefa (gemini | groq | groq-small). A voz
# prefere o Groq (latência menor); a fofoca, o Gemini com um pouco de "pensar".
LLM_ORDER_TEXT = _list("LLM_ORDER_TEXT", "gemini,groq,groq-small")
LLM_ORDER_VOICE = _list("LLM_ORDER_VOICE", "groq,groq-small,gemini")
LLM_ORDER_GOSSIP = _list("LLM_ORDER_GOSSIP", "gemini,groq,groq-small")
GEMINI_GOSSIP_THINKING = _int("GEMINI_GOSSIP_THINKING", 512)

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small").strip()
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto").strip()
# Vazio = float16 na GPU, int8 na CPU. Para large-v3-turbo numa GPU de 4 GB: int8_float16.
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "").strip() or None
VOICE_SILENCE_S = _float("VOICE_SILENCE_S", 0.55)
# Wake antecipado (amostra do começo da fala): auto = só com GPU.
VOICE_EARLY_WAKE = os.getenv("VOICE_EARLY_WAKE", "auto").strip()
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
LIVE_PLAYING_EVERY_S = _int("LIVE_PLAYING_EVERY_S", 60)   # quem está jogando
LIVE_MIN_GAP_S = _float("LIVE_MIN_GAP_S", 5)              # 1 consulta a cada N s, no máximo
LIVE_FETCH_SIZE = _int("LIVE_FETCH_SIZE", 2)              # partidas por checagem (0 = padrão da API)

# Respostas escritas ao vivo (edição progressiva da mensagem)
STREAM_REPLIES = _flag("STREAM_REPLIES")
# Depois da fofoca: reações no placar, votação de MVP e anúncio de quem mudou de elo
GOSSIP_REACTIONS = _flag("GOSSIP_REACTIONS")
MVP_VOTE = _flag("MVP_VOTE")
PROACTIVE_RANK = _flag("PROACTIVE_RANK")
# Momentos proativos: pré-fila quando 2+ abrem o jogo, resenha no fim da noite, Oscar no domingo
PROACTIVE_PREFILA = _flag("PROACTIVE_PREFILA")
PROACTIVE_NIGHT = _flag("PROACTIVE_NIGHT")
PROACTIVE_OSCAR = _flag("PROACTIVE_OSCAR")
