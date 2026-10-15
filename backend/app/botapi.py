"""botapi — a API do bot X9 (docs/BOT_X9.md §4).

Catálogo de *tools* tipadas e compactas, pensadas para um LLM consumir: o bot
do Discord importa este módulo direto (sem hop HTTP) e o FastAPI espelha o
mesmo catálogo em /bot/v1/tools.

Regras do contrato:
- jogador: apelido, Nome, Nome#TAG ou menção <@id>; "eu" = quem está falando;
- partida: "ultima" (padrão), "penultima", N (1 = última) ou match_id;
- round: 1-based, como o jogador fala;
- privacidade: amigos pelo apelido; quem NÃO é do grupo aparece só pelo agente
  ("Jett (inimigo)"). Nunca Riot ID nem puuid de quem não é do grupo;
- erro volta como {"erro": "..."}; a tool nunca lança exceção para o LLM.
"""
import difflib
import json
import logging
import re
import threading
import time
import unicodedata
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone

from . import analytics, config, db, ingest, matchview, progress, squad, storylines
from .metrics import (classify_buy, compute_clutches, death_was_traded,
                      infer_attacker_by_round, kills_by_round)

logger = logging.getLogger("botapi")

MAX_MATCH_OFFSET = 50


class ToolError(Exception):
    """Erro de uso (jogador/partida/round inválido) — vira {"erro": ...}."""


# ---------------------------------------------------------------------------
# Cache em memória (docs/PLANO_X9_V2.md, Fase 2.1)
#
# O banco fica a ~170 ms do PC do bot e cada tool fazia de 4 a 9 idas. Três
# camadas, todas por processo:
# - raw de partida: LRU sem prazo (uma partida completa nunca muda);
# - roster + vínculos: 60 s (e invalidado no vincular/desvincular);
# - o resto (listas de partidas, analytics): até a próxima partida nova
#   (`bump_version`) ou o prazo, o que vier primeiro. O bot chama
#   `bump_version` quando grava ou vê uma partida nova; o prazo cobre o que
#   entra por fora (poller, site).
# ---------------------------------------------------------------------------
ROSTER_TTL_S = 60
VOLATILE_TTL_S = 45     # "qual é a última partida" precisa ser fresco
ANALYTICS_TTL_S = 300
RAW_LRU_SIZE = 64

_cache_lock = threading.RLock()
_version = [0]
_memo = {}
_raw_lru = OrderedDict()
cache_stats = {"hits": 0, "misses": 0}


def invalidate_cache():
    """Esquece tudo (testes, opt-out)."""
    with _cache_lock:
        _memo.clear()
        _raw_lru.clear()
        _version[0] += 1


def bump_version():
    """Partida nova no banco: listas e agregados ficam velhos. O raw não."""
    with _cache_lock:
        _version[0] += 1
        _memo.clear()


def data_version():
    return _version[0]


def _forget(*keys):
    with _cache_lock:
        for k in keys:
            _memo.pop(k, None)


def _cached(key, fn, ttl=ANALYTICS_TTL_S):
    now = time.monotonic()
    with _cache_lock:
        e = _memo.get(key)
        if e and e[0] > now and e[1] == _version[0]:
            cache_stats["hits"] += 1
            return e[2]
        ver = _version[0]
    value = fn()
    with _cache_lock:
        cache_stats["misses"] += 1
        if ver == _version[0]:
            _memo[key] = (now + ttl, ver, value)
    return value


def _raw(match_id):
    with _cache_lock:
        if match_id in _raw_lru:
            _raw_lru.move_to_end(match_id)
            cache_stats["hits"] += 1
            return _raw_lru[match_id]
    raw = db.get_raw_match(match_id)
    if raw:
        _remember_raw(match_id, raw)
    return raw


def _remember_raw(match_id, raw):
    with _cache_lock:
        cache_stats["misses"] += 1
        _raw_lru[match_id] = raw
        _raw_lru.move_to_end(match_id)
        while len(_raw_lru) > RAW_LRU_SIZE:
            _raw_lru.popitem(last=False)


def _roster_bundle():
    return _cached(("roster",), lambda: (db.get_roster(), db.discord_links()), ROSTER_TTL_S)


def _row(match_id):
    return _cached(("row", match_id), lambda: db.get_match_row(match_id), VOLATILE_TTL_S)


def _match_players(match_id):
    return _cached(("mp", match_id), lambda: db.match_players_for_match(match_id), VOLATILE_TTL_S)


def _group_matches(puuids, limit=10, offset=0):
    key = ("gm", tuple(sorted(puuids)), limit, offset)
    return _cached(key, lambda: db.group_matches(list(puuids), limit, offset), VOLATILE_TTL_S)


def _bundles(match_ids):
    """{match_id: (row, match_players, raw)} pelo cache; o que falta vem em lote."""
    out, missing = {}, []
    now = time.monotonic()
    for mid in match_ids:
        with _cache_lock:
            row, mp = _memo.get(("row", mid)), _memo.get(("mp", mid))
            ok = all(e and e[0] > now and e[1] == _version[0] for e in (row, mp)) \
                and row[2] is not None
            has_raw = bool(ok and row[2].get("has_raw"))
            ok = ok and (not has_raw or mid in _raw_lru)
        if ok:
            out[mid] = (row[2], mp[2], _raw(mid) if has_raw else None)
        else:
            missing.append(mid)
    if missing:
        for mid, (row, mps, raw) in db.match_bundles(missing).items():
            with _cache_lock:
                _memo[("row", mid)] = (now + VOLATILE_TTL_S, _version[0], row)
                _memo[("mp", mid)] = (now + VOLATILE_TTL_S, _version[0], mps)
            if raw:
                _remember_raw(mid, raw)
            out[mid] = (row, mps, raw)
    return out


def _memo_call(name, fn, *args):
    return _cached(("fn", name) + tuple(args), lambda: fn(*args))


# ---------------------------------------------------------------------------
# Roster e resolução de jogador
# ---------------------------------------------------------------------------
def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9#]", "", s.lower())


class _Ctx:
    """Roster consentido + vínculos do Discord, carregados uma vez por chamada."""

    def __init__(self, speaker_discord_id=None):
        roster, links = _roster_bundle()
        self.all_puuids = {m["puuid"] for m in roster if m.get("puuid")}
        self.members = [m for m in roster if m.get("puuid") and m.get("consent")]
        self.by_puuid = {m["puuid"]: m for m in self.members}
        self.links = links
        self.speaker = self.links.get(str(speaker_discord_id)) if speaker_discord_id else None

    @property
    def friends(self):
        return set(self.by_puuid)

    def label(self, puuid):
        m = self.by_puuid.get(puuid)
        return m["username"] if m else None

    def resolve(self, query):
        """Texto livre -> puuid de um membro do grupo (ToolError se não der)."""
        q = str(query or "").strip()
        if not q:
            raise ToolError("Faltou dizer de quem.")
        if _norm(q) in ("eu", "mim", "me", "meu", "minha", "comigo"):
            if self.speaker and self.speaker in self.by_puuid:
                return self.speaker
            raise ToolError("Não sei quem você é: peça pro admin rodar /x9 vincular.")
        m = re.fullmatch(r"<@!?(\d+)>|(\d{15,21})", q)
        if m:
            pu = self.links.get(m.group(1) or m.group(2))
            if pu in self.by_puuid:
                return pu
            raise ToolError("Essa pessoa do Discord não está vinculada a ninguém do grupo.")

        nq = _norm(q.lstrip("@"))
        keys = {}  # chave normalizada -> puuid
        for p, mem in self.by_puuid.items():
            for k in (mem["username"], mem["riot_id"], f"{mem['riot_id']}#{mem['tagline']}"):
                keys.setdefault(_norm(k), set()).add(p)
        if nq in keys and len(keys[nq]) == 1:
            return next(iter(keys[nq]))
        partial = {p for k, ps in keys.items() if nq and (k.startswith(nq) or nq in k)
                   for p in ps}
        if len(partial) == 1:
            return partial.pop()
        if len(partial) > 1:
            raise ToolError("Mais de um jogador bate com '%s': %s." % (
                q, ", ".join(sorted(self.label(p) for p in partial))))
        close = difflib.get_close_matches(nq, list(keys), n=3, cutoff=0.7)
        found = {p for k in close for p in keys[k]}
        if len(found) == 1:
            return found.pop()
        raise ToolError("Não achei '%s' no grupo. Jogadores: %s." % (
            q, ", ".join(sorted(m["username"] for m in self.members))))


# ---------------------------------------------------------------------------
# Resolução de partida
# ---------------------------------------------------------------------------
_ORDINALS = {"ultima": 0, "last": 0, "penultima": 1, "antepenultima": 2}


