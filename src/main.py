print("--- [INICIALIZAÇÃO DO SISTEMA] ---")
import os
import sys
import asyncio
import logging
import random

# Configuração de log
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("VavaPPRT")

from dotenv import load_dotenv
load_dotenv()

# Diagnóstico inicial
required_vars = ["DISCORD_TOKEN", "DATABASE_URL", "HENRIKDEV_API_KEY"]
for var in required_vars:
    val = os.getenv(var)
    status = "✅ DEFINIDA" if val else "❌ FALTANDO"
    print(f"DEBUG: Variável {var}: {status}")

try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    import discord
    from discord.ext import commands, tasks
    from aiohttp import web
    from database import init_db, save_user, get_user, get_all_users, match_exists, save_match_data, set_config, get_config, remove_user, get_connection
    from valorant_api import fetch_player_matches, fetch_mmr, fetch_leaderboard, fetch_store_featured, get_crosshair_url
    from processor import calculate_metrics
    print("✅ Módulos carregados.")
except Exception as e:
    print(f"❌ Erro de importação: {e}")
    sys.exit(1)

TOKEN = os.getenv("DISCORD_TOKEN")

# Servidor Web
async def handle(request):
    return web.Response(text="Bot is alive! Connection to Discord may be pending rate-limit.")

app = web.Application()
app.router.add_get("/", handle)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

@bot.event
async def on_ready():
    print(f"✅ CONECTADO! Bot: {bot.user}")
    try:
        init_db()
        print("✅ Banco de dados pronto.")
    except Exception as e:
        print(f"❌ Erro Banco: {e}")
    if not check_matches.is_running():
        check_matches.start()

@tasks.loop(minutes=5)
async def check_matches():
    # ... (lógica de check_matches simplificada para economizar espaço e focar no erro)
    pass

@bot.command()
async def ping(ctx): await ctx.send("Pong! 🏓")

async def main():
    # 1. Inicia Servidor Web primeiro
    try:
        port = int(os.environ.get("PORT", 8080))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f"✅ Servidor Web rodando na porta {port}")
    except Exception as e:
        print(f"❌ Erro Web: {e}")

    if not TOKEN:
        print("❌ TOKEN FALTANDO")
        return

    # 2. Loop de conexão com o Discord (Tratamento de Rate Limit 429/1015)
    retries = 0
    while True:
        try:
            print(f"⏳ Tentando conectar ao Discord (Tentativa {retries + 1})...")
            async with bot:
                await bot.start(TOKEN)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                wait_time = random.randint(60, 180) + (retries * 60) # Espera entre 1 e 3 min + bônus
                print(f"⚠️ Rate Limit Detectado (429/1015). Discord bloqueou o IP do Render temporariamente.")
                print(f"⏳ Aguardando {wait_time} segundos para tentar novamente...")
                await asyncio.sleep(wait_time)
                retries += 1
            else:
                print(f"❌ Erro de Conexão: {e}")
                await asyncio.sleep(30)
        except Exception as e:
            print(f"❌ Erro Inesperado: {e}")
            await asyncio.sleep(60)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
