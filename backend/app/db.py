"""Camada de acesso a dados — schema de analytics.

Postgres (Supabase, produção) ou SQLite (dev/fallback). Tudo é chaveado por
`puuid` (estável); nomes são só rótulo. Partidas guardam um raw comprimido
(gzip) para recomputar métricas novas no futuro, mais tabelas normalizadas para
query rápida e uma tabela de métricas derivadas por jogador/partida (o agregado
é só um SUM em cima dela).
"""
import os
import json
import gzip
import sqlite3
from datetime import datetime, timedelta

from . import config

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None


# ---------------------------------------------------------------------------
# Conexão
# ---------------------------------------------------------------------------
def get_connection():
    url = config.DATABASE_URL
    if not url:
        db_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "app.db"
        )
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    if psycopg2 is None:
        raise RuntimeError("psycopg2 ausente mas DATABASE_URL definida.")
    return psycopg2.connect(url)


def _is_sqlite(conn):
    return isinstance(conn, sqlite3.Connection)


def _ph(conn):
    return "?" if _is_sqlite(conn) else "%s"


def _dict_cursor(conn):
    if _is_sqlite(conn):
        return conn.cursor()
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _d(row):
    return dict(row) if row is not None else None


def _blob(conn, data: bytes):
    """Adapta bytes para o driver (bytea no Postgres)."""
    if _is_sqlite(conn):
        return data
    return psycopg2.Binary(data)


