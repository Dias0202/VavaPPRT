import sqlite3
import json
import os
from datetime import datetime, timedelta

DB_FILE = 'data/bot_database.db'

def init_db():
    if not os.path.exists('data'):
        os.makedirs('data')
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        discord_id TEXT PRIMARY KEY,
        riot_id TEXT NOT NULL,
        tagline TEXT NOT NULL,
        region TEXT DEFAULT 'latam'
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS cache_telemetry (
        riot_id_tag TEXT PRIMARY KEY,
        last_updated TEXT,
        payload_json TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()

def save_user(discord_id, riot_id, tagline):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO users (discord_id, riot_id, tagline) VALUES (?, ?, ?)', (str(discord_id), riot_id, tagline))
    conn.commit()
    conn.close()

def get_user(discord_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT riot_id, tagline FROM users WHERE discord_id = ?', (str(discord_id),))
    res = cursor.fetchone()
    conn.close()
    return res

def get_cached_stats(riot_id_tag):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT last_updated, payload_json FROM cache_telemetry WHERE riot_id_tag = ?', (riot_id_tag,))
    res = cursor.fetchone()
    conn.close()
    
    if res:
        last_updated = datetime.fromisoformat(res[0])
        if datetime.now() - last_updated < timedelta(minutes=10):
            return json.loads(res[1])
    return None

def set_cached_stats(riot_id_tag, data):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO cache_telemetry (riot_id_tag, last_updated, payload_json) VALUES (?, ?, ?)', 
                   (riot_id_tag, datetime.now().isoformat(), json.dumps(data)))
    conn.commit()
    conn.close()