def _resolve_match(ctx, partida=None, jogador=None):
    puuids = [ctx.resolve(jogador)] if jogador else sorted(ctx.friends)
    p = _norm(partida if partida is not None else "ultima")
    if p in _ORDINALS or p == "":
        idx = _ORDINALS.get(p, 0)
    elif p.isdigit() and len(p) <= 3:
        idx = int(p) - 1
        if idx < 0:
            raise ToolError("Partida 1 é a última; não existe partida %s." % partida)
    else:
        row = _row(str(partida).strip())
        if not row:
            raise ToolError("Não achei a partida '%s'." % partida)
        players = {r["puuid"] for r in _match_players(row["match_id"])}
        if not players & set(puuids):
            raise ToolError("Ninguém do grupo jogou essa partida.")
        return row["match_id"], puuids
    if idx >= MAX_MATCH_OFFSET:
        raise ToolError("Só olho as últimas %d partidas." % MAX_MATCH_OFFSET)
    ids = _group_matches(puuids, limit=1, offset=idx)
    if not ids:
        raise ToolError("Não tem partida %s no banco%s." % (
            "nenhuma" if idx == 0 else f"nº {idx + 1}",
            f" de {ctx.label(puuids[0])}" if jogador else ""))
    return ids[0], puuids


def garantir_detalhe(match_id, ctx=None):
    """raw da partida; se só houver resumo, baixa da HenrikDev (1 requisição)."""
    raw = _raw(match_id)
    if raw:
        return raw
    if not config.HENRIKDEV_API_KEY:
        raise ToolError("Essa partida só tem resumo e estou sem chave da HenrikDev para baixar o detalhe.")
    ctx = ctx or _Ctx()
    row = _row(match_id) or {}
    region = row.get("region")
    if not region:
        mates = [ctx.by_puuid[r["puuid"]] for r in _match_players(match_id)
                 if r["puuid"] in ctx.by_puuid]
        region = (mates[0].get("region") if mates else None) or config.DEFAULT_REGION
    r = ingest._rehydrate(match_id, region, ctx.all_puuids)
    if r == 429:
        raise ToolError("A HenrikDev tá no limite de requisições agora; tenta de novo em 1 minuto.")
    if r != "ok":
        raise ToolError("Não consegui baixar o detalhe dessa partida na HenrikDev.")
    _forget(("row", match_id), ("mp", match_id))
    raw = _raw(match_id)
    if not raw:
        raise ToolError("Baixei a partida mas ela veio sem rounds.")
    return raw


# ---------------------------------------------------------------------------
# Visão de uma partida completa (rounds, kills, economia) com labels seguros
# ---------------------------------------------------------------------------
_RESULT = {"elimination": "eliminação", "defuse": "spike desarmada",
           "detonate": "spike explodiu", "surrendered": "rendição",
           "round timer expired": "tempo acabou"}
_CEREMONY = {"ceremonyclutch": "clutch", "ceremonyflawless": "flawless (sem baixas)",
             "ceremonythrifty": "thrifty (ganhou de eco)", "ceremonycloser": "fechou a partida",
             "ceremonyace": "ace", "ceremonyteamace": "team ace"}