def _upsert(conn, table, cols, conflict_cols, row, update=False):
    """INSERT ... ON CONFLICT (idempotente). update=False => DO NOTHING."""
    ph = _ph(conn)
    placeholders = ", ".join([ph] * len(cols))
    collist = ", ".join(cols)
    conflict = ", ".join(conflict_cols)
    if update:
        sets = ", ".join(
            f"{c} = excluded.{c}" if _is_sqlite(conn) else f"{c} = EXCLUDED.{c}"
            for c in cols if c not in conflict_cols
        )
        action = f"DO UPDATE SET {sets}" if sets else "DO NOTHING"
    else:
        action = "DO NOTHING"
    sql = (
        f"INSERT INTO {table} ({collist}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) {action}"
    )
    conn.cursor().execute(sql, row)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def init_db():
    conn = get_connection()
    try:
        cur = conn.cursor()
        is_sqlite = _is_sqlite(conn)
        serial_pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if is_sqlite else "SERIAL PRIMARY KEY"
        ts_default = "TEXT" if is_sqlite else "TIMESTAMP DEFAULT NOW()"
        blob_type = "BLOB" if is_sqlite else "BYTEA"

        cur.execute(f"""CREATE TABLE IF NOT EXISTS users (
            id {serial_pk},
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            riot_id TEXT NOT NULL,
            tagline TEXT NOT NULL,
            region TEXT DEFAULT 'latam',
            puuid TEXT,
            consent INTEGER DEFAULT 1,
            is_admin INTEGER DEFAULT 0,
            created_at {ts_default},
            UNIQUE (riot_id, tagline)
        )""")

        cur.execute("""CREATE TABLE IF NOT EXISTS app_config (
            key TEXT PRIMARY KEY, value TEXT
        )""")

        # Pessoas rastreadas SEM login (adicionadas por um usuário logado).
        cur.execute(f"""CREATE TABLE IF NOT EXISTS tracked_players (
            id {serial_pk},
            puuid TEXT UNIQUE,
            riot_id TEXT NOT NULL,
            tagline TEXT NOT NULL,
            region TEXT DEFAULT 'latam',
            consent INTEGER DEFAULT 1,
            added_by INTEGER,
            created_at {ts_default},
            UNIQUE (riot_id, tagline)
        )""")

        cur.execute("""CREATE TABLE IF NOT EXISTS cache_telemetry (
            cache_key TEXT PRIMARY KEY, last_updated TEXT NOT NULL, payload_json TEXT NOT NULL
        )""")

        # Partidas: metadados + raw comprimido para recompute futuro.
        cur.execute(f"""CREATE TABLE IF NOT EXISTS matches (
            match_id TEXT PRIMARY KEY,
            map TEXT, mode TEXT, queue TEXT, season TEXT,
            started_at TEXT, started_raw BIGINT,
            rounds INTEGER, region TEXT,
            raw_gz {blob_type}
        )""")

        # Resumo por jogador (TODOS os 10) — base de sinergia e contexto de rank.
        cur.execute("""CREATE TABLE IF NOT EXISTS match_players (
            match_id TEXT, puuid TEXT, name TEXT, tag TEXT, team TEXT,
            agent TEXT, won INTEGER, score INTEGER,
            kills INTEGER, deaths INTEGER, assists INTEGER,
            headshots INTEGER, bodyshots INTEGER, legshots INTEGER,
            damage_made INTEGER, damage_received INTEGER,
            loadout_avg REAL, party_id TEXT, tier INTEGER, tier_name TEXT,
            PRIMARY KEY (match_id, puuid)
        )""")

        # Métricas profundas por jogador AMIGO por partida (o agregado é SUM disto).
        cur.execute("""CREATE TABLE IF NOT EXISTS player_match_derived (
            match_id TEXT, puuid TEXT,
            map TEXT, agent TEXT, won INTEGER, rounds INTEGER,
            kills INTEGER, deaths INTEGER, assists INTEGER, score INTEGER,
            damage INTEGER, headshots INTEGER, bodyshots INTEGER, legshots INTEGER,
            first_bloods INTEGER, first_deaths INTEGER,
            trade_kills INTEGER, deaths_traded INTEGER,
            kast_rounds INTEGER,
            mk2 INTEGER, mk3 INTEGER, mk4 INTEGER, mk5 INTEGER,
            atk_rounds INTEGER, atk_wins INTEGER, def_rounds INTEGER, def_wins INTEGER,
            eco_rounds INTEGER, eco_wins INTEGER,
            force_rounds INTEGER, force_wins INTEGER,
            full_rounds INTEGER, full_wins INTEGER,
            pistol_rounds INTEGER, pistol_wins INTEGER,
            clutches_json TEXT,
            full_data INTEGER DEFAULT 1,
            PRIMARY KEY (match_id, puuid)
        )""")

        # Rounds por jogador AMIGO (detalhe/depuração).
        cur.execute("""CREATE TABLE IF NOT EXISTS player_round_stats (
            match_id TEXT, round_id INTEGER, puuid TEXT,
            kills INTEGER, damage INTEGER, headshots INTEGER,
            loadout_value INTEGER, buy_type TEXT, weapon TEXT,
            alive_end INTEGER, was_traded INTEGER, attacker_side INTEGER,
            PRIMARY KEY (match_id, round_id, puuid)
        )""")

        # Kills (todos) — heatmap + recompute.
        cur.execute("""CREATE TABLE IF NOT EXISTS kills (
            match_id TEXT, round_id INTEGER, order_in_round INTEGER,
            time_ms INTEGER, killer_puuid TEXT, victim_puuid TEXT, weapon TEXT,
            is_first INTEGER, is_trade INTEGER, vx INTEGER, vy INTEGER,
            PRIMARY KEY (match_id, round_id, order_in_round)
        )""")

        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_mp_puuid ON match_players(puuid)",
            "CREATE INDEX IF NOT EXISTS idx_pmd_puuid ON player_match_derived(puuid)",
            "CREATE INDEX IF NOT EXISTS idx_kills_killer ON kills(killer_puuid)",
            "CREATE INDEX IF NOT EXISTS idx_kills_victim ON kills(victim_puuid)",
            "CREATE INDEX IF NOT EXISTS idx_prs_puuid ON player_round_stats(puuid)",
        ):
            cur.execute(stmt)

        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def set_config(key, value):
    conn = get_connection()
    try:
        _upsert(conn, "app_config", ["key", "value"], ["key"], (key, str(value)), update=True)
        conn.commit()
    finally:
        conn.close()


def get_config(key):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT value FROM app_config WHERE key = {_ph(conn)}", (key,))
        r = cur.fetchone()
        return r[0] if r else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Usuários
