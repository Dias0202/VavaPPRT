"""Partidas em (quase) tempo real.

Nem a Riot nem a HenrikDev expõem a partida AO VIVO: ela só existe depois que
acaba. O que dá para fazer — e é o que isto faz — é baixar a partida segundos
depois de a HenrikDev publicá-la, em vez de esperar o poller de 15 min:

- presença do Discord: quem está "jogando VALORANT" agora (vínculo → puuid);
- jogando: consulta aquele jogador a cada `playing_every` (60 s);
- a presença mudou (saiu da partida, voltou ao menu) ou o jogo fechou: rajada
  de checagens em +30 s, 1, 1,5, 2, 3 e 5 min — a HenrikDev demora de 1 a 3 min
  para ter a partida;
- ninguém jogando: varredura de base a cada `base_every` (5 min), que cobre
  quem não compartilha a atividade no Discord.

X9 v2 (docs/PLANO_X9_V2.md, Fase 1):
- achou a partida → TODOS os amigos que estavam nela saem da rajada (antes,
  os outros 4 do stack continuavam consultando à toa);
- quem está na mesma party do Discord é checado como um só;
- a checagem pede só as partidas mais novas (`fetch_size`), não 5 completas;
- o placar sai na hora; o RR é buscado DEPOIS, em segundo plano, e vira
  edição do placar (`on_ranks`);
- 429 não trava o loop por 30 s: desiste e tenta no próximo ciclo.

Orçamento da HenrikDev (30 req/min, dividido com o poller): no máximo 1
consulta a cada `min_gap` s.

Não importa discord: o bot chama `on_presence()` e passa `on_new_matches`.
"""
import asyncio
import inspect
import logging
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger("x9.live")

AFTER_END = (30, 60, 90, 120, 180, 300)


@dataclass
class _State:
    playing: bool = False
    since: float = None
    sig: tuple = None
    detail: dict = None
    party: str = None
    next: float = 0.0
    burst: list = field(default_factory=list)
    last_check: float = None


