import sqlite3
import json
import os
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(BASE_DIR, 'data', 'bot_database.db')

def init_db():
    data_dir = os.path.dirname(DB_FILE)
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Tabela de Usuários Monitorados
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        riot_id TEXT,
        tagline TEXT,
        discord_id TEXT,
        puuid TEXT,
        region TEXT DEFAULT 'latam',
        PRIMARY KEY (riot_id, tagline)
    )''')

    # Cache temporário para evitar 429 (TTL 5-10min)
    cursor.execute('''CREATE TABLE IF NOT EXISTS cache_telemetry (
        riot_id_tag TEXT PRIMARY KEY,
        last_updated TEXT,
        payload_json TEXT NOT NULL
    )''')

    # Histórico persistente de partidas para análise Data-Driven
    cursor.execute('''CREATE TABLE IF NOT EXISTS matches (
        match_id TEXT PRIMARY KEY,
        map TEXT,
        mode TEXT,
        match_start INTEGER,
        rounds_played INTEGER,
        data_json TEXT NOT NULL
    )''')

    # Estatísticas individuais por partida (facilitando queries de ADR, KAST)
    cursor.execute('''CREATE TABLE IF NOT EXISTS player_match_stats (
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

    # Configurações do bot (ex: canal de notificações)
    cursor.execute('''CREATE TABLE IF NOT EXISTS bot_config (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')

    conn.commit()
    conn.close()

def set_config(key, value):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)', (key, str(value)))
    conn.commit()
    conn.close()

def get_config(key):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM bot_config WHERE key = ?', (key,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else None

def save_user(discord_id, riot_id, tagline):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO users (discord_id, riot_id, tagline) VALUES (?, ?, ?)', (str(discord_id), riot_id, tagline))
    conn.commit()
    conn.close()

def remove_user(discord_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM users WHERE discord_id = ?', (str(discord_id),))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT discord_id, riot_id, tagline FROM users')
    res = cursor.fetchall()
    conn.close()
    return res

def get_user(discord_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT riot_id, tagline FROM users WHERE discord_id = ?', (str(discord_id),))
    res = cursor.fetchone()
    conn.close()
    return res

def get_cached_stats(cache_key):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT last_updated, payload_json FROM cache_telemetry WHERE riot_id_tag = ?', (cache_key,))
    res = cursor.fetchone()
    conn.close()

    if res:
        # Recupera o TTL do payload se estiver lá, senão usa 5 min default
        payload = json.loads(res[1])
        ttl = payload.get('_ttl_minutes', 5)
        
        last_updated = datetime.fromisoformat(res[0])
        if datetime.now() - last_updated < timedelta(minutes=ttl):
            return payload
    return None

def set_cached_stats(cache_key, data, ttl_minutes=5):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Adiciona o TTL dentro do próprio JSON para facilitar
    if isinstance(data, dict):
        data['_ttl_minutes'] = ttl_minutes
        
    cursor.execute('INSERT OR REPLACE INTO cache_telemetry (riot_id_tag, last_updated, payload_json) VALUES (?, ?, ?)', 
                   (cache_key, datetime.now().isoformat(), json.dumps(data)))
    conn.commit()
    conn.close()

def match_exists(match_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM matches WHERE match_id = ?', (match_id,))
    res = cursor.fetchone()
    conn.close()
    return res is not None

def save_match_data(match_id, metadata, players_stats):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Salva metadados da partida
    cursor.execute('''INSERT OR IGNORE INTO matches (match_id, map, mode, match_start, rounds_played, data_json) 
                      VALUES (?, ?, ?, ?, ?, ?)''', 
                   (match_id, metadata['map'], metadata['mode'], metadata['game_start'], metadata['rounds_played'], json.dumps(metadata)))

    # Salva stats individuais
    for p in players_stats:
        cursor.execute('''INSERT OR REPLACE INTO player_match_stats 
                          (match_id, puuid, riot_id_tag, agent, kills, deaths, assists, damage_made, score, was_win)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                       (match_id, p['puuid'], p['riot_id_tag'], p['agent'], p['kills'], p['deaths'], p['assists'], p['damage'], p['score'], p['was_win']))

    conn.commit()
    conn.close()

