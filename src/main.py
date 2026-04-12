import os
import sys
from dotenv import load_dotenv

# Carrega as variáveis de ambiente antes de qualquer outro import local
load_dotenv()

import discord
import asyncio
from discord.ext import commands, tasks
from aiohttp import web

# Adiciona a pasta src ao path para os imports funcionarem de qualquer lugar
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import init_db, save_user, get_user, get_all_users, match_exists, save_match_data, set_config, get_config, remove_user, get_connection
from valorant_api import fetch_player_matches, fetch_mmr, fetch_leaderboard, fetch_store_featured, get_crosshair_url
from processor import calculate_metrics

TOKEN = os.getenv("DISCORD_TOKEN")

# Servidor Web simples para manter o bot online (Render/UptimeRobot)
async def handle(request):
    return web.Response(text="Bot is alive and monitoring!")

app = web.Application()
app.router.add_get("/", handle)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

@bot.event
async def on_ready():
    print(f"Bot logado como {bot.user}")
    try:
        init_db()
        print("✅ Banco de dados inicializado com sucesso.")
    except Exception as e:
        print(f"❌ ERRO ao inicializar banco de dados: {e}")
        
    if not check_matches.is_running():
        check_matches.start()

@tasks.loop(minutes=5)
async def check_matches():
    """Loop automático para monitorar novas partidas e notificar no canal configurado."""
    print("Checando novas partidas...")
    channel_id = get_config("notification_channel")
    if not channel_id:
        return

    channel = bot.get_channel(int(channel_id))
    if not channel:
        return

    users = get_all_users()
    for discord_id, name, tag in users:
        data = await fetch_player_matches(name, tag)
        if "error" in data or 'data' not in data or not data['data']:
            continue
            
        # Pega as 3 últimas partidas para garantir que não perdeu nenhuma no intervalo
        for match in data['data'][:3]:
            match_id = match['metadata']['matchid']
            
            if not match_exists(match_id):
                print(f"Nova partida detectada para {name}#{tag}: {match_id}")
                
                # Extrai estatísticas para salvar e notificar
                players_to_save = []
                target_player_metrics = None
                
                for p in match['players']['all_players']:
                    is_squad = any(u[1].lower() == p['name'].lower() and u[2].lower() == p['tag'].lower() for u in users)
                    if is_squad:
                        team_key = p.get('team', '').lower()
                        was_win = 0
                        if team_key in match.get('teams', {}):
                            was_win = 1 if match['teams'][team_key].get('has_won') else 0
                        
                        p_stats = {
                            'puuid': p['puuid'],
                            'riot_id_tag': f"{p['name']}#{p['tag']}",
                            'agent': p['character'],
                            'kills': p['stats']['kills'],
                            'deaths': p['stats']['deaths'],
                            'assists': p['stats']['assists'],
                            'damage': p.get('damage_made', 0),
                            'score': p['stats']['score'],
                            'was_win': was_win
                        }
                        players_to_save.append(p_stats)
                        
                        # Se for o jogador que disparou a busca, preparamos a métrica para o embed
                        if p['name'].lower() == name.lower() and p['tag'].lower() == tag.lower():
                            target_player_metrics = calculate_metrics(match, name)

                if players_to_save:
                    save_match_data(match_id, match['metadata'], players_to_save)
                    
                    # Notifica no canal
                    if target_player_metrics:
                        res = "Vitória" if target_player_metrics['was_win'] else "Derrota"
                        color = 0x2ecc71 if target_player_metrics['was_win'] else 0xe74c3c
                        
                        embed = discord.Embed(title=f"🎮 Nova Partida: {res}", color=color)
                        embed.set_author(name=f"{name}#{tag}")
                        embed.add_field(name="Mapa", value=match['metadata']['map'])
                        embed.add_field(name="Agente", value=target_player_metrics['agent'])
                        embed.add_field(name="KDA", value=f"{target_player_metrics['kills']}/{target_player_metrics['deaths']}/{target_player_metrics['assists']}")
                        embed.add_field(name="ADR", value=f"{target_player_metrics['adr']:.1f}")
                        
                        await channel.send(content=f"🔔 **{name}#{tag}** acabou de jogar!", embed=embed)

