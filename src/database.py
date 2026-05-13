import psycopg2
import sqlite3
import json
import os
from datetime import datetime, timedelta

def get_connection():
    url = os.getenv('DATABASE_URL')
    if not url:
        # Fallback para SQLite se DATABASE_URL não estiver presente (útil para dev local ou persistência em volume)
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'bot_database.db')
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        return sqlite3.connect(db_path)
    return psycopg2.connect(url)

def init_db():
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Determina o tipo de placeholder para o banco (Postgres usa %s, SQLite usa ?)
        is_sqlite = isinstance(conn, sqlite3.Connection)
        placeholder = '?' if is_sqlite else '%s'

        # Tabela de Usuários Monitorados
        cursor.execute(f'''CREATE TABLE IF NOT EXISTS users (
            riot_id TEXT,
            tagline TEXT,
            discord_id TEXT,
            puuid TEXT,
            region TEXT DEFAULT 'latam',
            PRIMARY KEY (riot_id, tagline)
        )''')

        # Cache temporário
        cursor.execute(f'''CREATE TABLE IF NOT EXISTS cache_telemetry (
            riot_id_tag TEXT PRIMARY KEY,
            last_updated TEXT,
            payload_json TEXT NOT NULL
        )''')

        # Histórico de partidas
        cursor.execute(f'''CREATE TABLE IF NOT EXISTS matches (
            match_id TEXT PRIMARY KEY,
            map TEXT,
            mode TEXT,
            match_start BIGINT,
            rounds_played INTEGER,
            data_json TEXT NOT NULL
        )''')

        # Estatísticas individuais
        cursor.execute(f'''CREATE TABLE IF NOT EXISTS player_match_stats (
            match_id TEXT,
            puuid TEXT,
            riot_id_tag TEXT,
            agent TEXT,
            kills INTEGER,
            deaths INTEGER,
            assists INTEGER,
            damage_made INTEGER,
            score INTEGER,
            was_win INTEGER,
            PRIMARY KEY (match_id, puuid)
        )''')

        # Configurações do bot
        cursor.execute(f'''CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Falha crítica ao inicializar banco: {e}")
        raise e

def set_config(key, value):
    conn = get_connection()
    cursor = conn.cursor()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    if is_sqlite:
        cursor.execute('''
            INSERT INTO bot_config (key, value) VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
        ''', (key, str(value)))
    else:
        cursor.execute('''
            INSERT INTO bot_config (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        ''', (key, str(value)))
    conn.commit()
    conn.close()

def get_config(key):
    conn = get_connection()
    cursor = conn.cursor()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    placeholder = '?' if is_sqlite else '%s'
    cursor.execute(f'SELECT value FROM bot_config WHERE key = {placeholder}', (key,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else None

def save_user(discord_id, riot_id, tagline):
    conn = get_connection()
    cursor = conn.cursor()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    if is_sqlite:
        cursor.execute('''
            INSERT INTO users (discord_id, riot_id, tagline) VALUES (?, ?, ?)
            ON CONFLICT (riot_id, tagline) DO UPDATE SET discord_id = excluded.discord_id
        ''', (str(discord_id), riot_id, tagline))
    else:
        cursor.execute('''
            INSERT INTO users (discord_id, riot_id, tagline) VALUES (%s, %s, %s)
            ON CONFLICT (riot_id, tagline) DO UPDATE SET discord_id = EXCLUDED.discord_id
        ''', (str(discord_id), riot_id, tagline))
    conn.commit()
    conn.close()

def remove_user(discord_id):
    conn = get_connection()
    cursor = conn.cursor()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    placeholder = '?' if is_sqlite else '%s'
    cursor.execute(f'DELETE FROM users WHERE discord_id = {placeholder}', (str(discord_id),))
    conn.commit()
    conn.close()

def get_all_users():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT discord_id, riot_id, tagline FROM users')
    res = cursor.fetchall()
    conn.close()
    return res

def get_user(discord_id):
    conn = get_connection()
    cursor = conn.cursor()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    placeholder = '?' if is_sqlite else '%s'
    cursor.execute(f'SELECT riot_id, tagline FROM users WHERE discord_id = {placeholder}', (str(discord_id),))
    res = cursor.fetchone()
    conn.close()
    return res

def get_cached_stats(cache_key):
    conn = get_connection()
    cursor = conn.cursor()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    placeholder = '?' if is_sqlite else '%s'
    cursor.execute(f'SELECT last_updated, payload_json FROM cache_telemetry WHERE riot_id_tag = {placeholder}', (cache_key,))
    res = cursor.fetchone()
    conn.close()

    if res:
        payload = json.loads(res[1])
        ttl = payload.get('_ttl_minutes', 5)
        
        last_updated = datetime.fromisoformat(res[0])
        if datetime.now() - last_updated < timedelta(minutes=ttl):
            return payload
    return None

def set_cached_stats(cache_key, data, ttl_minutes=5):
    conn = get_connection()
    cursor = conn.cursor()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    if isinstance(data, dict):
        data['_ttl_minutes'] = ttl_minutes
        
    if is_sqlite:
        cursor.execute('''
            INSERT INTO cache_telemetry (riot_id_tag, last_updated, payload_json) VALUES (?, ?, ?)
            ON CONFLICT (riot_id_tag) DO UPDATE SET last_updated = excluded.last_updated, payload_json = excluded.payload_json
        ''', (cache_key, datetime.now().isoformat(), json.dumps(data)))
    else:
        cursor.execute('''
            INSERT INTO cache_telemetry (riot_id_tag, last_updated, payload_json) VALUES (%s, %s, %s)
            ON CONFLICT (riot_id_tag) DO UPDATE SET last_updated = EXCLUDED.last_updated, payload_json = EXCLUDED.payload_json
        ''', (cache_key, datetime.now().isoformat(), json.dumps(data)))
    conn.commit()
    conn.close()

def match_exists(match_id):
    conn = get_connection()
    cursor = conn.cursor()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    placeholder = '?' if is_sqlite else '%s'
    cursor.execute(f'SELECT 1 FROM matches WHERE match_id = {placeholder}', (match_id,))
    res = cursor.fetchone()
    conn.close()
    return res is not None

def save_match_data(match_id, metadata, players_stats):
    conn = get_connection()
    cursor = conn.cursor()
    is_sqlite = isinstance(conn, sqlite3.Connection)
    placeholder = '?' if is_sqlite else '%s'

    # Salva metadados da partida
    if is_sqlite:
        cursor.execute('''
            INSERT INTO matches (match_id, map, mode, match_start, rounds_played, data_json) 
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (match_id) DO NOTHING
        ''', (match_id, metadata['map'], metadata['mode'], metadata['game_start'], metadata['rounds_played'], json.dumps(metadata)))
    else:
        cursor.execute('''
            INSERT INTO matches (match_id, map, mode, match_start, rounds_played, data_json) 
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (match_id) DO NOTHING
        ''', (match_id, metadata['map'], metadata['mode'], metadata['game_start'], metadata['rounds_played'], json.dumps(metadata)))

    # Salva stats individuais
    for p in players_stats:
        if is_sqlite:
            cursor.execute('''
                INSERT INTO player_match_stats 
                (match_id, puuid, riot_id_tag, agent, kills, deaths, assists, damage_made, score, was_win)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (match_id, puuid) DO UPDATE SET 
                    riot_id_tag = excluded.riot_id_tag,
                    agent = excluded.agent,
                    kills = excluded.kills,
                    deaths = excluded.deaths,
                    assists = excluded.assists,
                    damage_made = excluded.damage_made,
                    score = excluded.score,
                    was_win = excluded.was_win
            ''', (match_id, p['puuid'], p['riot_id_tag'], p['agent'], p['kills'], p['deaths'], p['assists'], p['damage'], p['score'], p['was_win']))
        else:
            cursor.execute('''
                INSERT INTO player_match_stats 
                (match_id, puuid, riot_id_tag, agent, kills, deaths, assists, damage_made, score, was_win)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (match_id, puuid) DO UPDATE SET 
                    riot_id_tag = EXCLUDED.riot_id_tag,
                    agent = EXCLUDED.agent,
                    kills = EXCLUDED.kills,
                    deaths = EXCLUDED.deaths,
                    assists = EXCLUDED.assists,
                    damage_made = EXCLUDED.damage_made,
                    score = EXCLUDED.score,
                    was_win = EXCLUDED.was_win
            ''', (match_id, p['puuid'], p['riot_id_tag'], p['agent'], p['kills'], p['deaths'], p['assists'], p['damage'], p['score'], p['was_win']))

    conn.commit()
    conn.close()
h
