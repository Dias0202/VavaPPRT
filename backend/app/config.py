"""Configuração central: carrega variáveis de ambiente uma única vez."""
import os
from dotenv import load_dotenv

load_dotenv()


def _env(name, default=""):
    """Variável de ambiente sem espaços nem caracteres invisíveis nas pontas.

    `.strip()` não tira o BOM (U+FEFF): um secret colado de um arquivo salvo
    como "UTF-8 com BOM" (padrão de alguns editores no Windows) virava
    "\ufeffhttps://…" e o requests recusava a URL — o webhook do placar
    falhou assim, em silêncio, por dias."""
    return (os.getenv(name, default) or "").strip().strip("\ufeff\u200b\u200c\u200d\u2060").strip()

# Banco de dados. Se ausente, cai para SQLite local (dev).
DATABASE_URL = _env("DATABASE_URL", "")

# Caminho do SQLite de dev (os testes apontam para um arquivo temporário).
SQLITE_PATH = _env("SQLITE_PATH", "")

# Chave da API não-oficial HenrikDev (Basic = 30 req/min).
HENRIKDEV_API_KEY = _env("HENRIKDEV_API_KEY", "")

# Região padrão da Riot (na, eu, ap, kr, latam, br). Só é usada quando a conta
# não informa a própria região — a HenrikDev devolve "na" para contas do Brasil.
DEFAULT_REGION = _env("DEFAULT_REGION", "na") or "na"

# Riot ID (Nome#TAG) de quem administra o grupo. Esse cadastro nasce admin e
# aprovado; todos os outros esperam aprovação.
ADMIN_RIOT_ID = _env("ADMIN_RIOT_ID", "")

# Segredo para assinar os tokens JWT de login.
_JWT_DEV_DEFAULT = "troque-este-segredo-em-producao"
JWT_SECRET = _env("JWT_SECRET", _JWT_DEV_DEFAULT)
JWT_SECRET_IS_DEFAULT = JWT_SECRET in ("", _JWT_DEV_DEFAULT, "troque-este-segredo")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "168"))  # 7 dias

# Origens liberadas para o frontend (CORS). Vírgula-separadas.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]

# TTL do cache de telemetria, em minutos (evita estourar o rate limit).
CACHE_TTL_MINUTES = int(os.getenv("CACHE_TTL_MINUTES", "10"))

# Orçamento do poller por execução (Basic Key = 30 req/min).
DEEP_PER_RUN = int(os.getenv("DEEP_PER_RUN", "10"))       # partidas re-hidratadas
MMR_REFRESH_HOURS = int(os.getenv("MMR_REFRESH_HOURS", "6"))
# Pula quem foi checado há menos de N s (o ao-vivo do bot X9 já olhou): não
# divide a cota à toa. 0 desliga.
POLL_SKIP_RECENT_S = int(os.getenv("POLL_SKIP_RECENT_S", "0") or 0)


def admin_riot():
    """(nome, tag) em minúsculas de ADMIN_RIOT_ID, ou None."""
    if "#" not in ADMIN_RIOT_ID:
        return None
    name, tag = ADMIN_RIOT_ID.rsplit("#", 1)
    return name.strip().lower(), tag.strip().lower()


# --- Bot X9 (docs/BOT_X9.md) ------------------------------------------------
# Token de serviço do espelho HTTP /bot/v1. Vazio = rota desligada (503).
BOT_API_TOKEN = _env("BOT_API_TOKEN", "")

# Webhook do canal do placar. Vazio = o poller não anuncia nada.
DISCORD_WEBHOOK_URL = _env("DISCORD_WEBHOOK_URL", "")

# Link para a página da partida no embed (ex.: https://dias0202.github.io/VavaPPRT).
SITE_URL = _env("SITE_URL", "").rstrip("/")

# Na primeira execução, só anuncia partidas das últimas N horas (não despeja o
# histórico inteiro no canal).
ANNOUNCE_LOOKBACK_HOURS = int(os.getenv("ANNOUNCE_LOOKBACK_HOURS", "6"))
