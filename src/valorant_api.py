import aiohttp
import os
from database import get_cached_stats, set_cached_stats

HENRIK_BASE_URL = "https://api.henrikdev.xyz/valorant"
ASSETS_BASE_URL = "https://valorant-api.com/v1"

async def fetch_player_matches(name, tag, region="latam"):
    riot_id_tag = f"{name}#{tag}"
    cached = get_cached_stats(riot_id_tag)
    if cached:
        return cached

    api_key = os.getenv("HENRIKDEV_API_KEY")
    headers = {"Authorization": api_key} if api_key else {}
    
    url = f"{HENRIK_BASE_URL}/v3/matches/{region}/{name}/{tag}"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                set_cached_stats(riot_id_tag, data)
                return data
            elif resp.status == 429:
                return {"error": "Rate limit excedido. Tente novamente em alguns minutos."}
            else:
                return {"error": f"Erro {resp.status} ao buscar dados."}

async def get_agent_icon(agent_uuid):
    url = f"{ASSETS_BASE_URL}/agents/{agent_uuid}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data['data']['displayIcon']
    return None
