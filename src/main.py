print("--- [INICIALIZAÇÃO DO SISTEMA V2.1] ---")
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

# Diagnóstico de Variáveis
for var in ["DISCORD_TOKEN", "DATABASE_URL", "HENRIKDEV_API_KEY"]:
    print(f"DEBUG: {var}: {'✅ OK' if os.getenv(var) else '❌ FALTANDO'}")

try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    import discord
    from discord.ext import commands, tasks
    from aiohttp import web
    from database import init_db, save_user, get_user, get_all_users, match_exists, save_match_data, set_config, get_config, remove_user, get_connection
    from valorant_api import fetch_player_matches, fetch_mmr, fetch_leaderboard, fetch_store_featured, get_crosshair_url
    from processor import calculate_metrics
    print("✅ Módulos locais carregados com sucesso.")
except Exception as e:
    print(f"❌ Erro ao carregar módulos: {e}")
    sys.exit(1)

TOKEN = os.getenv("DISCORD_TOKEN")

# Servidor Web para o Render (Health Check)
async def handle(request):
    return web.Response(text="Bot VavaPPRT is alive! Status: Monitoring Discord Rate-Limits.")

app = web.Application()
app.router.add_get("/", handle)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

@bot.event
async def on_ready():
    print(f"✅ SUCESSO! Bot logado como: {bot.user}")
    try:
        init_db()
        print("✅ Banco de dados inicializado.")
    except Exception as e:
        print(f"❌ Erro Banco: {e}")
    if not check_matches.is_running():
        check_matches.start()

@tasks.loop(minutes=5)
async def check_matches():
    """Loop automático para monitorar novas partidas."""
    try:
        channel_id = get_config("notification_channel")
        if not channel_id: return
        channel = bot.get_channel(int(channel_id))
        if not channel: return

        users = get_all_users()
        for discord_id, name, tag in users:
            data = await fetch_player_matches(name, tag)
            if "error" in data or 'data' not in data or not data['data']: continue
                
            for match in data['data'][:3]:
                match_id = match['metadata']['matchid']
                if not match_exists(match_id):
                    players_to_save = []
                    target_player_metrics = None
                    for p in match['players']['all_players']:
                        is_squad = any(u[1].lower() == p['name'].lower() and u[2].lower() == p['tag'].lower() for u in users)
                        if is_squad:
                            team_key = p.get('team', '').lower()
                            was_win = 1 if team_key in match.get('teams', {}) and match['teams'][team_key].get('has_won') else 0
                            p_stats = {'puuid': p['puuid'], 'riot_id_tag': f"{p['name']}#{p['tag']}", 'agent': p['character'], 
                                       'kills': p['stats']['kills'], 'deaths': p['stats']['deaths'], 'assists': p['stats']['assists'],
                                       'damage': p.get('damage_made', 0), 'score': p['stats']['score'], 'was_win': was_win}
                            players_to_save.append(p_stats)
                            if p['name'].lower() == name.lower() and p['tag'].lower() == tag.lower():
                                target_player_metrics = calculate_metrics(match, name)

                    if players_to_save:
                        save_match_data(match_id, match['metadata'], players_to_save)
                        if target_player_metrics:
                            res = "Vitória" if target_player_metrics['was_win'] else "Derrota"
                            color = 0x2ecc71 if target_player_metrics['was_win'] else 0xe74c3c
                            embed = discord.Embed(title=f"🎮 Nova Partida: {res}", color=color)
                            embed.set_author(name=f"{name}#{tag}")
                            embed.add_field(name="Mapa", value=match['metadata']['map'])
                            embed.add_field(name="KDA", value=f"{target_player_metrics['kills']}/{target_player_metrics['deaths']}/{target_player_metrics['assists']}")
                            await channel.send(embed=embed)
    except Exception as e:
        logger.warning(f"Erro no loop de partidas: {e}")

# Comandos
@bot.command()
async def ajuda(ctx):
    embed = discord.Embed(title="🤖 Menu VavaPPRT", color=0x3498db)
    embed.add_field(name="Comandos", value="`!registrar Nome#Tag`\n`!stats`\n`!configurar_canal`", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def configurar_canal(ctx):
    set_config("notification_channel", ctx.channel.id)
    await ctx.send(f"✅ Canal de notificações definido!")

@bot.command()
async def registrar(ctx, riot_id: str):
    if "#" not in riot_id: return await ctx.send("Use Nome#Tag")
    name, tag = riot_id.split("#")
    save_user(ctx.author.id, name, tag)
    await ctx.send(f"✅ {name}#{tag} registrado!")

@bot.command()
async def stats(ctx):
    user = get_user(ctx.author.id)
    if not user: return await ctx.send("Use !registrar")
    name, tag = user
    data = await fetch_player_matches(name, tag)
    if "data" in data and data['data']:
        m = calculate_metrics(data['data'][0], name)
        await ctx.send(f"📊 {name}#{tag}: {m['kills']}/{m['deaths']} com {m['agent']}")

async def main():
    # Inicia Web Server
    try:
        port = int(os.environ.get("PORT", 8080))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f"✅ Servidor Web na porta {port}")
    except Exception as e:
        print(f"❌ Erro Web: {e}")

    # Loop de Conexão Discord (Anti-429)
    retries = 0
    while True:
        try:
            print(f"⏳ Tentando conectar ao Discord (Tentativa {retries + 1})...")
            async with bot:
                await bot.start(TOKEN)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                wait = random.randint(60, 180) + (retries * 60)
                print(f"⚠️ RATE LIMIT (IP Bloqueado pelo Discord). Aguardando {wait}s...")
                await asyncio.sleep(wait)
                retries += 1
            else:
                print(f"❌ Erro Discord: {e}")
                await asyncio.sleep(60)
        except Exception as e:
            print(f"❌ Erro Fatal: {e}")
            await asyncio.sleep(60)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt: pass