def _mmss(ms):
    s = int((ms or 0) // 1000)
    return f"{s // 60}:{s % 60:02d}"


def _when(started_raw):
    if not started_raw:
        return None
    dt = datetime.fromtimestamp(started_raw, tz=timezone.utc)
    h = (time.time() - started_raw) / 3600
    ago = (f"há {int(h * 60)} min" if h < 1 else f"há {int(h)} h" if h < 48
           else f"há {int(h // 24)} dias")
    return {"iso": dt.isoformat(timespec="minutes"), "ha": ago}


class _Match:
    def __init__(self, raw, ctx, focus_puuid=None):
        self.raw, self.ctx = raw, ctx
        md = raw.get("metadata") or {}
        self.match_id = md.get("match_id")
        self.map = (md.get("map") or {}).get("name")
        self.players = raw.get("players") or []
        self.team_of = {p["puuid"]: p.get("team_id") for p in self.players}
        self.agent_of = {p["puuid"]: (p.get("agent") or {}).get("name") or "?" for p in self.players}
        friends_here = [p for p in self.team_of if p in ctx.friends]
        if focus_puuid in self.team_of:
            self.our = self.team_of[focus_puuid]
        else:
            count = defaultdict(int)
            for p in friends_here:
                count[self.team_of[p]] += 1
            self.our = max(count, key=count.get) if count else None
        teams = [t.get("team_id") for t in raw.get("teams") or []]
        self.their = next((t for t in teams if t != self.our), None)
        self.rounds = sorted(raw.get("rounds") or [], key=lambda r: r["id"])
        self.n_rounds = len(self.rounds)
        self.winners = {r["id"]: r.get("winning_team") for r in self.rounds}
        self.kills = kills_by_round(raw)
        self.traded = death_was_traded(raw)
        self.clutches = defaultdict(list)
        for c in compute_clutches(raw):
            self.clutches[c["round"]].append(c)
        self.attacker = infer_attacker_by_round(raw)
        self.team_rosters = defaultdict(set)
        for p, t in self.team_of.items():
            self.team_rosters[t].add(p)

    # --- labels ------------------------------------------------------------
    def label(self, puuid):
        name = self.ctx.label(puuid)
        if name:
            return name
        side = "aliado" if self.team_of.get(puuid) == self.our else "inimigo"
        return f"{self.agent_of.get(puuid, '?')} ({side})"

    def side_word(self, team):
        return "nós" if team == self.our else "eles"

    # --- placar ------------------------------------------------------------
    def score_before(self, rid):
        ours = sum(1 for r, w in self.winners.items() if r < rid and w == self.our)
        theirs = sum(1 for r, w in self.winners.items() if r < rid and w and w != self.our)
        return ours, theirs

    def final_score(self):
        ours, theirs = self.score_before(10 ** 6)
        return ours, theirs

    def header(self):
        ours, theirs = self.final_score()
        row = _row(self.match_id) or {}
        return {"match_id": self.match_id, "mapa": self.map, "modo": row.get("mode"),
                "quando": _when(row.get("started_raw")),
                "placar": f"{ours}x{theirs}",
                "resultado": "vitória" if ours > theirs else "derrota" if ours < theirs else "empate"}

    def round_index(self, n):
        try:
            n = int(n)
        except (TypeError, ValueError):
            raise ToolError("Round inválido: '%s'." % n)
        if not 1 <= n <= self.n_rounds:
            raise ToolError("Essa partida teve %d rounds; não existe o round %d." % (self.n_rounds, n))
        return self.rounds[n - 1]

    # --- economia ----------------------------------------------------------
    def economy(self, rd):
        lv = defaultdict(list)
        stats = {}
        for s in rd.get("stats") or []:
            pu = (s.get("player") or {}).get("puuid")
            stats[pu] = s
            v = (s.get("economy") or {}).get("loadout_value")
            if v is not None:
                lv[self.team_of.get(pu)].append(v)
        out = {}
        for team in (self.our, self.their):
            vals = lv.get(team) or []
            avg = round(sum(vals) / len(vals)) if vals else None
            out[self.side_word(team)] = {"media_loadout": avg,
                                         "compra": classify_buy(avg) if avg is not None else "?"}
        return out, stats

    # --- vivos ao longo do round -------------------------------------------
    def advantage_track(self, rid):
        """(max vantagem nossa, max desvantagem nossa) em número de vivos."""
        alive = {t: set(ps) for t, ps in self.team_rosters.items()}
        best = worst = 0
        for k in self.kills.get(rid, []):
            alive[k["victim"]["team"]].discard(k["victim"]["puuid"])
            if not alive.get(self.our) or not alive.get(self.their):
                break
            diff = len(alive[self.our]) - len(alive[self.their])
            best, worst = max(best, diff), min(worst, diff)
        return best, worst, alive

    def round_view(self, n):
        rd = self.round_index(n)
        rid = rd["id"]
        econ, stats = self.economy(rd)
        ours_b, theirs_b = self.score_before(rid)
        won = self.winners.get(rid) == self.our
        kills = []
        for i, k in enumerate(self.kills.get(rid, [])):
            vp = k["victim"]["puuid"]
            kills.append({
                "t": _mmss(k["time_in_round_in_ms"]),
                "matador": self.label(k["killer"]["puuid"]),
                "vitima": self.label(vp),
                "lado_vitima": self.side_word(k["victim"]["team"]),
                "arma": (k.get("weapon") or {}).get("name"),
                "abertura": i == 0,
                "morte_trocada": bool(self.traded.get((rid, vp))),
            })
        _, _, alive = self.advantage_track(rid)
        plant = rd.get("plant") or {}
        defuse = rd.get("defuse") or {}
        atk = self.attacker.get(rid)
        friends = []
        died = {k["victim"]["puuid"] for k in self.kills.get(rid, [])}
        for pu in sorted(self.team_of):
            if pu not in self.ctx.friends:
                continue
            s = stats.get(pu) or {}
            st = s.get("stats") or {}
            friends.append({
                "nome": self.label(pu), "agente": self.agent_of.get(pu),
                "kills": st.get("kills", 0),
                "dano": sum(d.get("damage", 0) for d in s.get("damage_events") or []),
                "arma": ((s.get("economy") or {}).get("weapon") or {}).get("name"),
                "loadout": (s.get("economy") or {}).get("loadout_value"),
                "vivo_no_fim": pu not in died,
            })
        clutch = [{"quem": self.label(c["puuid"]), "1v": c["size"], "ganhou": c["won"]}
                  for c in self.clutches.get(rid, [])]
        return {
            "round": n, "total_rounds": self.n_rounds,
            "placar_antes": f"{ours_b}x{theirs_b}",
            "placar_depois": f"{ours_b + won}x{theirs_b + (not won)}",
            "vencedor": "nós" if won else "eles",
            "como_acabou": _RESULT.get((rd.get("result") or "").lower(), rd.get("result")),
            "cerimonia": _CEREMONY.get((rd.get("ceremony") or "").lower()),
            "nosso_lado": None if atk is None else ("ataque" if atk == self.our else "defesa"),
            "pistol": rid in (0, 12),
            "economia": econ,
            "kills": kills,
            "plant": None if not plant.get("player") else {
                "site": plant.get("site"), "quem": self.label(plant["player"]["puuid"]),
                "t": _mmss(plant.get("round_time_in_ms"))},
            "defuse": None if not defuse.get("player") else {
                "quem": self.label(defuse["player"]["puuid"]),
                "t": _mmss(defuse.get("round_time_in_ms"))},
            "sobreviventes": {self.side_word(t): sorted(self.label(p) for p in ps)
                              for t, ps in alive.items() if t},
            "clutch": clutch,
            "amigos": friends,
        }


def _load(ctx, partida=None, jogador=None, full=True):
    mid, puuids = _resolve_match(ctx, partida, jogador)
    focus = puuids[0] if jogador else None
    if full:
        return _Match(garantir_detalhe(mid, ctx), ctx, focus_puuid=focus)
    return mid, focus


# ---------------------------------------------------------------------------
# Tools: partidas e rounds
# ---------------------------------------------------------------------------
def listar_jogadores(ctx):
    linked = set(ctx.links.values())
    return {"jogadores": [{"nome": m["username"], "riot_id": f"{m['riot_id']}#{m['tagline']}",
                           "discord_vinculado": m["puuid"] in linked}
                          for m in sorted(ctx.members, key=lambda m: m["username"].lower())]}


def _friend_line(r, rounds):
    shots = (r.get("headshots") or 0) + (r.get("bodyshots") or 0) + (r.get("legshots") or 0)
    rounds = max(1, rounds or 1)
    return {"agente": r.get("agent"),
            "kda": f"{r.get('kills') or 0}/{r.get('deaths') or 0}/{r.get('assists') or 0}",
            "acs": round((r.get("score") or 0) / rounds),
            "adr": round((r.get("damage_made") or 0) / rounds, 1),
            "hs_pct": round(100 * (r.get("headshots") or 0) / shots, 1) if shots else 0.0}


def _match_brief(ctx, mid, bundle=None):
    if bundle:
        row, mps, raw = bundle
    else:
        row = _row(mid) or {}
        mps = _match_players(mid)
        raw = _raw(mid) if row.get("has_raw") else None
    friends = [r for r in mps if r["puuid"] in ctx.friends]
    rounds = row.get("rounds") or 1
    placar = None
    if raw:
        mv = _Match(raw, ctx)
        o, t = mv.final_score()
        placar = f"{o}x{t}"
        won = o > t
    else:
        won = bool(friends and friends[0].get("won"))
    return {"match_id": mid, "mapa": row.get("map"), "quando": _when(row.get("started_raw")),
            "resultado": "vitória" if won else "derrota", "placar": placar,
            "rounds": rounds, "detalhe_completo": bool(raw),
            "amigos": [{"nome": ctx.label(r["puuid"]), **_friend_line(r, rounds)}
                       for r in sorted(friends, key=lambda r: -(r.get("score") or 0))]}


def partidas_recentes(ctx, jogador=None, limite=5):
    try:
        limite = max(1, min(int(limite or 5), 15))
    except (TypeError, ValueError):
        limite = 5
    puuids = [ctx.resolve(jogador)] if jogador else sorted(ctx.friends)
    mids = _group_matches(puuids, limite)
    bundles = _bundles(mids)
    return {"partidas": [_match_brief(ctx, mid, bundles.get(mid)) for mid in mids]}


def resumo_partida(ctx, partida=None, jogador=None):
    mid, focus = _load(ctx, partida, jogador, full=False)
    raw = _raw(mid)
    if not raw:
        brief = _match_brief(ctx, mid)
        brief["obs"] = ("Só tenho o resumo desta partida; pergunte de um round que eu baixo o detalhe.")
        return brief
    mv = _Match(raw, ctx, focus_puuid=focus)
    n = max(1, mv.n_rounds)
    first_kills = defaultdict(int)
    for rid, ks in mv.kills.items():
        if ks:
            first_kills[ks[0]["killer"]["puuid"]] += 1
    ranked = sorted(mv.players, key=lambda p: -((p.get("stats") or {}).get("score") or 0))
    board = []
    for pos, p in enumerate(ranked, 1):
        st = p.get("stats") or {}
        shots = (st.get("headshots") or 0) + (st.get("bodyshots") or 0) + (st.get("legshots") or 0)
        board.append({
            "pos_lobby": pos, "nome": mv.label(p["puuid"]), "amigo": p["puuid"] in ctx.friends,
            "time": mv.side_word(p.get("team_id")), "agente": mv.agent_of[p["puuid"]],
            "kda": f"{st.get('kills', 0)}/{st.get('deaths', 0)}/{st.get('assists', 0)}",
            "acs": round((st.get("score") or 0) / n),
            "adr": round(((st.get("damage") or {}).get("dealt") or 0) / n, 1),
            "hs_pct": round(100 * (st.get("headshots") or 0) / shots, 1) if shots else 0.0,
            "first_kills": first_kills.get(p["puuid"], 0),
        })
    friends = [b for b in board if b["amigo"]]
    streak = _streaks(mv)
    return {**mv.header(), "rounds": mv.n_rounds, "detalhe_completo": True,
            "placar_lobby": board,
            "mvp_do_grupo": friends[0]["nome"] if friends else None,
            "pior_do_grupo": friends[-1]["nome"] if len(friends) > 1 else None,
            "top_do_lobby": board[0]["nome"] if board else None,
            "sequencias": streak}


def _streaks(mv):
    best_w = best_l = cur_w = cur_l = 0
    max_def = max_lead = 0
    for rd in mv.rounds:
        if mv.winners.get(rd["id"]) == mv.our:
            cur_w, cur_l = cur_w + 1, 0
        else:
            cur_l, cur_w = cur_l + 1, 0
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)
        o, t = mv.score_before(rd["id"] + 1)
        max_def, max_lead = max(max_def, t - o), max(max_lead, o - t)
    o, t = mv.final_score()
    virada = ("viramos" if o > t and max_def >= 4 else
              "tomamos virada" if t > o and max_lead >= 4 else None)
    return {"maior_sequencia_vitorias": best_w, "maior_sequencia_derrotas": best_l,
            "maior_desvantagem": max_def, "maior_vantagem": max_lead, "virada": virada}


def narrar_round(ctx, round, partida=None, jogador=None):
    mv = _load(ctx, partida, jogador)
    return {"partida": mv.header(), **mv.round_view(round)}


EVENTOS = ["clutch", "ace", "multikill", "first_blood", "first_death", "eco_ganho",
           "full_perdido", "pistol", "perdidos", "vencidos", "flawless", "vantagem_perdida"]


