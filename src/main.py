import discord
import os
from discord.ext import commands
from dotenv import load_dotenv
from database import init_db, save_user, get_user
from valorant_api import fetch_player_matches

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    init_db()
    print(f"Bot logado como {bot.user}")

@bot.command()
async def registrar(ctx, riot_id: str):
    """Registra seu Riot ID#Tag no banco do bot."""
    if "#" not in riot_id:
        await ctx.send("Por favor, use o formato RiotID#Tag")
        return
    name, tag = riot_id.split("#")
    save_user(ctx.author.id, name, tag)
    await ctx.send(f"Usuário {name}#{tag} registrado com sucesso!")

@bot.command()
async def stats(ctx):
    """Mostra suas estatísticas recentes baseadas nas últimas partidas."""
    user = get_user(ctx.author.id)
    if not user:
        await ctx.send("Você não está registrado! Use !registrar RiotID#Tag")
        return
    
    name, tag = user
    await ctx.send(f"Buscando estatísticas de {name}#{tag}...")
    
    data = await fetch_player_matches(name, tag)
    if "error" in data:
        await ctx.send(f"Erro: {data['error']}")
        return

    if 'data' not in data or not data['data']:
        await ctx.send("Não foram encontradas partidas recentes para este usuário.")
        return

    # Processamento simples para demonstração
    last_match = data['data'][0]
    map_name = last_match['metadata']['map']
    mode = last_match['metadata']['mode']
    
    # Busca o jogador nos dados da partida
    player = next((p for p in last_match['players']['all_players'] if p['name'].lower() == name.lower()), None)
    
    if player:
        stats = player['stats']
        kd = stats['kills'] / max(1, stats['deaths'])
        embed = discord.Embed(title=f"Última partida: {map_name} ({mode})", color=0xff4654)
        embed.set_author(name=f"{name}#{tag}")
        embed.add_field(name="KDA", value=f"{stats['kills']}/{stats['deaths']}/{stats['assists']}")
        embed.add_field(name="K/D Ratio", value=f"{kd:.2f}")
        embed.add_field(name="Agente", value=player['character'])
        await ctx.send(embed=embed)
    else:
        await ctx.send("Não consegui encontrar seus dados na última partida.")

@bot.command()
async def ping(ctx):
    await ctx.send(f"Pong! Latência: {round(bot.latency * 1000)}ms")

if __name__ == '__main__':
    if not TOKEN:
        print("ERRO: DISCORD_TOKEN não encontrado no arquivo .env")
    else:
        bot.run(TOKEN)