# ---------------------------------------------------------------------------
def create_user(username, password_hash, riot_id, tagline, region="latam", puuid=None, is_admin=0):
    conn = get_connection()
    try:
        ph = _ph(conn)
        conn.cursor().execute(
            f"INSERT INTO users (username, password_hash, riot_id, tagline, region, puuid, is_admin) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (username, password_hash, riot_id, tagline, region, puuid, is_admin),
        )
        conn.commit()
    finally:
        conn.close()


def _get_user(where, val):
    conn = get_connection()
    try:
        cur = _dict_cursor(conn)
        cur.execute(f"SELECT * FROM users WHERE {where} = {_ph(conn)}", (val,))
        return _d(cur.fetchone())
    finally:
        conn.close()


def get_user_by_username(username):
    return _get_user("username", username)


def get_user_by_id(uid):
    return _get_user("id", uid)


def get_user_by_puuid(puuid):
    return _get_user("puuid", puuid)


def get_user_by_riot(riot_id, tagline):
    conn = get_connection()
    try:
        cur = _dict_cursor(conn)
        ph = _ph(conn)
        cur.execute(
            f"SELECT * FROM users WHERE LOWER(riot_id)=LOWER({ph}) AND LOWER(tagline)=LOWER({ph})",
            (riot_id, tagline),
        )
        return _d(cur.fetchone())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Roster (login users + tracked players) — unificado por puuid
# ---------------------------------------------------------------------------
def add_tracked_player(riot_id, tagline, region, puuid, added_by=None):
    conn = get_connection()
    try:
        ph = _ph(conn)
        conn.cursor().execute(
            f"INSERT INTO tracked_players (puuid, riot_id, tagline, region, added_by) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph})",
            (puuid, riot_id, tagline, region, added_by),
        )
        conn.commit()
    finally:
        conn.close()


def puuid_in_roster(puuid):
    conn = get_connection()
    try:
        cur = conn.cursor()
        ph = _ph(conn)
        cur.execute(f"SELECT 1 FROM users WHERE puuid={ph} "
                    f"UNION SELECT 1 FROM tracked_players WHERE puuid={ph}", (puuid, puuid))
        return cur.fetchone() is not None
    finally:
        conn.close()


def get_roster():
    """Lista unificada (formato 'user') de quem é rastreado. Dedupe por puuid,
    login prevalece sobre tracked. Campos: username(label), riot_id, tagline,
    region, puuid, consent."""
    out, seen = [], set()
    for u in get_all_users():
        if u.get("puuid"):
            out.append({"username": u["username"], "riot_id": u["riot_id"],
                        "tagline": u["tagline"], "region": u.get("region") or "latam",
                        "puuid": u["puuid"], "consent": u.get("consent", 1), "kind": "user"})
            seen.add(u["puuid"])
    for t in _query("SELECT * FROM tracked_players ORDER BY riot_id"):
        if t["puuid"] and t["puuid"] not in seen:
            out.append({"username": f"{t['riot_id']}", "riot_id": t["riot_id"],
                        "tagline": t["tagline"], "region": t.get("region") or "latam",
                        "puuid": t["puuid"], "consent": t.get("consent", 1), "kind": "tracked"})
            seen.add(t["puuid"])
    return out


def get_roster_member_by_riot(riot_id, tagline):
    for m in get_roster():
        if m["riot_id"].lower() == riot_id.lower() and m["tagline"].lower() == tagline.lower():
            return m
    return None


def username_exists(username):
    return get_user_by_username(username) is not None