@bot.command()
async def ajuda(ctx):
    """Mostra o menu de ajuda personalizado."""
    embed = discord.Embed(title="🤖 Menu de Ajuda - VavaPPRT", color=0x3498db)
    
    comandos_essenciais = (
        "`!registrar Nome#Tag`: Vincula sua conta e ativa monitoramento.\n"
        "`!remover`: Para de monitorar sua conta.\n"
        "`!configurar_canal`: Define o canal atual para alertas automáticos."
    )
    embed.add_field(name="🚀 Essenciais", value=comandos_essenciais, inline=False)
    
    comandos_stats = (
        "`!stats`: Resumo da sua última partida.\n"
        "`!ultimas`: Lista suas 5 partidas recentes.\n"
        "`!rank [Nome#Tag]`: Mostra o elo atual.\n"
        "`!analise Nome#Tag`: Análise de WinRate e K/D histórico.\n"
        "`!mapas Nome#Tag`: WinRate detalhado por mapa."
    )
    embed.add_field(name="📊 Estatísticas", value=comandos_stats, inline=False)
    
    comandos_util = (
        "`!squad`: Status atual do esquadrão.\n"
        "`!loja`: Featured bundle da loja.\n"
        "`!mira [codigo]`: Visualização de mira.\n"
        "`!leaderboard [region]`: Top 10 jogadores."
    )
    embed.add_field(name="🛠️ Utilidades", value=comandos_util, inline=False)
    
    embed.set_footer(text="Bot de Telemetria Valorant - v2.0")
    await ctx.send(embed=embed)

@bot.command()
async def configurar_canal(ctx):
    """Define o canal atual como o canal de notificações automáticas."""
    set_config("notification_channel", ctx.channel.id)
    await ctx.send(f"✅ Canal de notificações automáticas definido para: {ctx.channel.mention}")

@bot.command()
async def registrar(ctx, riot_id: str):
    """Adiciona um Riot ID#Tag à lista de monitoramento do esquadrão."""
    if "#" not in riot_id:
        await ctx.send("Por favor, use o formato RiotID#Tag")
        return
    name, tag = riot_id.split("#")
    save_user(ctx.author.id, name, tag)
    await ctx.send(f"✅ {name}#{tag} vinculado ao seu Discord e adicionado ao monitoramento!")

@bot.command()
async def remover(ctx):
    """Remove o usuário do monitoramento."""
    remove_user(ctx.author.id)
    await ctx.send("❌ Você foi removido da lista de monitoramento.")

@bot.command()
async def rank(ctx, name_tag: str = None):
    """Mostra o elo atual de um jogador."""
    if not name_tag:
        user = get_user(ctx.author.id)
        if not user:
            await ctx.send("Você não está registrado! Use !registrar Nome#Tag")
            return
        name, tag = user
    else:
        if "#" not in name_tag:
            await ctx.send("Use o formato Nome#Tag")
            return
        name, tag = name_tag.split("#")

    await ctx.send(f"Buscando rank de {name}#{tag}...")
    data = await fetch_mmr(name, tag)
    
    if "error" in data:
        await ctx.send(f"Erro: {data['error']}")
        return

    mmr = data.get('data')
    if not mmr or 'currenttierpatched' not in mmr:
        await ctx.send("Não consegui encontrar dados de MMR para este jogador.")
        return

    embed = discord.Embed(title=f"Rank: {mmr['currenttierpatched']}", color=0x3498db)
    embed.set_author(name=f"{name}#{tag}")
    embed.add_field(name="RR Atual", value=str(mmr.get('ranking_in_tier', 0)))
    embed.add_field(name="MMR Change", value=str(mmr.get('mmr_change_to_last_game', 0)))
    if mmr.get('images'):
        embed.set_thumbnail(url=mmr['images']['small'])
    
    await ctx.send(embed=embed)

