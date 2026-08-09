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
            bodyshots INTEGER, legshots INTEGER,
            loadout_value INTEGER, buy_type TEXT, weapon TEXT,
            alive_end INTEGER, was_traded INTEGER, attacker_side INTEGER,
            PRIMARY KEY (match_id, round_id, puuid)
        )""")

        # Trajetória de rank/RR por partida (fonte: /v1/mmr-history).
        cur.execute("""CREATE TABLE IF NOT EXISTS rank_history (
            puuid TEXT, match_id TEXT,
            date_raw BIGINT, tier INTEGER, tier_name TEXT,
            rr INTEGER, rr_change INTEGER, elo INTEGER, map TEXT,
            PRIMARY KEY (puuid, match_id)
        )""")

        # Metas de treino: fecham o loop do motor de insights.
        cur.execute(f"""CREATE TABLE IF NOT EXISTS player_goals (
            id {serial_pk},
            puuid TEXT NOT NULL,
            metric TEXT NOT NULL,
            label TEXT,
            baseline REAL,
            target REAL,
            direction TEXT DEFAULT 'up',
            status TEXT DEFAULT 'active',
            created_raw BIGINT,
            achieved_raw BIGINT
        )""")

        # Kills (todos) — heatmap + recompute.
        cur.execute("""CREATE TABLE IF NOT EXISTS kills (
            match_id TEXT, round_id INTEGER, order_in_round INTEGER,
            time_ms INTEGER, killer_puuid TEXT, victim_puuid TEXT, weapon TEXT,
            is_first INTEGER, is_trade INTEGER, vx INTEGER, vy INTEGER,
            PRIMARY KEY (match_id, round_id, order_in_round)
        )""")

        # Positional snapshots at key events (kill frames, plant/defuse).
        cur.execute("""CREATE TABLE IF NOT EXISTS spatial_events (
            match_id TEXT, round_id INTEGER, puuid TEXT, event_type TEXT,
            time_ms INTEGER, x REAL, y REAL, z REAL,
            outcome TEXT,
            PRIMARY KEY (match_id, round_id, puuid, event_type, time_ms)
        )""")

        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_mp_puuid ON match_players(puuid)",
            "CREATE INDEX IF NOT EXISTS idx_pmd_puuid ON player_match_derived(puuid)",
            "CREATE INDEX IF NOT EXISTS idx_kills_killer ON kills(killer_puuid)",
            "CREATE INDEX IF NOT EXISTS idx_kills_victim ON kills(victim_puuid)",
            "CREATE INDEX IF NOT EXISTS idx_prs_puuid ON player_round_stats(puuid)",
            "CREATE INDEX IF NOT EXISTS idx_se_puuid ON spatial_events(puuid)",
            "CREATE INDEX IF NOT EXISTS idx_rh_puuid ON rank_history(puuid)",
            "CREATE INDEX IF NOT EXISTS idx_goals_puuid ON player_goals(puuid)",
        ):
            cur.execute(stmt)

        _ensure_columns(conn, cur)
        conn.commit()
    finally:
        conn.close()


# Colunas acrescentadas depois da v1 do schema. Bancos já existentes (o seu
# Supabase, o SQLite de dev) não são recriados — precisam de ALTER.
_ADDED_COLUMNS = {
    "player_match_derived": [
        ("plants", "INTEGER DEFAULT 0"), ("defuses", "INTEGER DEFAULT 0"),
        ("postplant_rounds", "INTEGER DEFAULT 0"), ("postplant_wins", "INTEGER DEFAULT 0"),
        ("retake_rounds", "INTEGER DEFAULT 0"), ("retake_wins", "INTEGER DEFAULT 0"),
        ("sites_json", "TEXT"),
        ("cast_grenade", "INTEGER DEFAULT 0"), ("cast_ability1", "INTEGER DEFAULT 0"),
        ("cast_ability2", "INTEGER DEFAULT 0"), ("cast_ultimate", "INTEGER DEFAULT 0"),
    ],
    "player_round_stats": [
        ("bodyshots", "INTEGER DEFAULT 0"), ("legshots", "INTEGER DEFAULT 0"),
    ],
}


def _existing_columns(conn, cur, table):
    if _is_sqlite(conn):
        cur.execute(f"PRAGMA table_info({table})")
        return {r[1] for r in cur.fetchall()}
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
    return {r[0] for r in cur.fetchall()}


def _ensure_columns(conn, cur):
    """Acrescenta colunas novas em tabelas que já existem (idempotente)."""
    for table, cols in _ADDED_COLUMNS.items():
        have = _existing_columns(conn, cur, table)
        for name, decl in cols:
            if name not in have:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


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
# Colunas de player_match_derived, em uma única definição (escrita completa e
# de resumo usam a mesma lista; `.get()` cobre o que o resumo não tem).
DERIVED_COLS = [
    "match_id", "puuid", "map", "agent", "won", "rounds", "kills", "deaths",
    "assists", "score", "damage", "headshots", "bodyshots", "legshots", "first_bloods",
    "first_deaths", "trade_kills", "deaths_traded", "kast_rounds",
    "mk2", "mk3", "mk4", "mk5", "atk_rounds", "atk_wins", "def_rounds", "def_wins",
    "eco_rounds", "eco_wins", "force_rounds", "force_wins", "full_rounds", "full_wins",
    "pistol_rounds", "pistol_wins", "clutches_json", "full_data",
    "plants", "defuses", "postplant_rounds", "postplant_wins",
    "retake_rounds", "retake_wins", "sites_json",
    "cast_grenade", "cast_ability1", "cast_ability2", "cast_ultimate",
]


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


def save_match_bundle(meta, raw_gz, match_players, derived, round_rows, kill_rows,
                      spatial_rows=None, upgrade=False):
    """Grava uma partida inteira de forma idempotente.

    upgrade=True sobrescreve linhas já existentes: é o que o backfill profundo
    usa para promover uma partida de resumo (full_data=0) a completa. Sem isso o
    ON CONFLICT DO NOTHING descartaria silenciosamente a re-hidratação.
    """
    conn = get_connection()
    try:
        _upsert(conn, "matches",
                ["match_id", "map", "mode", "queue", "season", "started_at", "started_raw", "rounds", "region", "raw_gz"],
                ["match_id"],
                (meta["match_id"], meta["map"], meta["mode"], meta["queue"], meta["season"],
                 meta["started_at"], meta.get("started_raw"), meta["rounds"], meta["region"],
                 _blob(conn, raw_gz)), update=upgrade)

        mp_cols = ["match_id", "puuid", "name", "tag", "team", "agent", "won", "score",
                   "kills", "deaths", "assists", "headshots", "bodyshots", "legshots",
                   "damage_made", "damage_received", "loadout_avg", "party_id", "tier", "tier_name"]
        for r in match_players:
            _upsert(conn, "match_players", mp_cols, ["match_id", "puuid"],
                    tuple(r[c] for c in mp_cols), update=upgrade)

        for r in derived:
            _upsert(conn, "player_match_derived", DERIVED_COLS, ["match_id", "puuid"],
                    tuple(r.get(c) for c in DERIVED_COLS), update=upgrade)

        rs_cols = ["match_id", "round_id", "puuid", "kills", "damage", "headshots",
                   "bodyshots", "legshots",
                   "loadout_value", "buy_type", "weapon", "alive_end", "was_traded", "attacker_side"]
        for r in round_rows:
            _upsert(conn, "player_round_stats", rs_cols, ["match_id", "round_id", "puuid"],
                    tuple(r.get(c) for c in rs_cols), update=upgrade)

        k_cols = ["match_id", "round_id", "order_in_round", "time_ms", "killer_puuid",
                  "victim_puuid", "weapon", "is_first", "is_trade", "vx", "vy"]
        for r in kill_rows:
            _upsert(conn, "kills", k_cols, ["match_id", "round_id", "order_in_round"],
                    tuple(r[c] for c in k_cols))

        if spatial_rows:
            se_cols = ["match_id", "round_id", "puuid", "event_type", "time_ms", "x", "y", "z", "outcome"]
            for r in spatial_rows:
                _upsert(conn, "spatial_events", se_cols,
                        ["match_id", "round_id", "puuid", "event_type", "time_ms"],
                        tuple(r.get(c) for c in se_cols))

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
        _upsert(conn, "player_match_derived", DERIVED_COLS, ["match_id", "puuid"],
                tuple(derived_row.get(c) for c in DERIVED_COLS))
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


# ---------------------------------------------------------------------------
# Spatial events
# ---------------------------------------------------------------------------
def spatial_events_for_puuid(puuid):
    return _query("SELECT * FROM spatial_events WHERE puuid = ?", (puuid,))


# ---------------------------------------------------------------------------
# Raw match recomputation helpers
# ---------------------------------------------------------------------------
def get_raw_match(match_id):
    """Returns decompressed match dict, or None if no raw stored."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT raw_gz FROM matches WHERE match_id = {_ph(conn)}", (match_id,))
        r = cur.fetchone()
        if not r or not r[0]:
            return None
        return json.loads(gzip.decompress(bytes(r[0])))
    finally:
        conn.close()