def riot_id_exists(riot_id, tagline):
    conn = get_connection()
    try:
        cur = conn.cursor()
        ph = _ph(conn)
        cur.execute(
            f"SELECT 1 FROM users WHERE LOWER(riot_id)=LOWER({ph}) AND LOWER(tagline)=LOWER({ph})",
            (riot_id, tagline),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def get_all_users():
    conn = get_connection()
    try:
        cur = _dict_cursor(conn)
        cur.execute("SELECT id, username, riot_id, tagline, region, puuid, consent FROM users ORDER BY username")
        return [_d(r) for r in cur.fetchall()]
    finally:
        conn.close()


def consenting_puuids():
    """puuids dos usuários que consentiram (para limitar a análise exibida)."""
    return {u["puuid"] for u in get_all_users() if u.get("puuid") and u.get("consent")}


def update_user_puuid(riot_id, tagline, puuid):
    conn = get_connection()
    try:
        ph = _ph(conn)
        conn.cursor().execute(
            f"UPDATE users SET puuid={ph} WHERE LOWER(riot_id)=LOWER({ph}) AND LOWER(tagline)=LOWER({ph})",
            (puuid, riot_id, tagline),
        )
        conn.commit()
    finally:
        conn.close()


def delete_user(uid):
    conn = get_connection()
    try:
        conn.cursor().execute(f"DELETE FROM users WHERE id = {_ph(conn)}", (uid,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Partidas — escrita
# ---------------------------------------------------------------------------
def match_exists(match_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM matches WHERE match_id = {_ph(conn)}", (match_id,))
        return cur.fetchone() is not None
    finally:
        conn.close()


def compress_raw(match_dict_stripped):
    return gzip.compress(json.dumps(match_dict_stripped, ensure_ascii=False).encode("utf-8"))


def save_match_bundle(meta, raw_gz, match_players, derived, round_rows, kill_rows):
    """Grava uma partida inteira de forma idempotente."""
    conn = get_connection()
    try:
        _upsert(conn, "matches",
                ["match_id", "map", "mode", "queue", "season", "started_at", "started_raw", "rounds", "region", "raw_gz"],
                ["match_id"],
                (meta["match_id"], meta["map"], meta["mode"], meta["queue"], meta["season"],
                 meta["started_at"], meta.get("started_raw"), meta["rounds"], meta["region"],
                 _blob(conn, raw_gz)))

        mp_cols = ["match_id", "puuid", "name", "tag", "team", "agent", "won", "score",
                   "kills", "deaths", "assists", "headshots", "bodyshots", "legshots",
                   "damage_made", "damage_received", "loadout_avg", "party_id", "tier", "tier_name"]
        for r in match_players:
            _upsert(conn, "match_players", mp_cols, ["match_id", "puuid"],
                    tuple(r[c] for c in mp_cols))

        d_cols = ["match_id", "puuid", "map", "agent", "won", "rounds", "kills", "deaths",
                  "assists", "score", "damage", "headshots", "bodyshots", "legshots", "first_bloods",
                  "first_deaths", "trade_kills", "deaths_traded", "kast_rounds",
                  "mk2", "mk3", "mk4", "mk5", "atk_rounds", "atk_wins", "def_rounds", "def_wins",
                  "eco_rounds", "eco_wins", "force_rounds", "force_wins", "full_rounds", "full_wins",
                  "pistol_rounds", "pistol_wins", "clutches_json", "full_data"]
        for r in derived:
            _upsert(conn, "player_match_derived", d_cols, ["match_id", "puuid"],
                    tuple(r[c] for c in d_cols))

        rs_cols = ["match_id", "round_id", "puuid", "kills", "damage", "headshots",
                   "loadout_value", "buy_type", "weapon", "alive_end", "was_traded", "attacker_side"]
        for r in round_rows:
            _upsert(conn, "player_round_stats", rs_cols, ["match_id", "round_id", "puuid"],
                    tuple(r[c] for c in rs_cols))

        k_cols = ["match_id", "round_id", "order_in_round", "time_ms", "killer_puuid",
                  "victim_puuid", "weapon", "is_first", "is_trade", "vx", "vy"]
        for r in kill_rows:
            _upsert(conn, "kills", k_cols, ["match_id", "round_id", "order_in_round"],
                    tuple(r[c] for c in k_cols))

        conn.commit()
    finally:
        conn.close()


def save_summary_match(meta, mp_row, derived_row):
    """Grava uma partida só-resumo (backfill via lifetime). Não sobrescreve
    partidas completas já existentes."""
    conn = get_connection()
    try:
        _upsert(conn, "matches",
                ["match_id", "map", "mode", "queue", "season", "started_at", "started_raw", "rounds", "region", "raw_gz"],
                ["match_id"],
                (meta["match_id"], meta["map"], meta["mode"], meta.get("queue"), meta.get("season"),
                 meta["started_at"], meta.get("started_raw"), meta["rounds"], meta["region"], None))
        mp_cols = ["match_id", "puuid", "name", "tag", "team", "agent", "won", "score",
                   "kills", "deaths", "assists", "headshots", "bodyshots", "legshots",
                   "damage_made", "damage_received", "loadout_avg", "party_id", "tier", "tier_name"]
        _upsert(conn, "match_players", mp_cols, ["match_id", "puuid"], tuple(mp_row[c] for c in mp_cols))
        d_cols = ["match_id", "puuid", "map", "agent", "won", "rounds", "kills", "deaths",
                  "assists", "score", "damage", "headshots", "bodyshots", "legshots", "first_bloods",
                  "first_deaths", "trade_kills", "deaths_traded", "kast_rounds",
                  "mk2", "mk3", "mk4", "mk5", "atk_rounds", "atk_wins", "def_rounds", "def_wins",
                  "eco_rounds", "eco_wins", "force_rounds", "force_wins", "full_rounds", "full_wins",
                  "pistol_rounds", "pistol_wins", "clutches_json", "full_data"]
        _upsert(conn, "player_match_derived", d_cols, ["match_id", "puuid"],
                tuple(derived_row[c] for c in d_cols))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Partidas — leitura (analytics)
# ---------------------------------------------------------------------------
def _query(sql, params=()):
    conn = get_connection()
    try:
        cur = _dict_cursor(conn)
        sql2 = sql if _is_sqlite(conn) else sql.replace("?", "%s")
        cur.execute(sql2, params)
        return [_d(r) for r in cur.fetchall()]
    finally:
        conn.close()


def derived_for_puuid(puuid):
    return _query(
        "SELECT d.*, m.started_raw FROM player_match_derived d "
        "JOIN matches m ON m.match_id = d.match_id WHERE d.puuid = ? "
        "ORDER BY m.started_raw DESC",
        (puuid,),
    )


def match_players_for_puuid(puuid):
    return _query("SELECT mp.*, m.started_raw, m.map, m.mode FROM match_players mp "
                  "JOIN matches m ON m.match_id = mp.match_id WHERE mp.puuid = ? "
                  "ORDER BY m.started_raw DESC", (puuid,))


def all_match_players_for_puuids(puuids):
    """match_players (+ mapa) de todas as partidas onde QUALQUER amigo jogou."""
    if not puuids:
        return []
    marks = ",".join("?" for _ in puuids)
    return _query(
        f"SELECT mp.*, m.map AS map, m.started_raw AS started_raw FROM match_players mp "
        f"JOIN matches m ON m.match_id = mp.match_id WHERE mp.match_id IN "
        f"(SELECT DISTINCT match_id FROM match_players WHERE puuid IN ({marks}))",
        tuple(puuids),
    )


def kills_for_puuid(puuid):
    return _query("SELECT * FROM kills WHERE killer_puuid = ? OR victim_puuid = ?", (puuid, puuid))


def round_rows_for_puuid(puuid):
    return _query("SELECT * FROM player_round_stats WHERE puuid = ?", (puuid,))


def recent_matches_for_puuid(puuid, limit=20):
    return _query("SELECT d.*, m.map, m.mode, m.started_at, m.started_raw "
                  "FROM player_match_derived d JOIN matches m ON m.match_id = d.match_id "
                  "WHERE d.puuid = ? ORDER BY m.started_raw DESC LIMIT ?", (puuid, limit))


# ---------------------------------------------------------------------------
# Cache (TTL) — reuso para chamadas pontuais
# ---------------------------------------------------------------------------
def get_cached(cache_key, ttl_minutes=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT last_updated, payload_json FROM cache_telemetry WHERE cache_key = {_ph(conn)}", (cache_key,))
        r = cur.fetchone()
        if not r:
            return None
        ttl = ttl_minutes if ttl_minutes is not None else config.CACHE_TTL_MINUTES
        if datetime.utcnow() - datetime.fromisoformat(r[0]) < timedelta(minutes=ttl):
            return json.loads(r[1])
        return None
    finally:
        conn.close()


def set_cached(cache_key, data):
    conn = get_connection()
    try:
        _upsert(conn, "cache_telemetry", ["cache_key", "last_updated", "payload_json"], ["cache_key"],
                (cache_key, datetime.utcnow().isoformat(), json.dumps(data)), update=True)
        conn.commit()
    finally:
        conn.close()
