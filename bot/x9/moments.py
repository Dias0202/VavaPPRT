"""Momentos proativos do X9 (docs/PLANO_X9_V2.md, Fase 4.4).

Sem ninguém perguntar:
- **pré-fila**: 2+ do grupo abriram o VALORANT (presença do Discord) → espera
  um pouco para o resto chegar e pede a preleção (mapas e escalação). Uma vez
  por grupo a cada `prefila_cooldown_s`;
- **fim da noite**: todo mundo que estava jogando fechou o jogo há
  `night_idle_s` → resenha da noite (o callback decide se houve partidas);
- **Oscar do X9**: domingo, a partir das 22 h (hora do PC do bot), uma vez
  por semana (marcado no banco, sobrevive a reinício).

Não importa discord: o bot chama `on_presence()` e `tick()`; os callbacks são
async e recebem o necessário.
"""
import asyncio
import logging
import time
from datetime import datetime

logger = logging.getLogger("x9.moments")

OSCAR_KEY = "x9_oscar_week"


class Moments:
    def __init__(self, *, on_prefila=None, on_night_end=None, on_oscar=None, store=None,
                 clock=time.time, now=datetime.now, prefila_delay_s=90, prefila_max_wait_s=240,
                 prefila_cooldown_s=4 * 3600, night_idle_s=20 * 60, oscar_weekday=6,
                 oscar_hour=22):
        self.on_prefila, self.on_night_end, self.on_oscar = on_prefila, on_night_end, on_oscar
        self.store, self.clock, self.now = store, clock, now
        self.prefila_delay_s, self.prefila_max_wait_s = prefila_delay_s, prefila_max_wait_s
        self.prefila_cooldown_s, self.night_idle_s = prefila_cooldown_s, night_idle_s
        self.oscar_weekday, self.oscar_hour = oscar_weekday, oscar_hour
        self.playing = {}            # puuid -> desde quando
        self._prefila_due = None
        self._prefila_first = None
        self._prefila_done = {}      # frozenset(puuids) -> quando postou
        self.session_start = None
        self.idle_since = None
        self._oscar_week = None

    # --- presença ---------------------------------------------------------
    def on_presence(self, puuid, playing, quiet=False):
        """quiet: só registra (boot do bot com gente já jogando não é "acabaram
        de abrir o jogo" — sem isso, cada reinício repetiria a pré-fila)."""
        if not puuid:
            return
        now = self.clock()
        was = puuid in self.playing
        if playing and not was:
            self.playing[puuid] = now
            if self.session_start is None:
                self.session_start = now
            self.idle_since = None
            if len(self.playing) >= 2 and not quiet:
                # Espera o resto do stack abrir; sem empurrar para sempre.
                self._prefila_first = self._prefila_first or now
                self._prefila_due = min(now + self.prefila_delay_s,
                                        self._prefila_first + self.prefila_max_wait_s)
        elif not playing and was:
            self.playing.pop(puuid, None)
            if not self.playing:
                self.idle_since = now
                self._prefila_due = self._prefila_first = None

    # --- relógio ----------------------------------------------------------
    async def tick(self):
        fired = []
        now = self.clock()
        if self._prefila_due and now >= self._prefila_due:
            self._prefila_due = self._prefila_first = None
            group = frozenset(self.playing)
            last = max((t for g, t in self._prefila_done.items() if g >= group or group >= g),
                       default=None)
            if len(group) >= 2 and (last is None or now - last >= self.prefila_cooldown_s):
                self._prefila_done[group] = now
                fired.append("prefila")
                await self._call(self.on_prefila, sorted(group))
        if self.session_start is not None and not self.playing and self.idle_since is not None \
                and now - self.idle_since >= self.night_idle_s:
            start, self.session_start, self.idle_since = self.session_start, None, None
            fired.append("noite")
            await self._call(self.on_night_end, start)
        if await self._oscar_due():
            fired.append("oscar")
            await self._call(self.on_oscar)
        return fired

    async def _oscar_due(self):
        if not self.on_oscar:
            return False
        dt = self.now()
        if dt.weekday() != self.oscar_weekday or dt.hour < self.oscar_hour:
            return False
        week = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        if self._oscar_week == week:
            return False
        if self.store is not None:
            try:
                done = await asyncio.to_thread(self.store.get_config, OSCAR_KEY)
                if done == week:
                    self._oscar_week = week
                    return False
                await asyncio.to_thread(self.store.set_config, OSCAR_KEY, week)
            except Exception:
                logger.exception("Não consegui marcar o Oscar da semana")
                return False
        self._oscar_week = week
        return True

    async def _call(self, fn, *args):
        if not fn:
            return
        try:
            await fn(*args)
        except Exception:
            logger.exception("Momento proativo falhou")

    async def run_forever(self, interval_s=20):
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("Tick dos momentos falhou")
            await asyncio.sleep(interval_s)
