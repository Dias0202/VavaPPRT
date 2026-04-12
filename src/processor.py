def calculate_metrics(match_data, player_name):
    # Busca o jogador nos dados da partida
    players = match_data['players']['all_players']
    player = next((p for p in players if p['name'].lower() == player_name.lower()), None)
    
    if not player:
        return None
        
    rounds_played = match_data['metadata']['rounds_played']
    stats = player['stats']

    # ADR: Dano Médio por Round
    # Na API Henrik v3, o dano total está em 'damage_made' no nível do jogador, não dentro de 'stats'
    damage = player.get('damage_made', 0)
    adr = (damage / rounds_played) if rounds_played > 0 else 0.0

    # K/D
    kd = stats['kills'] / max(1, stats['deaths'])

    # HS% (Cálculo simplificado baseado nos tiros)
    # Nota: Algumas APIs fornecem isso pronto, na Henrik v3/v4 precisamos iterar rounds se quisermos precisão total.
    # Por agora, usaremos os stats básicos fornecidos.

    # was_win logic: safely check if team exists in teams object
    team_key = player.get('team', '').lower()
    was_win = 0
    if team_key in match_data.get('teams', {}):
        was_win = 1 if match_data['teams'][team_key].get('has_won') else 0

    return {
        "adr": adr,
        "kd": kd,
        "agent": player['character'],
        "score_per_round": (stats['score'] / rounds_played) if rounds_played > 0 else 0.0,
        "kills": stats['kills'],
        "deaths": stats['deaths'],
        "assists": stats['assists'],
        "was_win": was_win
    }


def get_team_performance(matches_stats):
    # Analisa quem puxa pra cima/baixo baseado em ADR e Win Rate histórico
    # matches_stats: lista de dicionários com as métricas calculadas
    if not matches_stats:
        return "Sem dados suficientes."
        
    avg_adr = sum(m['adr'] for m in matches_stats) / len(matches_stats)
    
    # Identifica tendências
    performance = "Estável"
    if avg_adr > 200: performance = "Carregando (High Impact)"
    elif avg_adr < 100: performance = "Suporte Passivo (Low Impact)"
    
    return performance