def _round_events(mv, rd, only=None):
    """Lista de (evento, detalhe) que aconteceram no round."""
    rid = rd["id"]
    won = mv.winners.get(rid) == mv.our
    econ, stats = mv.economy(rd)
    ours, theirs = econ.get("nós", {}), econ.get("eles", {})
    ev = []
    for c in mv.clutches.get(rid, []):
        if only and c["puuid"] != only:
            continue
        if c["puuid"] in mv.ctx.friends or mv.team_of.get(c["puuid"]) == mv.our:
            ev.append(("clutch", f"{mv.label(c['puuid'])} 1v{c['size']} "
                                 f"{'GANHOU' if c['won'] else 'perdeu'}"))
    for pu, s in stats.items():
        k = (s.get("stats") or {}).get("kills", 0)
        if only and pu != only:
            continue
        if k >= 5:
            ev.append(("ace", f"{mv.label(pu)} fez ACE"))
        elif k >= 3 and (pu in mv.ctx.friends or only):
            ev.append(("multikill", f"{mv.label(pu)} fez {k} kills"))
    ks = mv.kills.get(rid, [])
    if ks:
        fk, fv = ks[0]["killer"]["puuid"], ks[0]["victim"]["puuid"]
        if (only and fk == only) or (not only and fk in mv.ctx.friends):
            ev.append(("first_blood", f"{mv.label(fk)} abriu matando {mv.label(fv)}"))
        if (only and fv == only) or (not only and fv in mv.ctx.friends):
            ev.append(("first_death", f"{mv.label(fv)} morreu primeiro para {mv.label(fk)}"))
    pistol = rid in (0, 12)  # todo mundo está "de eco" no pistol: não é roubo de round
    if won and not pistol and ours.get("compra") == "eco":
        ev.append(("eco_ganho", f"ganhamos de eco ({ours.get('media_loadout')} vs {theirs.get('media_loadout')})"))
    if not won and not pistol and ours.get("compra") == "full" and theirs.get("compra") in ("eco", "force"):
        ev.append(("full_perdido", f"perdemos de full buy para {theirs.get('compra')}"))
    if pistol:
        ev.append(("pistol", "pistol " + ("ganho" if won else "perdido")))
    ev.append(("vencidos" if won else "perdidos", "ganhamos" if won else "perdemos"))
    if (rd.get("ceremony") or "").lower() == "ceremonyflawless":
        ev.append(("flawless", "flawless " + ("nosso" if won else "deles")))
    best, worst, _ = mv.advantage_track(rid)
    if not won and best >= 2:
        ev.append(("vantagem_perdida", f"estávamos com +{best} jogadores e perdemos"))
    if won and worst <= -2:
        ev.append(("vencidos", f"viramos o round estando com {worst} jogadores"))
    return ev


def buscar_rounds(ctx, evento, partida=None, jogador=None):
    evento = _norm(evento).replace("#", "")
    alias = {"clutches": "clutch", "firstblood": "first_blood", "fb": "first_blood",
             "firstdeath": "first_death", "fd": "first_death", "ecoganho": "eco_ganho",
             "fullperdido": "full_perdido", "vantagemperdida": "vantagem_perdida",
             "throw": "vantagem_perdida", "multikills": "multikill", "aces": "ace"}
    evento = alias.get(evento, evento)
    if evento not in EVENTOS:
        raise ToolError("Evento desconhecido. Opções: %s." % ", ".join(EVENTOS))
    only = ctx.resolve(jogador) if jogador else None
    mv = _load(ctx, partida, jogador)
    found = []
    for i, rd in enumerate(mv.rounds, 1):
        hits = [d for e, d in _round_events(mv, rd, only) if e == evento]
        if hits:
            found.append({"round": i, "vencedor": "nós" if mv.winners.get(rd["id"]) == mv.our
                          else "eles", "detalhe": "; ".join(hits)})
    return {"partida": mv.header(), "evento": evento, "total": len(found), "rounds": found}


def momentos_chave(ctx, partida=None, jogador=None):
    mv = _load(ctx, partida, jogador)
    wanted = ("clutch", "ace", "multikill", "eco_ganho", "full_perdido", "flawless",
              "vantagem_perdida", "pistol")
    moments = []
    for i, rd in enumerate(mv.rounds, 1):
        for e, d in _round_events(mv, rd):
            if e in wanted:
                moments.append({"round": i, "tipo": e, "detalhe": d})
    return {"partida": mv.header(), "sequencias": _streaks(mv), "momentos": moments}


def analisar_round(ctx, round, partida=None, jogador=None):
    mv = _load(ctx, partida, jogador)
    view = mv.round_view(round)
    rd = mv.round_index(round)
    rid = rd["id"]
    fatores = []
    ks = view["kills"]
    if ks:
        a = ks[0]
        fatores.append(("Abrimos o round: " if a["lado_vitima"] == "eles" else
                        "Perdemos a abertura: ") + f"{a['matador']} matou {a['vitima']} aos {a['t']}.")
    nossas_sem_trade = [k for k in ks if k["lado_vitima"] == "nós" and not k["morte_trocada"]]
    if nossas_sem_trade:
        fatores.append("%d morte(s) nossa(s) sem trade: %s." % (
            len(nossas_sem_trade), ", ".join(f"{k['vitima']} ({k['t']})" for k in nossas_sem_trade)))
    best, worst, _ = mv.advantage_track(rid)
    won = view["vencedor"] == "nós"
    if not won and best >= 1:
        fatores.append(f"Chegamos a ter +{best} jogador(es) de vantagem e perdemos.")
    if won and worst <= -1:
        fatores.append(f"Ganhamos mesmo estando com {-worst} jogador(es) a menos.")
    econ = view["economia"]
    fatores.append("Economia: nós %s (%s) vs eles %s (%s)." % (
        econ.get("nós", {}).get("compra"), econ.get("nós", {}).get("media_loadout"),
        econ.get("eles", {}).get("compra"), econ.get("eles", {}).get("media_loadout")))
    if view["nosso_lado"] == "ataque" and not view["plant"] and not won:
        fatores.append("Atacamos e nem plantamos a spike.")
    if view["plant"]:
        fatores.append(f"Spike plantada no {view['plant']['site']} por {view['plant']['quem']} "
                       f"aos {view['plant']['t']}.")
    if view["defuse"]:
        fatores.append(f"{view['defuse']['quem']} desarmou aos {view['defuse']['t']}.")
    for c in view["clutch"]:
        if c["quem"].endswith("(inimigo)") and not c["ganhou"]:
            continue  # inimigo "último vivo" que morreu não explica nada
        fatores.append(f"Clutch: {c['quem']} 1v{c['1v']} — {'ganhou' if c['ganhou'] else 'perdeu'}.")
    zerados = [f["nome"] for f in view["amigos"] if f["dano"] == 0 and not f["vivo_no_fim"]]
    if zerados:
        fatores.append("Morreram sem dar dano: %s." % ", ".join(zerados))
    return {"partida": mv.header(), "round": view["round"], "vencedor": view["vencedor"],
            "como_acabou": view["como_acabou"], "nosso_lado": view["nosso_lado"],
            "fatores": fatores, "kills": ks, "amigos": view["amigos"]}


# ---------------------------------------------------------------------------
# Tools: desempenho
# ---------------------------------------------------------------------------
def desempenho_jogador(ctx, jogador):
    pu = ctx.resolve(jogador)
    ov = _memo_call("overview", analytics.player_overview, pu)
    if not ov.get("matches"):
        return {"jogador": ctx.label(pu), "partidas": 0}
    ins = _memo_call("insights", analytics.insights, pu)
    return {
        "jogador": ctx.label(pu), "partidas": ov["matches"],
        "partidas_com_detalhe": ov.get("deep_matches"),
        "vitorias": ov["wins"], "win_rate": ov["win_rate"], "kd": ov["kd"],
        "acs": ov["acs"], "adr": ov["adr"], "hs_pct": ov["hs_pct"], "kast": ov["kast"],
        "first_bloods_por_jogo": ov["fb_per_match"], "first_deaths_por_jogo": ov["fd_per_match"],
        "mortes_trocadas_pct": ov["trade_pct"],
        "clutch": {k: ov["clutch"][k] for k in ("attempted", "won", "win_rate")},
        "multikills": ov["multikills"],
        "economia_win_rate": {k: v["wr"] for k, v in ov["economy"].items() if v["n"]},
        "lados_win_rate": {k: v["wr"] for k, v in ov["sides"].items() if v["n"]},
        "pontos_fortes": ins.get("strengths", []), "pontos_fracos": ins.get("gaps", []),
        "confianca": ov["confidence"],
    }


def tendencia_jogador(ctx, jogador, janela=10):
    pu = ctx.resolve(jogador)
    try:
        janela = max(5, min(int(janela or 10), 50))
    except (TypeError, ValueError):
        janela = 10
    t = _memo_call("trend", progress.trend, pu, janela)
    out = {"jogador": ctx.label(pu), "janela": janela, "disponivel": t.get("available")}
    if not t.get("available"):
        out["motivo"] = t.get("reason")
        out["atual"] = t.get("current")
        return out
    out["mudancas"] = [{"metrica": d["label"], "antes": d["previous"], "agora": d["current"],
                        "veredito": d["verdict"]} for d in t["deltas"]]
    return out


def mapas_e_agentes(ctx, jogador):
    pu = ctx.resolve(jogador)
    maps = _memo_call("maps", analytics.player_maps, pu, False)[:10]
    agents = _memo_call("agents", analytics.player_agents, pu)[:10]
    return {"jogador": ctx.label(pu),
            "mapas": [{"mapa": m["map"], "partidas": m["matches"], "win_rate": m["win_rate"],
                       "kd": m["kd"], "acs": m["acs"], "confianca": m["confidence"]} for m in maps],
            "agentes": [{"agente": a["agent"], "partidas": a["matches"], "win_rate": a["win_rate"],
                         "kd": a["kd"], "acs": a["acs"], "selo": a["tag"],
                         "confianca": a["confidence"]} for a in agents]}


