"""Cliente da API não-oficial HenrikDev (Valorant) + assets da Valorant-API.com.

Versão síncrona (requests). Usada pelo poller de ingestão e por validações
pontuais (ex.: conferir um Riot ID no momento do cadastro). As páginas web
NÃO usam este módulo — elas leem do banco.

Rate limit (Basic Key): 30 req/min. O poller serializa as chamadas e respeita
um intervalo mínimo entre requisições.
"""
import threading
import time

import requests

from . import config

HENRIK_BASE = "https://api.henrikdev.xyz/valorant"
ASSETS_BASE = "https://valorant-api.com/v1"

# Intervalo mínimo entre chamadas à Henrik (segundos). 30 req/min => 2s.
_MIN_INTERVAL = 2.1
_last_call = [0.0]

# Contadores da execução corrente (o poller grava no ingest_runs).
stats = {"calls": 0, "rate_limited": 0}


def reset_stats():
    stats["calls"] = 0
    stats["rate_limited"] = 0


# A API baixa jogadores novos em threads de background (um por cadastro). Sem
# lock, duas threads leem o mesmo _last_call e disparam juntas: 6 cadastros em
# sequência estouravam os 30 req/min e viravam 429.
_throttle_lock = threading.Lock()


def _throttle():
    with _throttle_lock:
        elapsed = time.monotonic() - _last_call[0]
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_call[0] = time.monotonic()


def _get(url, throttle=True):
    if throttle:
        _throttle()
    headers = {}
    if config.HENRIKDEV_API_KEY:
        headers["Authorization"] = config.HENRIKDEV_API_KEY
    stats["calls"] += 1
    try:
        resp = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as e:
        return {"error": f"Falha de rede: {e}"}
    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError:
            return {"error": "json_invalido", "status": 502}
    if resp.status_code == 429:
        stats["rate_limited"] += 1
        return {"error": "rate_limit", "status": 429}
    if resp.status_code == 404:
        return {"error": "not_found", "status": 404}
    return {"error": f"http_{resp.status_code}", "status": resp.status_code}


def fetch_account(name, tag):
    """Valida um Riot ID e devolve dados da conta (inclui puuid)."""
    url = f"{HENRIK_BASE}/v1/account/{name}/{tag}"
    return _get(url)


def fetch_matches(name, tag, region=None, mode=None):
    """Histórico recente de partidas (v3) — legado."""
    region = region or config.DEFAULT_REGION
    url = f"{HENRIK_BASE}/v3/matches/{region}/{name}/{tag}"
    if mode:
        url += f"?filter={mode}"
    return _get(url)


def fetch_matches_v4(name, tag, region=None, platform="pc", mode=None, size=None):
    """Histórico recente no schema v4 (canônico — rounds+kills completos).

    size: quantas partidas (as mais novas). O ao-vivo do bot pede poucas: o
    padrão traz várias partidas completas, um payload grande para detectar
    só a última."""
    region = region or config.DEFAULT_REGION
    url = f"{HENRIK_BASE}/v4/matches/{region}/{platform}/{name}/{tag}"
    params = [f"mode={mode}"] if mode else []
    if size:
        params.append(f"size={int(size)}")
    if params:
        url += "?" + "&".join(params)
    return _get(url)


def fetch_match_v4(match_id, region=None):
    """Uma partida v4 por id (backfill profundo).

    A região faz parte do path: /v4/match/{region}/{matchid}. Sem ela a Henrik
    responde 404 para toda partida.
    """
    region = region or config.DEFAULT_REGION
    url = f"{HENRIK_BASE}/v4/match/{region}/{match_id}"
    return _get(url)


def fetch_lifetime(name, tag, region=None, page=1, size=20):
    """Resumo paginado (backfill barato, sem rounds)."""
    region = region or config.DEFAULT_REGION
    url = f"{HENRIK_BASE}/v1/lifetime/matches/{region}/{name}/{tag}?page={page}&size={size}"
    return _get(url)


def fetch_mmr(name, tag, region=None):
    region = region or config.DEFAULT_REGION
    url = f"{HENRIK_BASE}/v2/mmr/{region}/{name}/{tag}"
    return _get(url)


def fetch_mmr_history(name, tag, region=None):
    """Curva de RR: uma entrada por partida ranqueada (tier, RR, ganho/perda)."""
    region = region or config.DEFAULT_REGION
    url = f"{HENRIK_BASE}/v1/mmr-history/{region}/{name}/{tag}"
    return _get(url)


# --- Assets (Valorant-API.com): sem rate limit relevante, com cache em memória.
_asset_cache = {}


def agent_icon(agent_name):
    """URL do ícone de um agente pelo nome (ex.: 'Jett')."""
    key = "agents"
    if key not in _asset_cache:
        try:
            data = requests.get(f"{ASSETS_BASE}/agents?isPlayableCharacter=true", timeout=30).json()
            _asset_cache[key] = {
                a["displayName"].lower(): a["displayIcon"] for a in data.get("data", [])
            }
        except requests.RequestException:
            _asset_cache[key] = {}
    return _asset_cache[key].get((agent_name or "").lower())
