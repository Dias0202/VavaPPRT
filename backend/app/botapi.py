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
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone

from . import analytics, config, db, ingest, progress, squad
from .metrics import (classify_buy, compute_clutches, death_was_traded,
                      infer_attacker_by_round, kills_by_round)

logger = logging.getLogger("botapi")

MAX_MATCH_OFFSET = 50


class ToolError(Exception):
    """Erro de uso (jogador/partida/round inválido) — vira {"erro": ...}."""


# ---------------------------------------------------------------------------
# Roster e resolução de jogador
# ---------------------------------------------------------------------------
def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9#]", "", s.lower())


class _Ctx:
    """Roster consentido + vínculos do Discord, carregados uma vez por chamada."""

    def __init__(self, speaker_discord_id=None):
        roster = db.get_roster()
        self.all_puuids = {m["puuid"] for m in roster if m.get("puuid")}
        self.members = [m for m in roster if m.get("puuid") and m.get("consent")]
        self.by_puuid = {m["puuid"]: m for m in self.members}
        self.links = db.discord_links()
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
        row = db.get_match_row(str(partida).strip())
        if not row:
            raise ToolError("Não achei a partida '%s'." % partida)
        players = {r["puuid"] for r in db.match_players_for_match(row["match_id"])}
        if not players & set(puuids):
            raise ToolError("Ninguém do grupo jogou essa partida.")
        return row["match_id"], puuids
    if idx >= MAX_MATCH_OFFSET:
        raise ToolError("Só olho as últimas %d partidas." % MAX_MATCH_OFFSET)
    ids = db.group_matches(puuids, limit=1, offset=idx)
    if not ids:
        raise ToolError("Não tem partida %s no banco%s." % (
            "nenhuma" if idx == 0 else f"nº {idx + 1}",
            f" de {ctx.label(puuids[0])}" if jogador else ""))
    return ids[0], puuids


def garantir_detalhe(match_id, ctx=None):
    """raw da partida; se só houver resumo, baixa da HenrikDev (1 requisição)."""
    raw = db.get_raw_match(match_id)
    if raw:
        return raw
    if not config.HENRIKDEV_API_KEY:
        raise ToolError("Essa partida só tem resumo e estou sem chave da HenrikDev para baixar o detalhe.")
    ctx = ctx or _Ctx()
    row = db.get_match_row(match_id) or {}
    region = row.get("region")
    if not region:
        mates = [ctx.by_puuid[r["puuid"]] for r in db.match_players_for_match(match_id)
                 if r["puuid"] in ctx.by_puuid]
        region = (mates[0].get("region") if mates else None) or config.DEFAULT_REGION
    r = ingest._rehydrate(match_id, region, ctx.all_puuids)
    if r == 429:
        raise ToolError("A HenrikDev tá no limite de requisições agora; tenta de novo em 1 minuto.")
    if r != "ok":
        raise ToolError("Não consegui baixar o detalhe dessa partida na HenrikDev.")
    raw = db.get_raw_match(match_id)
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
        row = db.get_match_row(self.match_id) or {}
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
        row = db.get_match_row(mid) or {}
        mps = db.match_players_for_match(mid)
        raw = db.get_raw_match(mid) if row.get("has_raw") else None
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
    mids = db.group_matches(puuids, limite)
    bundles = db.match_bundles(mids)
    return {"partidas": [_match_brief(ctx, mid, bundles.get(mid)) for mid in mids]}


def resumo_partida(ctx, partida=None, jogador=None):
    mid, focus = _load(ctx, partida, jogador, full=False)
    raw = db.get_raw_match(mid)
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
    ov = analytics.player_overview(pu)
    if not ov.get("matches"):
        return {"jogador": ctx.label(pu), "partidas": 0}
    ins = analytics.insights(pu)
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
    t = progress.trend(pu, window=janela)
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
    maps = analytics.player_maps(pu, only_pool=False)[:10]
    agents = analytics.player_agents(pu)[:10]
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
    d = squad.duo(pa, pb, info)
    a, b = ctx.label(pa), ctx.label(pb)
    oa, ob = analytics.player_overview(pa), analytics.player_overview(pb)

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
    board = analytics.leaderboard(ctx.members)
    return {"ranking": [{"pos": r["rank"], "nome": r["username"], "partidas": r["matches"],
                         "win_rate": r["win_rate"], "kd": r["kd"], "acs": r["acs"],
                         "kast": r.get("kast")} for r in board]}


def resumo_semanal(ctx):
    w = progress.weekly_report(ctx.members)
    return {"partidas": w["matches_played"], "win_rate_grupo": w["group_win_rate"],
            "mvp": w["mvp"],
            "jogadores": [{"nome": p["username"], "partidas": p["matches"],
                           "win_rate": p["win_rate"], "kd": p["kd"], "acs": p["acs"],
                           "mudancas": [f"{c['label']} {c['verdict']} ({c['delta']:+})"
                                        for c in p["changes"]],
                           "melhor_mapa": p["best_map"]} for p in w["players"]]}


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------
_S = {"type": "string"}
_PARTIDA = {"type": "string", "description": "'ultima' (padrão), 'penultima', N (1 = última) "
                                             "ou o match_id."}
_JOG = {"type": "string", "description": "Apelido, Nome#TAG, menção <@id> ou 'eu'."}
_ROUND = {"type": "integer", "description": "Número do round, começando em 1."}


def _obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


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
    "comparar_jogadores": (comparar_jogadores,
                           "Compara dois jogadores: números gerais, juntos vs separados, quem "
                           "carrega e quem vinga quem.",
                           _obj({"jogador_a": _JOG, "jogador_b": _JOG}, ["jogador_a", "jogador_b"])),
    "ranking_grupo": (ranking_grupo, "Ranking do grupo por win rate e K/D.", _obj({})),
    "resumo_semanal": (resumo_semanal, "O que mudou no grupo nos últimos 7 dias.", _obj({})),
}


def tool_catalog():
    return [{"name": n, "description": d, "parameters": p} for n, (_, d, p) in TOOLS.items()]


def call_tool(name, args=None, speaker_discord_id=None):
    """Executa uma tool. Sempre devolve dict (erros em {"erro": ...})."""
    entry = TOOLS.get(name)
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
    return ctx.label(pu)


def linked_label(discord_id):
    pu = db.discord_links().get(str(discord_id))
    if not pu:
        return None
    return next((m["username"] for m in db.get_roster() if m["puuid"] == pu), None)


def list_links():
    by = {m["puuid"]: m["username"] for m in db.get_roster()}
    return [{"discord_id": d, "nome": by.get(p, "?")} for d, p in db.discord_links().items()]
