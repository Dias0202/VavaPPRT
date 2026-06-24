"""Configuração central: carrega variáveis de ambiente uma única vez."""
import os
from dotenv import load_dotenv

load_dotenv()

# Banco de dados. Se ausente, cai para SQLite local (dev).
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Chave da API não-oficial HenrikDev (Basic = 30 req/min).
HENRIKDEV_API_KEY = os.getenv("HENRIKDEV_API_KEY", "").strip()

# Região padrão da Riot (na, eu, ap, kr, latam, br).
DEFAULT_REGION = os.getenv("DEFAULT_REGION", "latam").strip() or "latam"

# Segredo para assinar os tokens JWT de login.
JWT_SECRET = os.getenv("JWT_SECRET", "troque-este-segredo-em-producao").strip()
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