def match_ids_with_raw():
    """Toda partida que guardou raw — base do recompute offline."""
    return [r["match_id"] for r in _query(
        "SELECT match_id FROM matches WHERE raw_gz IS NOT NULL ORDER BY started_raw DESC")]


def recent_match_ids(limit=5):
    return [r["match_id"] for r in _query(
        "SELECT match_id FROM matches WHERE raw_gz IS NOT NULL ORDER BY started_raw DESC LIMIT ?",
        (limit,),
    )]


def derived_for_puuid_match(match_id):
    return _query("SELECT * FROM player_match_derived WHERE match_id = ?", (match_id,))


# ---------------------------------------------------------------------------
# Backfill profundo — partidas de resumo que dá para re-hidratar
# ---------------------------------------------------------------------------
def summary_match_ids_for_puuid(puuid, limit=200):
    """Partidas do jogador que só têm resumo (sem rounds/kills), mais novas
    primeiro. São as candidatas a re-hidratação via /v4/match/{id}."""
    return [r["match_id"] for r in _query(
        "SELECT d.match_id FROM player_match_derived d "
        "JOIN matches m ON m.match_id = d.match_id "
        "WHERE d.puuid = ? AND (d.full_data = 0 OR d.full_data IS NULL) "
        "ORDER BY m.started_raw DESC LIMIT ?",
        (puuid, limit),
    )]


