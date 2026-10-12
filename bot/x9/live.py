"""Partidas em (quase) tempo real.

Nem a Riot nem a HenrikDev expõem a partida AO VIVO: ela só existe depois que
acaba. O que dá para fazer — e é o que isto faz — é baixar a partida minutos
depois do fim, em vez de esperar o poller de 15 min:

- presença do Discord: quem está "jogando VALORANT" agora (vínculo → puuid);
- jogando: consulta aquele jogador a cada `playing_every` (2 min);
- a presença mudou (saiu da partida, voltou ao menu) ou o jogo fechou: rajada
  de checagens em +45 s, 2, 4 e 8 min — a HenrikDev demora um pouco para ter
  a partida;
- ninguém jogando: varredura de base a cada `base_every` (5 min), que cobre
  quem não compartilha a atividade no Discord.

Orçamento da HenrikDev (30 req/min, dividido com o poller): no máximo 1
consulta a cada `min_gap` s — 7 jogadores jogando dão ~3,5 req/min.

Não importa discord: o bot chama `on_presence()` e passa `on_new_matches`.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger("x9.live")

AFTER_END = (45, 120, 240, 480)


@dataclass
class _State:
    playing: bool = False
    since: float = None
    sig: tuple = None
    detail: dict = None
    next: float = 0.0
    burst: list = field(default_factory=list)


class LiveTracker:
    def __init__(self, backend, on_new_matches, *, clock=time.time, base_every=300,
                 playing_every=120, after_end=AFTER_END, min_gap=5, roster_ttl=600):
        self.backend, self.on_new_matches = backend, on_new_matches
        self.clock = clock
        self.base_every, self.playing_every = base_every, playing_every
        self.after_end, self.min_gap, self.roster_ttl = after_end, min_gap, roster_ttl
        self.state = {}
        self._roster, self._roster_at = {}, -1e18

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
        """activity: None (não está no VALORANT) ou {"details", "state", "start"}."""
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

    def after_check(self, puuid, found_new):
        now = self.clock()
        st = self._st(puuid)
        st.burst = [] if found_new else [t for t in st.burst if t > now]
        if st.burst:
            st.next = st.burst[0]
        else:
            st.next = now + (self.playing_every if st.playing else self.base_every)

    def check(self, puuid):
        """Baixa as partidas novas de um jogador (bloqueante: rode em thread)."""
        from app import ingest

        roster = self.roster()
        member = roster.get(puuid)
        if not member:
            return []
        new_ids = []
        try:
            ingest.ingest_user(member, set(roster), collect=new_ids)
        except ingest.IngestError as e:
            logger.warning("Ao vivo: falha ao checar %s: %s", member["username"], e)
            return []
        if new_ids:
            # RR mudou com a partida: curva e rank atual na hora também.
            for fn in (ingest.ingest_rank_history, ingest.ingest_mmr_profile):
                try:
                    fn(member)
                except Exception as e:
                    logger.warning("Ao vivo: %s de %s falhou: %s", fn.__name__, member["username"], e)
            self.backend.db.update_ingest_state(puuid, last_mmr_raw=int(time.time()))
        return new_ids

    async def tick(self):
        await asyncio.to_thread(self.roster)
        pu = self.next_due()
        if not pu:
            return []
        new_ids = await asyncio.to_thread(self.check, pu)
        self.after_check(pu, bool(new_ids))
        if new_ids:
            logger.info("Ao vivo: %d partida(s) nova(s) de %s", len(new_ids), self._name(pu))
            await self.on_new_matches(new_ids)
        return new_ids

    async def run_forever(self):
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("Tick do ao-vivo falhou")
            await asyncio.sleep(self.min_gap)

    # --- tool "quem está jogando" ---------------------------------------------
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


PLAYING_TOOL_SCHEMA = {"type": "object", "properties": {}, "required": []}
PLAYING_TOOL_DESC = ("Quem do grupo está jogando VALORANT AGORA (pela atividade do Discord), "
                     "há quanto tempo e em que modo/estado.")