@bot.command()
async def ultimas(ctx, name_tag: str = None):
    """Lista as últimas 5 partidas do jogador."""
    if not name_tag:
        user = get_user(ctx.author.id)
        if not user:
            await ctx.send("Use !registrar Nome#Tag")
            return
        name, tag = user
    else:
        if "#" not in name_tag:
            await ctx.send("Use Nome#Tag")
            return
        name, tag = name_tag.split("#")

    await ctx.send(f"Buscando últimas partidas de {name}#{tag}...")
    data = await fetch_player_matches(name, tag)
    if "error" in data or 'data' not in data:
        await ctx.send("Erro ao buscar partidas.")
        return

    embed = discord.Embed(title=f"Últimas Partidas: {name}#{tag}", color=0x3498db)
    for match in data['data'][:5]:
        meta = match['metadata']
        player = next((p for p in match['players']['all_players'] if p['name'].lower() == name.lower()), None)
        if player:
            team_key = player['team'].lower()
            res = "Vitória" if match['teams'][team_key]['has_won'] else "Derrota"
            kda = f"{player['stats']['kills']}/{player['stats']['deaths']}/{player['stats']['assists']}"
            embed.add_field(name=f"{meta['map']} - {res}", value=f"Agente: {player['character']} | KDA: {kda}", inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
async def mapas(ctx, name_tag: str):
    """Análise de WinRate por mapa."""
    if "#" not in name_tag:
        await ctx.send("Use Nome#Tag")
        return
        
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT m.map, COUNT(*), SUM(p.was_win)
        FROM player_match_stats p
        JOIN matches m ON p.match_id = m.match_id
        WHERE p.riot_id_tag LIKE %s
        GROUP BY m.map
    ''', (f"{name_tag}%",))
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        await ctx.send("Sem dados históricos. Use !puxar_historico.")
        return
        
    embed = discord.Embed(title=f"🗺️ Estatísticas por Mapa: {name_tag}", color=0xe67e22)
    for map_name, total, wins in rows:
        wr = (wins / total) * 100
        embed.add_field(name=map_name, value=f"Jogos: {total} | WR: {wr:.1f}%", inline=True)
        
    await ctx.send(embed=embed)

@bot.command()
async def stats(ctx):
    """Mostra suas estatísticas recentes."""
    user = get_user(ctx.author.id)
    if not user:
        await ctx.send("Você não está registrado!")
        return
    
    name, tag = user
    await ctx.send(f"Buscando estatísticas de {name}#{tag}...")
    
    data = await fetch_player_matches(name, tag)
    if "error" in data or 'data' not in data or not data['data']:
        await ctx.send("Erro ao buscar dados.")
        return

    last_match = data['data'][0]
    metrics = calculate_metrics(last_match, name)
    
    if metrics:
        mmr_data = await fetch_mmr(name, tag)
        rank_str = "N/A"
        thumbnail = None
        if "data" in mmr_data:
            rank_str = mmr_data['data'].get('currenttierpatched', 'N/A')
            thumbnail = mmr_data['data'].get('images', {}).get('small')

        embed = discord.Embed(title=f"Última partida: {last_match['metadata']['map']}", color=0xff4654)
        embed.set_author(name=f"{name}#{tag}")
        embed.add_field(name="Rank", value=rank_str)
        embed.add_field(name="KDA", value=f"{metrics['kills']}/{metrics['deaths']}/{metrics['assists']}")
        embed.add_field(name="ADR", value=f"{metrics['adr']:.1f}")
        embed.add_field(name="Agente", value=metrics['agent'])
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        await ctx.send(embed=embed)

@bot.command()
async def squad(ctx):
    """Analisa o desempenho do esquadrão completo."""
    users = get_all_users()
    if not users:
        await ctx.send("Ninguém está registrado!")
        return
        
    embed = discord.Embed(title="📊 Status do Esquadrão VavaPPRT", color=0x3498db)
    for discord_id, name, tag in users:
        data = await fetch_player_matches(name, tag)
        if "data" in data and data['data']:
            last_match = data['data'][0]
            metrics = calculate_metrics(last_match, name)
            if metrics:
                perf = "🔥" if metrics['adr'] > 150 else "🧊"
                embed.add_field(name=f"{name}#{tag} {perf}", value=f"ADR: {metrics['adr']:.1f} | Agente: {metrics['agent']}", inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
async def puxar_historico(ctx):
    """Puxa o histórico de todos jogadores para análise."""
    users = get_all_users()
    if not users:
        await ctx.send("Ninguém registrado.")
        return
        
    await ctx.send(f"⏳ Coletando histórico para {len(users)} jogadores...")
    total_matches_saved = 0
    for discord_id, name, tag in users:
        data = await fetch_player_matches(name, tag)
        if "data" not in data: continue
        for match in data['data']:
            match_id = match['metadata']['matchid']
            if not match_exists(match_id):
                players_to_save = []
                for p in match['players']['all_players']:
                    is_squad = any(u[1].lower() == p['name'].lower() and u[2].lower() == p['tag'].lower() for u in users)
                    if is_squad:
                        team_key = p.get('team', '').lower()
                        was_win = 0
                        if team_key in match.get('teams', {}):
                            was_win = 1 if match['teams'][team_key].get('has_won') else 0
                        
                        players_to_save.append({
                            'puuid': p['puuid'],
                            'riot_id_tag': f"{p['name']}#{p['tag']}",
                            'agent': p['character'],
                            'kills': p['stats']['kills'],
                            'deaths': p['stats']['deaths'],
                            'assists': p['stats']['assists'],
                            'damage': p.get('damage_made', 0),
                            'score': p['stats']['score'],
                            'was_win': was_win
                        })
                if players_to_save:
                    save_match_data(match_id, match['metadata'], players_to_save)
                    total_matches_saved += 1
                    
    await ctx.send(f"✅ Histórico atualizado! {total_matches_saved} novas partidas salvas.")

@bot.command()
async def analise(ctx, name_tag: str):
    """Análise histórica de um jogador."""
    if "#" not in name_tag:
        await ctx.send("Use Nome#Tag")
        return
        
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT agent, kills, deaths, assists, damage_made, was_win 
        FROM player_match_stats 
        WHERE riot_id_tag LIKE %s
    ''', (f"{name_tag}%",))
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        await ctx.send("Sem dados históricos.")
        return
        
    total_games = len(rows)
    wins = sum(r[5] for r in rows)
    win_rate = (wins / total_games) * 100
    
    embed = discord.Embed(title=f"📈 Análise: {name_tag}", color=0x2ecc71)
    embed.add_field(name="Partidas", value=str(total_games))
    embed.add_field(name="Win Rate", value=f"{win_rate:.1f}%")
    await ctx.send(embed=embed)