def comparar_jogadores(ctx, jogador_a, jogador_b):
    pa, pb = ctx.resolve(jogador_a), ctx.resolve(jogador_b)
    if pa == pb:
        raise ToolError("Escolha duas pessoas diferentes.")
    info = {p: {"username": m["username"], "riot_id": m["riot_id"], "tagline": m["tagline"]}
            for p, m in ctx.by_puuid.items()}
    d = _cached(("fn", "duo", pa, pb), lambda: squad.duo(pa, pb, info))
    a, b = ctx.label(pa), ctx.label(pb)
    oa = _memo_call("overview", analytics.player_overview, pa)
    ob = _memo_call("overview", analytics.player_overview, pb)

    def basics(o):
        return {k: o.get(k) for k in ("matches", "win_rate", "kd", "acs", "adr", "hs_pct", "kast")}

    return {
        "a": a, "b": b, "geral": {a: basics(oa), b: basics(ob)},
        "juntos": {"partidas": d["together"]["games"], "win_rate": d["together"]["win_rate"],
                   "confianca": d["together"]["confidence"]},
        "um_contra_o_outro": d["against"]["games"],
        "win_rate_separados": {d["lift"]["a"]["name"]: d["lift"]["a"]["apart_wr"],
                               d["lift"]["b"]["name"]: d["lift"]["b"]["apart_wr"]},
        "quem_carrega": {a: {"acs_medio": d["carry"]["a_acs"], "vezes_maior_acs": d["carry"]["a_top"]},
                         b: {"acs_medio": d["carry"]["b_acs"], "vezes_maior_acs": d["carry"]["b_top"]}},
        "quem_vinga_quem": {f"{a} vinga {b} (%)": d["trades"]["a_avenges_b_pct"],
                            f"{b} vinga {a} (%)": d["trades"]["b_avenges_a_pct"]},
        "mapas_juntos": d["maps"][:5],
    }


def ranking_grupo(ctx):
    board = _cached(("fn", "leaderboard"), lambda: analytics.leaderboard(ctx.members))
    return {"ranking": [{"pos": r["rank"], "nome": r["username"], "partidas": r["matches"],
                         "win_rate": r["win_rate"], "kd": r["kd"], "acs": r["acs"],
                         "kast": r.get("kast")} for r in board]}


def resumo_semanal(ctx):
    w = _cached(("fn", "weekly"), lambda: progress.weekly_report(ctx.members))
    return {"partidas": w["matches_played"], "win_rate_grupo": w["group_win_rate"],
            "mvp": w["mvp"],
            "jogadores": [{"nome": p["username"], "partidas": p["matches"],
                           "win_rate": p["win_rate"], "kd": p["kd"], "acs": p["acs"],
                           "mudancas": [f"{c['label']} {c['verdict']} ({c['delta']:+})"
                                        for c in p["changes"]],
                           "melhor_mapa": p["best_map"]} for p in w["players"]]}


# ---------------------------------------------------------------------------
# Tools do X9 v2: fatos, por que, elo, noite, pré-fila, sinergia, armas…
# ---------------------------------------------------------------------------
DAY_S = 86400


def _history(puuids):
    """{puuid: linhas derivadas, mais novas primeiro} de vários jogadores em 1 query."""
    puuids = tuple(sorted(set(puuids)))

    def load():
        out = defaultdict(list)
        for r in db.derived_for_puuids(list(puuids)):
            out[r["puuid"]].append(r)
        return dict(out)
    return _cached(("hist", puuids), load) if puuids else {}


def _rank_rows_for(match_id):
    return _cached(("rank_match", match_id), lambda: db.rank_rows_for_match(match_id),
                   VOLATILE_TTL_S)


def _hooks_for(mv, ctx, limit=8):
    friends = [p for p in mv.team_of if p in ctx.friends]
    hist = _history(friends)
    ranks = {r["puuid"]: r for r in _rank_rows_for(mv.match_id) if r["puuid"] in ctx.friends}
    started = (_row(mv.match_id) or {}).get("started_raw") or 0
    prev = {pu: _cached(("rank_prev", pu, mv.match_id),
                        lambda pu=pu: db.previous_rank_row(pu, r.get("date_raw") or started))
            for pu, r in ranks.items()}
    return storylines.match_hooks(mv, ctx, hist, ranks, prev, limit=limit)


def fatos_partida(ctx, partida=None, jogador=None):
    mv = _load(ctx, partida, jogador)
    hooks = _hooks_for(mv, ctx, limit=10)
    return {"partida": mv.header(),
            "fatos": [{"tipo": h["tipo"], "fato": h["texto"], "surpresa": h["nota"]}
                      for h in hooks]}


def analisar_partida(ctx, partida=None, jogador=None):
    mv = _load(ctx, partida, jogador)
    hooks = _hooks_for(mv, ctx, limit=20)
    return {"partida": mv.header(), **storylines.match_factors(mv, hooks)}


def _elo_rows(ctx, puuids, now=None):
    now = now or time.time()
    puuids = list(puuids)
    profiles = _cached(("profiles", tuple(sorted(puuids))), lambda: db.rank_profiles(puuids))
    rows = _cached(("rank_since", tuple(sorted(puuids))),
                   lambda: db.rank_rows_since(puuids, int(now - 30 * DAY_S)), VOLATILE_TTL_S)
    by = defaultdict(list)
    for r in rows:
        by[r["puuid"]].append(r)
    out = []
    for pu in sorted(puuids, key=lambda p: (ctx.label(p) or "").lower()):
        prof, pts = profiles.get(pu) or {}, by.get(pu, [])
        last = pts[-1] if pts else {}
        tier_name = prof.get("current_tier_name") or last.get("tier_name")
        if not tier_name:
            out.append({"jogador": ctx.label(pu), "disponivel": False})
            continue
        changes = [p["rr_change"] for p in reversed(pts) if p.get("rr_change") is not None]
        week = [p["rr_change"] for p in pts if p.get("rr_change") is not None
                and (p.get("date_raw") or 0) >= now - 7 * DAY_S]
        today = [p["rr_change"] for p in pts if p.get("rr_change") is not None
                 and (p.get("date_raw") or 0) >= now - 14 * 3600]
        out.append({"jogador": ctx.label(pu), "elo": tier_name,
                    "rr": prof.get("current_rr") if prof else last.get("rr"),
                    "pico": prof.get("peak_tier_name"),
                    "ultimas_variacoes_rr": changes[:10],
                    "saldo_7_dias": sum(week) if week else None,
                    "saldo_hoje": sum(today) if today else None})
    return out


def elo_e_rr(ctx, jogador=None):
    if jogador:
        return _elo_rows(ctx, [ctx.resolve(jogador)])[0]
    return {"elo": _elo_rows(ctx, ctx.friends)}


def noite_de_hoje(ctx, jogador=None, now=None):
    """A sessão de jogo mais recente do grupo (buracos > 3 h separam sessões)."""
    now = now or time.time()
    pus = sorted(ctx.friends)

    def load():
        by = defaultdict(list)
        for r in db.derived_for_puuids(pus, since_raw=int(now - DAY_S)):
            by[r["puuid"]].append(r)
        ranks = db.rank_rows_since(pus, int(now - DAY_S))
        labels = {p: ctx.label(p) for p in pus}
        return storylines.night_summary(dict(by), labels, ranks, now)

    res = _cached(("night", tuple(pus)), load, VOLATILE_TTL_S) if pus else {"tem_noite": False}
    if jogador and res.get("tem_noite"):
        name = ctx.label(ctx.resolve(jogador))
        res = {**res, "jogadores": [p for p in res["jogadores"] if p["nome"] == name]}
    return res


def _names_to_puuids(ctx, jogadores):
    if isinstance(jogadores, (list, tuple)):
        parts = [str(x) for x in jogadores]
    else:
        parts = re.split(r"[,;/]|\s+e\s+", str(jogadores or ""))
    pus = []
    for part in (x.strip() for x in parts):
        if part:
            pu = ctx.resolve(part)
            if pu not in pus:
                pus.append(pu)
    return pus


def _info(ctx):
    return {p: {"username": m["username"], "riot_id": m["riot_id"], "tagline": m["tagline"]}
            for p, m in ctx.by_puuid.items()}


def pre_fila(ctx, jogadores):
    pus = _names_to_puuids(ctx, jogadores)[:5]
    if not pus:
        raise ToolError("Diga quem vai jogar (nomes separados por vírgula).")
    info = _info(ctx)
    r = _cached(("fn", "prequeue", tuple(sorted(pus))), lambda: squad.pre_queue(pus, info))
    return {"jogadores": [ctx.label(p) for p in pus],
            "mapas": [{"mapa": m["map"], "veredito": m["verdict"], "base": m["basis"],
                       "partidas": m["games"], "win_rate": m["win_rate"],
                       "escalacao": [f"{x['player']}: {x['agent'] or '?'}"
                                     + (f" ({x['win_rate']}% em {x['games']})" if x["games"] else "")
                                     for x in m["lineup"]]}
                      for m in r["maps"]]}


