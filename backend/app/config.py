"""Configuração central: carrega variáveis de ambiente uma única vez."""
import os
from dotenv import load_dotenv

load_dotenv()

# Banco de dados. Se ausente, cai para SQLite local (dev).
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Caminho do SQLite de dev (os testes apontam para um arquivo temporário).
SQLITE_PATH = os.getenv("SQLITE_PATH", "").strip()

# Chave da API não-oficial HenrikDev (Basic = 30 req/min).
HENRIKDEV_API_KEY = os.getenv("HENRIKDEV_API_KEY", "").strip()

# Região padrão da Riot (na, eu, ap, kr, latam, br). Só é usada quando a conta
# não informa a própria região — a HenrikDev devolve "na" para contas do Brasil.
DEFAULT_REGION = os.getenv("DEFAULT_REGION", "na").strip() or "na"

# Riot ID (Nome#TAG) de quem administra o grupo. Esse cadastro nasce admin e
# aprovado; todos os outros esperam aprovação.
ADMIN_RIOT_ID = os.getenv("ADMIN_RIOT_ID", "").strip()

# Segredo para assinar os tokens JWT de login.
_JWT_DEV_DEFAULT = "troque-este-segredo-em-producao"
JWT_SECRET = os.getenv("JWT_SECRET", _JWT_DEV_DEFAULT).strip()
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


def admin_riot():
    """(nome, tag) em minúsculas de ADMIN_RIOT_ID, ou None."""
    if "#" not in ADMIN_RIOT_ID:
        return None
    name, tag = ADMIN_RIOT_ID.rsplit("#", 1)
    return name.strip().lower(), tag.strip().lower()


# --- Bot X9 (docs/BOT_X9.md) ------------------------------------------------
# Token de serviço do espelho HTTP /bot/v1. Vazio = rota desligada (503).
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "").strip()

# Webhook do canal do placar. Vazio = o poller não anuncia nada.
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

# Link para a página da partida no embed (ex.: https://dias0202.github.io/VavaPPRT).
SITE_URL = os.getenv("SITE_URL", "").strip().rstrip("/")

# Na primeira execução, só anuncia partidas das últimas N horas (não despeja o
# histórico inteiro no canal).
ANNOUNCE_LOOKBACK_HOURS = int(os.getenv("ANNOUNCE_LOOKBACK_HOURS", "6"))
