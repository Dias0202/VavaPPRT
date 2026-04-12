import aiohttp
import os
import json
from database import get_cached_stats, set_cached_stats

HENRIK_BASE_URL = "https://api.henrikdev.xyz/valorant"
ASSETS_BASE_URL = "https://valorant-api.com/v1"

async def _fetch(url):
    api_key = os.getenv("HENRIKDEV_API_KEY")
    headers = {"Authorization": api_key} if api_key else {}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
            elif resp.status == 429:
                return {"error": "Rate limit excedido."}
            else:
                return {"error": f"Erro {resp.status}"}

async def fetch_player_matches(name, tag, region="latam"):
    cache_key = f"matches_{name}#{tag}"
    cached = get_cached_stats(cache_key)
    if cached:
        return cached

    url = f"{HENRIK_BASE_URL}/v3/matches/{region}/{name}/{tag}"
    data = await _fetch(url)
    if "error" not in data:
        set_cached_stats(cache_key, data)
    return data

async def fetch_mmr(name, tag, region="latam"):
    cache_key = f"mmr_{name}#{tag}"
    cached = get_cached_stats(cache_key)
    if cached:
        return cached

    url = f"{HENRIK_BASE_URL}/v2/mmr/{region}/{name}/{tag}"
    data = await _fetch(url)
    if "error" not in data:
        set_cached_stats(cache_key, data)
    return data

async def fetch_leaderboard(region="latam", platform="pc"):
    cache_key = f"leaderboard_{region}_{platform}"
    cached = get_cached_stats(cache_key)
    if cached:
        return cached

    url = f"{HENRIK_BASE_URL}/v3/leaderboard/{region}/{platform}"
    data = await _fetch(url)
    if "error" not in data:
        set_cached_stats(cache_key, data, ttl_minutes=30)
    return data

async def fetch_store_featured():
    cache_key = "store_featured"
    cached = get_cached_stats(cache_key)
    if cached:
        return cached

    url = f"{HENRIK_BASE_URL}/v2/store-featured"
    data = await _fetch(url)
    if "error" not in data:
        set_cached_stats(cache_key, data, ttl_minutes=60)
    return data

def get_crosshair_url(code):
    # Retorna a URL para gerar a imagem da mira
    return f"https://api.henrikdev.xyz/valorant/v1/crosshair/generate?id={code}"

async def get_agent_icon(agent_uuid):
    url = f"{ASSETS_BASE_URL}/agents/{agent_uuid}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data['data']['displayIcon']
    return None