@bot.command()
async def leaderboard(ctx, region: str = "latam"):
    """Top 10 leaderboard."""
    data = await fetch_leaderboard(region)
    if "error" in data:
        await ctx.send("Erro na API.")
        return
    lb = data.get('data', [])[:10]
    desc = ""
    for i, p in enumerate(lb):
        name = p.get('IsAnonymized') and "Anônimo" or f"{p.get('gameName')}#{p.get('tagLine')}"
        desc += f"**{i+1}.** {name} - {p.get('rankedRating')} RR\n"
    embed = discord.Embed(title=f"🏆 Top 10 - {region.upper()}", description=desc, color=0xf1c40f)
    await ctx.send(embed=embed)

@bot.command()
async def loja(ctx):
    """Featured bundle."""
    data = await fetch_store_featured()
    featured = data.get('data', [])
    if not featured:
        await ctx.send("Vazio.")
        return
    embed = discord.Embed(title="🛍️ Loja em Destaque", color=0x9b59b6)
    await ctx.send(embed=embed)

@bot.command()
async def mira(ctx, codigo: str):
    """Visualização de mira."""
    url = get_crosshair_url(codigo)
    embed = discord.Embed(title="🎯 Mira")
    embed.set_image(url=url)
    await ctx.send(embed=embed)

async def main():
    # Inicia o servidor web ANTES de tudo para o Render não dar timeout
    try:
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 8080))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f"✅ Servidor Web rodando na porta {port}")
    except Exception as e:
        print(f"❌ Erro ao iniciar servidor web: {e}")

    # Tenta iniciar o bot
    if not TOKEN:
        print("❌ ERRO: DISCORD_TOKEN não configurado!")
        return

    try:
        async with bot:
            await bot.start(TOKEN)
    except Exception as e:
        print(f"❌ Erro fatal no bot: {e}")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