def sinergia_grupo(ctx):
    info = _info(ctx)
    pus = sorted(ctx.friends)
    m = _cached(("fn", "synergy", tuple(pus)), lambda: squad.synergy_matrix(pus, info))
    cells = [{"dupla": f"{ctx.label(c['a'])} + {ctx.label(c['b'])}", "partidas": c["games"],
              "win_rate": c["win_rate"], "confianca": c["confidence"]} for c in m["cells"]]
    solid = [c for c in cells if c["confianca"] == "high"]
    return {"melhores_duplas": sorted(solid, key=lambda c: -c["win_rate"])[:5],
            "piores_duplas": sorted(solid, key=lambda c: c["win_rate"])[:3],
            "mais_jogadas": sorted(cells, key=lambda c: -c["partidas"])[:5],
            "obs": f"Dupla só entra em melhores/piores com {analytics.MIN_DUO}+ partidas juntas."}


def armas_jogador(ctx, jogador):
    pu = ctx.resolve(jogador)
    w = _memo_call("weapons", analytics.player_weapons, pu)
    return {"jogador": ctx.label(pu), "kills_total": w["total_kills"],
            "armas": [{"arma": x["weapon"], "kills": x["kills"], "pct_das_kills": x["share"],
                       "hs_pct": x["hs_pct"], "confianca": x["aim_confidence"]}
                      for x in w["weapons"][:8]]}


def contra_o_lobby(ctx, jogador):
    pu = ctx.resolve(jogador)
    v = _memo_call("vs_lobby", matchview.vs_lobby, pu)
    lb, rk = v["lobby"], v.get("rank") or {}
    return {"jogador": ctx.label(pu), "partidas": lb["matches"], "confianca": lb["confidence"],
            "posicao_media_no_lobby": lb["avg_place"], "posicao_media_no_time": lb["avg_team_place"],
            "top1_do_lobby_pct": lb["match_mvp_pct"], "top1_do_time_pct": lb["team_mvp_pct"],
            "eu": lb["me"], "aliados": lb["teammates"], "inimigos": lb["enemies"],
            "elo": rk.get("tier_name"), "media_do_elo": rk.get("baseline"),
            "amostra_do_elo": rk.get("sample")}


def metas_jogador(ctx, jogador):
    pu = ctx.resolve(jogador)
    goals = progress.evaluate_goals(pu)
    return {"jogador": ctx.label(pu),
            "metas": [{"meta": g["label"], "de": g["baseline"], "alvo": g["target"],
                       "agora": g["current"], "progresso_pct": g["progress"], "status": g["status"]}
                      for g in goals],
            "obs": None if goals else "Sem metas ativas: dá para criar no site (aba Metas)."}


def _records(rows):
    if not rows:
        return None
    by_time = sorted(rows, key=lambda r: r.get("started_raw") or 0)
    best_w = best_l = cur_w = cur_l = 0
    for r in by_time:
        if r.get("won"):
            cur_w, cur_l = cur_w + 1, 0
        else:
            cur_l, cur_w = cur_l + 1, 0
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)

    def line(r):
        when = datetime.fromtimestamp(r.get("started_raw") or 0).strftime("%d/%m/%y")
        return (f"{r.get('map')} {when} ({r.get('agent')}) {r.get('kills')}/{r.get('deaths')}/"
                f"{r.get('assists')} ACS {round(storylines._acs(r))}")
    return {"partidas": len(rows),
            "melhor_acs": line(max(rows, key=storylines._acs)),
            "pior_acs": line(min(rows, key=storylines._acs)),
            "mais_kills": line(max(rows, key=lambda r: r.get("kills") or 0)),
            "maior_sequencia_vitorias": best_w, "maior_sequencia_derrotas": best_l}


def recordes(ctx, jogador=None):
    pus = [ctx.resolve(jogador)] if jogador else sorted(ctx.friends)
    hist = _history(pus)
    out = [{"jogador": ctx.label(p), **(_records(hist.get(p, [])) or {"partidas": 0})}
           for p in pus]
    return out[0] if jogador else {"recordes": out}


# --- tools agrupadas para o LLM (catálogo enxuto: menos tokens por passo) ----
def _pick(table, value, what):
    key = _norm(value or "").replace("#", "")
    fn = table.get(key)
    if not fn:
        raise ToolError(f"{what} inválido: '{value}'. Opções: {', '.join(sorted(table))}.")
    return fn


_PARTIDA_ASPECTOS = {"resumo": resumo_partida, "momentos": momentos_chave,
                     "fatos": fatos_partida, "porque": analisar_partida}
_JOGADOR_ASPECTOS = {"geral": desempenho_jogador, "tendencia": tendencia_jogador,
                     "mapasagentes": mapas_e_agentes, "armas": armas_jogador,
                     "vslobby": contra_o_lobby, "metas": metas_jogador, "elo": elo_e_rr,
                     "recordes": recordes, "noite": noite_de_hoje}
_GRUPO_ASPECTOS = {"ranking": ranking_grupo, "semana": resumo_semanal, "noite": noite_de_hoje,
                   "elo": elo_e_rr, "sinergia": sinergia_grupo, "recordes": recordes,
                   "jogadores": listar_jogadores}


def partida(ctx, aspecto="resumo", partida=None, jogador=None):
    return _pick(_PARTIDA_ASPECTOS, aspecto, "Aspecto")(ctx, partida=partida, jogador=jogador)


def round_(ctx, round, modo="narrar", partida=None, jogador=None):
    fn = _pick({"narrar": narrar_round, "analisar": analisar_round}, modo, "Modo")
    return fn(ctx, round, partida=partida, jogador=jogador)


def jogador(ctx, jogador, aspecto="geral", janela=None):
    fn = _pick(_JOGADOR_ASPECTOS, aspecto, "Aspecto")
    if fn is tendencia_jogador:
        return fn(ctx, jogador, janela or 10)
    return fn(ctx, jogador)


def grupo(ctx, aspecto="ranking"):
    return _pick(_GRUPO_ASPECTOS, aspecto, "Aspecto")(ctx)


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------
_S = {"type": "string"}
_PARTIDA = {"type": "string", "description": "'ultima' (padrão), 'penultima', N (1 = última) "
                                             "ou o match_id."}
_JOG = {"type": "string", "description": "Apelido, Nome#TAG, menção <@id> ou 'eu'."}
_ROUND = {"type": "integer", "description": "Número do round, começando em 1."}
_JOGS = {"type": "string", "description": "Quem vai jogar, separado por vírgula (até 5)."}


def _obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