class LiveTracker:
    def __init__(self, backend, on_new_matches, *, clock=time.time, base_every=300,
                 playing_every=60, after_end=AFTER_END, min_gap=5, roster_ttl=600,
                 fetch_size=2, on_ranks=None, spawn=None):
        self.backend, self.on_new_matches, self.on_ranks = backend, on_new_matches, on_ranks
        self.clock = clock
        self.base_every, self.playing_every = base_every, playing_every
        self.after_end, self.min_gap, self.roster_ttl = tuple(after_end), min_gap, roster_ttl
        self.fetch_size = fetch_size
        # Como rodar o "depois" de uma partida achada (anúncio + RR) sem
        # travar o loop de checagens; os testes passam um que espera.
        self.spawn = spawn or (lambda coro: asyncio.get_running_loop().create_task(coro))
        self.state = {}
        self._roster, self._roster_at = {}, -1e18
        self.checks = deque(maxlen=200)  # instantes das consultas (req/min no status)

    # --- roster ---------------------------------------------------------------
    def roster(self):
        now = self.clock()
        if now - self._roster_at > self.roster_ttl:
            members = [m for m in self.backend.db.get_roster() if m.get("puuid")]
            self._roster = {m["puuid"]: m for m in members}
            self._roster_at = now
            # quem entrou no roster agora ganha checagens espaçadas (sem rajada no boot)
            for i, pu in enumerate(sorted(self._roster)):
                if pu not in self.state:
                    self.state[pu] = _State(next=now + 20 + i * 15)
        return self._roster

    def _st(self, puuid):
        return self.state.setdefault(puuid, _State(next=self.clock() + self.base_every))

    # --- presença -------------------------------------------------------------
    def on_presence(self, puuid, activity):
        """activity: None (não está no VALORANT) ou {"details", "state", "start", "party"}."""
        if not puuid:
            return
        now = self.clock()
        st = self._st(puuid)
        playing = activity is not None
        sig = (activity.get("details"), activity.get("state")) if activity else None
        if playing and not st.playing:
            st.playing, st.since = True, activity.get("start") or now
            st.next = min(st.next, now + self.playing_every)
            logger.info("Ao vivo: %s abriu o VALORANT (%s / %s)", self._name(puuid),
                        activity.get("details"), activity.get("state"))
        elif not playing and st.playing:
            st.playing, st.since = False, None
            self._burst(st, now)
            logger.info("Ao vivo: %s fechou o VALORANT — checando partida nova", self._name(puuid))
        elif playing and sig != st.sig:
            # mudou de estado no jogo: fim de partida, volta ao menu, nova fila…
            self._burst(st, now)
            logger.info("Ao vivo: %s → %s / %s", self._name(puuid),
                        activity.get("details"), activity.get("state"))
        st.sig, st.detail = sig, activity
        st.party = (activity or {}).get("party") if playing else None

    def _burst(self, st, now):
        st.burst = [now + d for d in self.after_end]
        st.next = min(st.next, st.burst[0])

    def _name(self, puuid):
        m = self._roster.get(puuid)
        return m["username"] if m else puuid[:8]

    # --- agenda ---------------------------------------------------------------
    def next_due(self):
        now = self.clock()
        due = [(st.next, pu) for pu, st in self.state.items()
               if st.next <= now and pu in self._roster]
        return min(due)[1] if due else None

    def party_of(self, puuid):
        """Quem está na mesma party do Discord (incluindo o próprio)."""
        st = self.state.get(puuid)
        if not st or not st.party:
            return [puuid]
        return [pu for pu, s in self.state.items() if s.party == st.party and s.playing]

    def after_check(self, puuid, found_new):
        now = self.clock()
        st = self._st(puuid)
        st.last_check = now
        st.burst = [] if found_new else [t for t in st.burst if t > now]
        if st.burst:
            st.next = st.burst[0]
        else:
            st.next = now + (self.playing_every if st.playing else self.base_every)

    def _settle_group(self, puuids, found_new):
        for pu in puuids:
            if pu in self._roster:
                self.after_check(pu, found_new)

    def check(self, puuid):
        """Baixa as partidas novas de um jogador (bloqueante: rode em thread)."""
        from app import ingest

        roster = self.roster()
        member = roster.get(puuid)
        if not member:
            return []
        new_ids = []
        self.checks.append(self.clock())
        try:
            ingest.ingest_user(member, set(roster), collect=new_ids, size=self.fetch_size,
                               retry_429=False)
        except ingest.IngestError as e:
            logger.warning("Ao vivo: falha ao checar %s: %s", member["username"], e)
            return []
        return new_ids

    def players_in(self, match_ids):
        """puuids do roster que jogaram essas partidas (bloqueante)."""
        out = set()
        for mid in match_ids:
            for r in self.backend.db.match_players_for_match(mid):
                if r["puuid"] in self._roster:
                    out.add(r["puuid"])
        return out

    def refresh_ranks(self, puuids):
        """RR e rank atual depois da partida (bloqueante: rode em thread)."""
        from app import ingest

        for pu in puuids:
            member = self._roster.get(pu)
            if not member:
                continue
            for fn in (ingest.ingest_rank_history, ingest.ingest_mmr_profile):
                try:
                    fn(member)
                except Exception as e:
                    logger.warning("Ao vivo: %s de %s falhou: %s", fn.__name__, member["username"], e)
            self.backend.db.update_ingest_state(pu, last_mmr_raw=int(time.time()))

    async def tick(self):
        await asyncio.to_thread(self.roster)
        pu = self.next_due()
        if not pu:
            return []
        group = self.party_of(pu)
        new_ids = await asyncio.to_thread(self.check, pu)
        if not new_ids:
            self._settle_group(group, False)
            return []
        logger.info("Ao vivo: %d partida(s) nova(s) de %s", len(new_ids), self._name(pu))
        stack = await asyncio.to_thread(self.players_in, new_ids)
        self._settle_group(set(group) | stack | {pu}, True)
        job = self.spawn(self._after_new(new_ids, stack or {pu}))
        if inspect.isawaitable(job) and not isinstance(job, asyncio.Task):
            await job  # spawn de teste: roda junto
        return new_ids

    async def _after_new(self, new_ids, stack):
        try:
            await self.on_new_matches(new_ids)
        except Exception:
            logger.exception("Anúncio das partidas novas falhou")
        try:
            await asyncio.to_thread(self.refresh_ranks, sorted(stack))
            if self.on_ranks:
                await self.on_ranks(new_ids)
        except Exception:
            logger.exception("RR depois da partida falhou")

    async def run_forever(self):
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("Tick do ao-vivo falhou")
            await asyncio.sleep(self.min_gap)

    # --- tool "quem está jogando" e status ------------------------------------
    def playing_now(self):
        now = self.clock()
        out = []
        for pu, st in self.state.items():
            if st.playing and pu in self._roster:
                d = st.detail or {}
                out.append({"nome": self._name(pu),
                            "jogando_ha_min": int((now - (st.since or now)) // 60),
                            "detalhe": d.get("details"), "estado": d.get("state")})
        return {"jogando_agora": sorted(out, key=lambda x: x["nome"].lower()),
                "obs": "Pela atividade do Discord; só aparece quem compartilha a atividade "
                       "e está vinculado (/x9 vincular)."}

    def playing_line(self):
        """Linha para o contexto do LLM: quem está on agora."""
        on = self.playing_now()["jogando_agora"]
        if not on:
            return ""
        return "Jogando VALORANT agora (Discord): " + ", ".join(
            f"{p['nome']} (há {p['jogando_ha_min']} min)" for p in on) + "."

    def status(self):
        now = self.clock()
        rows = []
        for pu, st in self.state.items():
            if pu not in self._roster:
                continue
            rows.append({"nome": self._name(pu), "jogando": st.playing,
                         "proxima_em_s": max(0, int(st.next - now)), "rajada": len(st.burst)})
        rows.sort(key=lambda r: r["proxima_em_s"])
        return {"jogadores": rows,
                "consultas_ultimo_min": sum(1 for t in self.checks if now - t <= 60)}


PLAYING_TOOL_SCHEMA = {"type": "object", "properties": {}, "required": []}
PLAYING_TOOL_DESC = ("Quem do grupo está jogando VALORANT AGORA (pela atividade do Discord), "
                     "há quanto tempo e em que modo/estado.")