def deep_coverage(puuid):
    """(partidas completas, total) — quanto das métricas profundas tem lastro."""
    rows = _query(
        "SELECT full_data, COUNT(*) AS n FROM player_match_derived "
        "WHERE puuid = ? GROUP BY full_data", (puuid,))
    full = sum(r["n"] for r in rows if r["full_data"])
    return full, sum(r["n"] for r in rows)


# ---------------------------------------------------------------------------
# Rank / RR
# ---------------------------------------------------------------------------
RANK_COLS = ["puuid", "match_id", "date_raw", "tier", "tier_name", "rr",
             "rr_change", "elo", "map"]


def save_rank_history(rows):
    if not rows:
        return 0
    conn = get_connection()
    try:
        for r in rows:
            _upsert(conn, "rank_history", RANK_COLS, ["puuid", "match_id"],
                    tuple(r.get(c) for c in RANK_COLS), update=True)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def rank_history_for_puuid(puuid, limit=200):
    return _query("SELECT * FROM rank_history WHERE puuid = ? "
                  "ORDER BY date_raw DESC LIMIT ?", (puuid, limit))


# ---------------------------------------------------------------------------
# Metas de treino
# ---------------------------------------------------------------------------
GOAL_COLS = ["puuid", "metric", "label", "baseline", "target", "direction",
             "status", "created_raw", "achieved_raw"]


def create_goal(row):
    conn = get_connection()
    try:
        ph = _ph(conn)
        cols = ", ".join(GOAL_COLS)
        conn.cursor().execute(
            f"INSERT INTO player_goals ({cols}) VALUES ({', '.join([ph] * len(GOAL_COLS))})",
            tuple(row.get(c) for c in GOAL_COLS))
        conn.commit()
    finally:
        conn.close()


def goals_for_puuid(puuid):
    return _query("SELECT * FROM player_goals WHERE puuid = ? ORDER BY id DESC", (puuid,))


def update_goal_status(goal_id, status, achieved_raw=None):
    conn = get_connection()
    try:
        ph = _ph(conn)
        conn.cursor().execute(
            f"UPDATE player_goals SET status={ph}, achieved_raw={ph} WHERE id={ph}",
            (status, achieved_raw, goal_id))
        conn.commit()
    finally:
        conn.close()


def delete_goal(goal_id, puuid):
    conn = get_connection()
    try:
        ph = _ph(conn)
        conn.cursor().execute(
            f"DELETE FROM player_goals WHERE id={ph} AND puuid={ph}", (goal_id, puuid))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Opt-out / direito ao esquecimento
# ---------------------------------------------------------------------------
def purge_puuid(puuid):
    """Apaga TODO dado pessoal derivado de um puuid, em todas as tabelas.

    Cumpre o opt-out prometido no PLANO_ANALYTICS §7. Não apaga as partidas em
    si (são compartilhadas com os outros amigos que jogaram nelas) — apaga as
    linhas que identificam esta pessoa."""
    conn = get_connection()
    try:
        ph = _ph(conn)
        cur = conn.cursor()
        counts = {}
        for table, col in (("player_match_derived", "puuid"),
                           ("player_round_stats", "puuid"),
                           ("match_players", "puuid"),
                           ("spatial_events", "puuid"),
                           ("rank_history", "puuid"),
                           ("player_goals", "puuid")):
            cur.execute(f"DELETE FROM {table} WHERE {col} = {ph}", (puuid,))
            counts[table] = cur.rowcount
        for col in ("killer_puuid", "victim_puuid"):
            cur.execute(f"UPDATE kills SET {col} = NULL WHERE {col} = {ph}", (puuid,))
        cur.execute(f"DELETE FROM tracked_players WHERE puuid = {ph}", (puuid,))
        counts["tracked_players"] = cur.rowcount
        conn.commit()
        return counts
    finally:
        conn.close()


def delete_user_by_id(uid):
    conn = get_connection()
    try:
        conn.cursor().execute(f"DELETE FROM users WHERE id = {_ph(conn)}", (uid,))
        conn.commit()
    finally:
        conn.close()


def orphan_matches_cleanup():
    """Remove partidas que não sobraram jogadores rastreados (pós-purge)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM matches WHERE match_id NOT IN "
                    "(SELECT DISTINCT match_id FROM player_match_derived)")
        n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()