# Catálogo completo (contrato do espelho HTTP /bot/v1 e dos testes).
TOOLS = {
    "listar_jogadores": (listar_jogadores, "Lista quem é do grupo (apelido, Riot ID).", _obj({})),
    "partidas_recentes": (partidas_recentes,
                          "Últimas partidas do grupo (ou de um jogador): mapa, quando, placar, "
                          "resultado e a linha de cada amigo.",
                          _obj({"jogador": _JOG, "limite": {"type": "integer",
                                                            "description": "1 a 15, padrão 5."}})),
    "resumo_partida": (resumo_partida,
                       "Placar completo de uma partida: os 10 jogadores (quem não é do grupo "
                       "aparece pelo agente), MVP e pior do grupo, sequências e viradas.",
                       _obj({"partida": _PARTIDA, "jogador": _JOG})),
    "narrar_round": (narrar_round,
                     "Narra um round específico: kills em ordem com tempo, quem abriu, trades, "
                     "plant/defuse, economia dos dois times, sobreviventes e clutch.",
                     _obj({"round": _ROUND, "partida": _PARTIDA, "jogador": _JOG}, ["round"])),
    "buscar_rounds": (buscar_rounds,
                      "Acha rounds de uma partida por evento (ex.: em qual round alguém fez "
                      "clutch, onde perdemos com full buy).",
                      _obj({"evento": {"type": "string", "enum": EVENTOS}, "partida": _PARTIDA,
                            "jogador": _JOG}, ["evento"])),
    "momentos_chave": (momentos_chave,
                       "Destaques automáticos da partida: clutches, aces, ecos ganhos, full buys "
                       "perdidos, vantagens jogadas fora, pistols, viradas e sequências.",
                       _obj({"partida": _PARTIDA, "jogador": _JOG})),
    "analisar_round": (analisar_round,
                       "Evidências para explicar POR QUE um round foi perdido/ganho: abertura, "
                       "mortes sem trade, vantagem numérica, economia, plant, clutch.",
                       _obj({"round": _ROUND, "partida": _PARTIDA, "jogador": _JOG}, ["round"])),
    "fatos_partida": (fatos_partida,
                      "Ganchos da partida com contexto: contra a própria média, recordes, RR e "
                      "elo, sequências, noite, mapa, lobby — cada um com o número que prova.",
                      _obj({"partida": _PARTIDA, "jogador": _JOG})),
    "analisar_partida": (analisar_partida,
                         "POR QUE a partida foi ganha/perdida: aberturas, trades, economia, "
                         "pós-plant/retake, lados, pistols e quem ficou abaixo da média.",
                         _obj({"partida": _PARTIDA, "jogador": _JOG})),
    "desempenho_jogador": (desempenho_jogador,
                           "Desempenho geral de um jogador: WR, K/D, ACS, ADR, HS%, KAST, entrada, "
                           "clutch, trade, economia, lados e pontos fortes/fracos.",
                           _obj({"jogador": _JOG}, ["jogador"])),
    "tendencia_jogador": (tendencia_jogador,
                          "O jogador está melhorando? Últimas N partidas vs as N anteriores.",
                          _obj({"jogador": _JOG, "janela": {"type": "integer",
                                                            "description": "5 a 50, padrão 10."}},
                               ["jogador"])),
    "mapas_e_agentes": (mapas_e_agentes, "Win rate e K/D por mapa e por agente de um jogador.",
                        _obj({"jogador": _JOG}, ["jogador"])),
    "armas_jogador": (armas_jogador, "Kills e HS% por arma de um jogador.",
                      _obj({"jogador": _JOG}, ["jogador"])),
    "contra_o_lobby": (contra_o_lobby,
                       "O jogador contra o lobby (posição média, top 1) e contra a média do elo.",
                       _obj({"jogador": _JOG}, ["jogador"])),
    "metas_jogador": (metas_jogador, "Metas de treino do jogador e o progresso.",
                      _obj({"jogador": _JOG}, ["jogador"])),
    "elo_e_rr": (elo_e_rr, "Elo atual, RR, pico e variações de RR (de um ou de todos).",
                 _obj({"jogador": _JOG})),
    "noite_de_hoje": (noite_de_hoje,
                      "A sessão de jogo mais recente: saldo, resultados, RR, MVP, bagre e tilt.",
                      _obj({"jogador": _JOG})),
    "pre_fila": (pre_fila, "Mapas para aceitar/evitar e escalação de agentes para quem vai jogar.",
                 _obj({"jogadores": _JOGS}, ["jogadores"])),
    "sinergia_grupo": (sinergia_grupo, "Melhores e piores duplas do grupo (win rate juntos).",
                       _obj({})),
    "recordes": (recordes, "Recordes: melhor/pior ACS, mais kills, maiores sequências.",
                 _obj({"jogador": _JOG})),
    "comparar_jogadores": (comparar_jogadores,
                           "Compara dois jogadores: números gerais, juntos vs separados, quem "
                           "carrega e quem vinga quem.",
                           _obj({"jogador_a": _JOG, "jogador_b": _JOG}, ["jogador_a", "jogador_b"])),
    "ranking_grupo": (ranking_grupo, "Ranking do grupo por win rate e K/D.", _obj({})),
    "resumo_semanal": (resumo_semanal, "O que mudou no grupo nos últimos 7 dias.", _obj({})),
}

# Catálogo enxuto que vai para o LLM: tools parecidas agrupadas por `aspecto`.
# Menos tokens por passo = resposta mais rápida e cabe na cota por minuto do Groq.
_ASP = {"type": "string"}
LLM_TOOLS = {
    "partidas_recentes": (partidas_recentes, "Últimas partidas do grupo ou de um jogador.",
                          _obj({"jogador": _JOG, "limite": {"type": "integer",
                                                            "description": "1 a 15"}})),
    "partida": (partida,
                "Uma partida. aspecto: resumo (placar dos 10, MVP/pior) | momentos (clutch, ace, "
                "ecos, viradas) | fatos (ganchos com contexto: vs média, recordes, RR, "
                "sequências) | por_que (o que decidiu o resultado).",
                _obj({"aspecto": {**_ASP, "enum": ["resumo", "momentos", "fatos", "por_que"]},
                      "partida": _PARTIDA, "jogador": _JOG})),
    "round": (round_, "Um round. modo: narrar (kills, economia, plant) | analisar (por que).",
              _obj({"round": _ROUND, "modo": {**_ASP, "enum": ["narrar", "analisar"]},
                    "partida": _PARTIDA, "jogador": _JOG}, ["round"])),
    "buscar_rounds": (buscar_rounds, "Rounds de uma partida por evento.",
                      _obj({"evento": {"type": "string", "enum": EVENTOS}, "partida": _PARTIDA,
                            "jogador": _JOG}, ["evento"])),
    "jogador": (jogador,
                "Um jogador. aspecto: geral | tendencia | mapas_agentes | armas | vs_lobby | metas "
                "| elo | recordes | noite.",
                _obj({"jogador": _JOG,
                      "aspecto": {**_ASP, "enum": ["geral", "tendencia", "mapas_agentes", "armas",
                                                   "vs_lobby", "metas", "elo", "recordes",
                                                   "noite"]},
                      "janela": {"type": "integer", "description": "tendencia: 5 a 50"}},
                     ["jogador"])),
    "grupo": (grupo,
              "O grupo. aspecto: ranking | semana | noite (sessão de hoje) | elo (todos) | "
              "sinergia (duplas) | recordes | jogadores.",
              _obj({"aspecto": {**_ASP, "enum": ["ranking", "semana", "noite", "elo", "sinergia",
                                                 "recordes", "jogadores"]}})),
    "comparar_jogadores": (comparar_jogadores, "Dois jogadores: juntos vs separados, quem carrega.",
                           _obj({"jogador_a": _JOG, "jogador_b": _JOG},
                                ["jogador_a", "jogador_b"])),
    "pre_fila": (pre_fila, "Mapas para aceitar/evitar e escalação para quem vai jogar.",
                 _obj({"jogadores": _JOGS}, ["jogadores"])),
}


def tool_catalog():
    return [{"name": n, "description": d, "parameters": p} for n, (_, d, p) in TOOLS.items()]


def llm_catalog():
    return [{"name": n, "description": d, "parameters": p} for n, (_, d, p) in LLM_TOOLS.items()]


def call_tool(name, args=None, speaker_discord_id=None):
    """Executa uma tool (do catálogo completo ou do enxuto). Sempre devolve dict."""
    entry = TOOLS.get(name) or LLM_TOOLS.get(name)
    if not entry:
        return {"erro": f"Ferramenta '{name}' não existe."}
    fn, _, schema = entry
    args = {k: v for k, v in (args or {}).items()
            if k in schema["properties"] and v not in (None, "")}
    missing = [k for k in schema["required"] if k not in args]
    if missing:
        return {"erro": "Faltou: %s." % ", ".join(missing)}
    try:
        return fn(_Ctx(speaker_discord_id), **args)
    except ToolError as e:
        return {"erro": str(e)}
    except Exception:
        logger.exception("Tool %s falhou com %s", name, json.dumps(args, ensure_ascii=False)[:300])
        return {"erro": "Deu ruim aqui dentro ao buscar isso."}


# ---------------------------------------------------------------------------
# Briefing: o contexto que vai pronto no prompt (Fase 2.2)
# ---------------------------------------------------------------------------
BRIEFING_TTL_S = 90


def briefing(now=None):
    """Resumo compacto do estado do grupo para o system prompt do X9.

    Com isto as perguntas comuns ("quem carregou?", "como foi hoje?", "qual o
    elo do X?") saem em UMA chamada de LLM, sem tool nenhuma."""
    return _cached(("briefing",), lambda: _build_briefing(now), BRIEFING_TTL_S)


def _build_briefing(now=None):
    now = now or time.time()
    ctx = _Ctx()
    if not ctx.members:
        return "Grupo vazio: ninguém cadastrado no site ainda."
    lines = []
    linked = set(ctx.links.values())
    lines.append("Grupo: " + ", ".join(
        m["username"] + ("" if m["puuid"] in linked else " (sem Discord vinculado)")
        for m in sorted(ctx.members, key=lambda m: m["username"].lower())) + ".")
    elo = [e for e in _elo_rows(ctx, ctx.friends, now) if e.get("disponivel", True)]
    if elo:
        lines.append("Elo agora: " + "; ".join(
            f"{e['jogador']} {e['elo']}" + (f" {e['rr']} RR" if e.get("rr") is not None else "")
            + (f" (hoje {e['saldo_hoje']:+d})" if e.get("saldo_hoje") else "") for e in elo) + ".")
    mids = _group_matches(sorted(ctx.friends), 5)
    bundles = _bundles(mids)
    if mids:
        lines.append('Últimas partidas do grupo (partida="1" é a mais recente):')
    for i, mid in enumerate(mids, 1):
        b = _match_brief(ctx, mid, bundles.get(mid))
        amigos = "; ".join(f"{a['nome']} ({a['agente']}) {a['kda']} ACS {a['acs']}"
                           for a in b["amigos"])
        quando = (b.get("quando") or {}).get("ha", "?")
        lines.append(f"{i}) {b['mapa']} · {b['resultado']} {b['placar'] or ''} · {quando} — {amigos}")
    if mids and bundles.get(mids[0]) and bundles[mids[0]][2]:
        try:
            mv = _Match(bundles[mids[0]][2], ctx)
            hooks = _hooks_for(mv, ctx, limit=5)
            if hooks:
                lines.append("Fatos da partida 1: " + " | ".join(h["texto"] for h in hooks))
        except Exception:
            logger.exception("Briefing: fatos da última falharam")
    n = noite_de_hoje(ctx, now=now)
    if n.get("tem_noite") and n.get("em_andamento"):
        lines.append(
            f"Noite de hoje: {n['partidas']} partida(s), {n['vitorias']}V {n['derrotas']}D "
            f"({n['resultados']}). " + "; ".join(
                f"{p['nome']} {p['vitorias']}V {p['derrotas']}D K/D {p['kd']}"
                + (f" {p['rr']:+d} RR" if p.get("rr") is not None else "")
                for p in n["jogadores"]) + ".")
    lore = lore_lines(ctx, now)
    if lore:
        lines.append("Lore do grupo (apelidos e momentos, bom para callback): " + " | ".join(lore))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Material para o bot (fofoca, noite, pré-fila, Oscar) e lore
# ---------------------------------------------------------------------------
def material_fofoca(match_id):
    """Tudo que a fofoca de uma partida precisa, numa chamada (sem tool loop)."""
    ctx = _Ctx()
    mv = _Match(garantir_detalhe(match_id, ctx), ctx)
    res = resumo_partida(ctx, match_id)
    hooks = _hooks_for(mv, ctx, limit=8)
    head = {k: v for k, v in mv.header().items() if k != "match_id"}
    return {"partida": head, "rounds": mv.n_rounds,
            "amigos": [{k: b[k] for k in ("nome", "agente", "kda", "acs", "adr", "hs_pct",
                                          "first_kills", "pos_lobby")}
                       for b in res["placar_lobby"] if b["amigo"]],
            "top_do_lobby": res["top_do_lobby"], "mvp_do_grupo": res["mvp_do_grupo"],
            "pior_do_grupo": res["pior_do_grupo"], "sequencias": res["sequencias"],
            "fatos": [h["texto"] for h in hooks], "_hooks": hooks,
            "amigos_puuids": sorted(p for p in mv.team_of if p in ctx.friends),
            "venceu": mv.final_score()[0] > mv.final_score()[1]}


def amigos_e_resultado(match_id):
    """{"venceu": bool, "amigos_puuids": [...]} — barato (raw do cache)."""
    ctx = _Ctx()
    raw = _raw(match_id)
    if raw:
        mv = _Match(raw, ctx)
        o, t = mv.final_score()
        return {"venceu": o > t, "amigos_puuids": sorted(p for p in mv.team_of if p in ctx.friends)}
    friends = [r for r in _match_players(match_id) if r["puuid"] in ctx.friends]
    return {"venceu": bool(friends and friends[0].get("won")),
            "amigos_puuids": sorted(r["puuid"] for r in friends)}


def material_noite(now=None):
    ctx = _Ctx()
    n = noite_de_hoje(ctx, now=now)
    if not n.get("tem_noite"):
        return n
    mids = _group_matches(sorted(ctx.friends), n["partidas"])
    bundles = _bundles(mids)
    n["partidas_detalhe"] = [{k: v for k, v in _match_brief(ctx, m, bundles.get(m)).items()
                              if k not in ("match_id",)} for m in mids]
    n["elo"] = _elo_rows(ctx, ctx.friends, now)
    return n


def material_prefila(puuids):
    ctx = _Ctx()
    pus = [p for p in puuids if p in ctx.friends]
    if len(pus) < 1:
        return None
    pf = pre_fila(ctx, [ctx.label(p) for p in pus])
    pf["elo"] = _elo_rows(ctx, pus)
    pf["mapas"] = pf["mapas"][:6]
    return pf


def material_oscar(now=None):
    """A semana do grupo para o "Oscar do X9"."""
    now = now or time.time()
    ctx = _Ctx()
    week = resumo_semanal(ctx)
    pus = sorted(ctx.friends)
    rows = [r for r in db.derived_for_puuids(pus, since_raw=int(now - 7 * DAY_S))]
    by = defaultdict(list)
    for r in rows:
        by[r["puuid"]].append(r)
    labels = {p: ctx.label(p) for p in pus}
    best = max(rows, key=storylines._acs) if rows else None
    worst = min(rows, key=storylines._acs) if rows else None

    def line(r):
        return (f"{labels.get(r['puuid'])} na {r.get('map')} ({r.get('agent')}) "
                f"{r.get('kills')}/{r.get('deaths')}/{r.get('assists')} ACS {round(storylines._acs(r))}")
    return {"semana": week,
            "melhor_partida": line(best) if best else None,
            "pior_partida": line(worst) if worst else None,
            "apelidos": [{k: n[k] for k in ("nome", "apelido", "prova")}
                         for n in storylines.nicknames(dict(by), labels)],
            "duplas": sinergia_grupo(ctx), "elo": _elo_rows(ctx, pus, now)}


def lore_lines(ctx=None, now=None):
    ctx = ctx or _Ctx()
    out = []
    for item in _cached(("lore",), lambda: db.active_lore(now), VOLATILE_TTL_S):
        if item.get("puuid") and item["puuid"] not in ctx.friends:
            continue  # quem saiu (ou não consente) não vira piada
        out.append(item["text"])
    return out[:12]


LORE_MOMENT_TYPES = {"ace", "recorde", "pior_da_vida", "elo", "virada"}


def atualizar_lore(match_id, hooks=None, now=None):
    """Grava os momentos marcantes da partida (30 dias) e recalcula os
    apelidos da semana (7 dias; o título muda de dono quando outro passa)."""
    now = int(now or time.time())
    ctx = _Ctx()
    row = _row(match_id) or {}
    when = datetime.fromtimestamp(row.get("started_raw") or now).strftime("%d/%m")
    if hooks is None:
        hooks = _hooks_for(_Match(garantir_detalhe(match_id, ctx), ctx), ctx, limit=12)
    n = 0
    for h in hooks:
        big_clutch = h["tipo"] == "clutch" and any(f"1v{n}" in h["texto"] for n in (3, 4, 5))
        if h["tipo"] in LORE_MOMENT_TYPES or big_clutch:
            db.add_lore(f"momento:{match_id}:{h['tipo']}:{h.get('puuid') or ''}", h.get("puuid"),
                        "momento", f"{when} {row.get('map') or ''}: {h['texto']}",
                        now + 30 * DAY_S, now)
            n += 1
    pus = sorted(ctx.friends)
    by = defaultdict(list)
    for r in db.derived_for_puuids(pus, since_raw=now - 7 * DAY_S):
        by[r["puuid"]].append(r)
    for nick in storylines.nicknames(dict(by), {p: ctx.label(p) for p in pus}):
        db.add_lore(f"apelido:{nick['chave']}", nick["puuid"], "apelido",
                    f"{nick['nome']} é o \"{nick['apelido']}\" da semana ({nick['prova']})",
                    now + 7 * DAY_S, now)
        n += 1
    _forget(("lore",), ("briefing",))
    return n


# ---------------------------------------------------------------------------
# Helpers do bot (não são tools do LLM)
# ---------------------------------------------------------------------------
def link_member(discord_id, riot_text, linked_by=None):
    """Vincula um usuário do Discord a um membro do roster. Devolve o apelido."""
    ctx = _Ctx()
    text = str(riot_text or "").strip()
    pu = None
    if "#" in text:
        name, tag = text.rsplit("#", 1)
        m = db.get_roster_member_by_riot(name.strip(), tag.strip())
        pu = m and m.get("puuid")
    if not pu:
        pu = ctx.resolve(text)
    if pu not in ctx.by_puuid:
        raise ToolError("Esse jogador não autorizou exibir dados.")
    db.link_discord(discord_id, pu, linked_by)
    _forget(("roster",), ("briefing",))
    return ctx.label(pu)


def unlink_member(discord_id):
    n = db.unlink_discord(discord_id)
    _forget(("roster",), ("briefing",))
    return n


def linked_label(discord_id):
    roster, links = _roster_bundle()
    pu = links.get(str(discord_id))
    if not pu:
        return None
    return next((m["username"] for m in roster if m["puuid"] == pu), None)


def linked_puuid(discord_id):
    return _roster_bundle()[1].get(str(discord_id))


def discord_of(puuid):
    """discord_id vinculado a um puuid (ou None)."""
    return next((d for d, p in _roster_bundle()[1].items() if p == puuid), None)


def list_links():
    roster, links = _roster_bundle()
    by = {m["puuid"]: m["username"] for m in roster}
    return [{"discord_id": d, "nome": by.get(p, "?")} for d, p in links.items()]
