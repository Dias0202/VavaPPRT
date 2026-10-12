"""Loop do dedo-duro (docs/BOT_X9.md §2.1).

A cada tick:
1. placar de fallback: partidas que o poller não anunciou (webhook desligado ou
   fora do ar) e já passaram da folga, o bot mesmo faz o claim e posta o embed;
2. fofoca: para cada anúncio sem `gossip_raw`, abre uma thread na mensagem do
   placar e posta a zoeira; se estiver numa call, fala o resumo curto;
3. muito atraso (PC ficou desligado): junta tudo num resumo único.

Não importa discord: fala com o mundo através de um `poster`:
    async post_embed(channel_id, embed) -> message_id
    async post_in_thread(channel_id, message_id, title, text) -> thread_id
    async speak(text) -> bool   (False = não está em call)
"""
import asyncio
import logging
import time

from .brain import BrainUnavailable

logger = logging.getLogger("x9.announcer")

DIGEST_THRESHOLD = 3


class Announcer:
    def __init__(self, brain, poster, backend, *, channel_id=None, max_age_h=48,
                 claim_after_min=80, post_scoreboard=True, clock=time.time):
        self.brain, self.poster, self.backend = brain, poster, backend
        self.channel_id = channel_id
        self.max_age_h, self.claim_after_min = max_age_h, claim_after_min
        self.post_scoreboard = post_scoreboard
        self.clock = clock

    async def _db(self, fn, *a, **k):
        return await asyncio.to_thread(fn, *a, **k)

    async def tick(self):
        posted = await self.fallback_scoreboards()
        gossiped = await self.gossip_pending()
        return {"placares": posted, "fofocas": gossiped}

    # --- 1. placar quando o poller não postou -------------------------------
    async def fallback_scoreboards(self):
        if not (self.post_scoreboard and self.channel_id):
            return 0
        db, botapi, announce = self.backend.db, self.backend.botapi, self.backend.announce
        friends = sorted((await self._db(botapi._Ctx)).friends)
        since = await self._db(announce.announce_since)
        cutoff = self.clock() - self.claim_after_min * 60
        posted = 0
        for mid in await self._db(db.unannounced_group_matches, friends, since):
            row = await self._db(db.get_match_row, mid) or {}
            if (row.get("started_raw") or 0) > cutoff:
                continue  # ainda é cedo: o poller tem a vez
            if not await self._db(db.claim_announcement, mid, "bot"):
                continue
            try:
                embed = await self._db(announce.build_embed, mid)
                msg_id = await self.poster.post_embed(self.channel_id, embed)
            except Exception:
                logger.exception("Falha ao postar o placar de %s", mid[:8])
                await self._db(db.release_announcement, mid)
                break
            await self._db(db.update_announcement, mid, channel_id=str(self.channel_id),
                           message_id=str(msg_id))
            posted += 1
        return posted

    # --- 2/3. fofoca --------------------------------------------------------
    async def gossip_pending(self):
        db = self.backend.db
        since = int(self.clock() - self.max_age_h * 3600)
        pend = await self._db(db.announcements_pending_gossip, since)
        if not pend:
            return 0
        now = int(self.clock())
        try:
            if len(pend) > DIGEST_THRESHOLD:
                latest = pend[-1]
                text = await self.brain.gossip_digest([p["match_id"] for p in pend])
                thread = await self.poster.post_in_thread(
                    latest["channel_id"], latest["message_id"], "X9 · enquanto eu tava off", text)
                for p in pend:
                    await self._db(db.update_announcement, p["match_id"], gossip_raw=now,
                                   thread_id=str(thread) if p is latest else None)
                return len(pend)
            done = 0
            for p in pend:
                text = await self.brain.gossip(p["match_id"])
                thread = await self.poster.post_in_thread(
                    p["channel_id"], p["message_id"], "X9 · dedo-duro", text)
                await self._db(db.update_announcement, p["match_id"], gossip_raw=now,
                               thread_id=str(thread))
                done += 1
                await self._speak(p["match_id"])
            return done
        except BrainUnavailable as e:
            logger.warning("Sem LLM para a fofoca (tenta no próximo ciclo): %s", e)
            return 0

    async def _speak(self, match_id):
        if not getattr(self.poster, "in_voice", lambda: False)():
            return
        try:
            line = await self.brain.voice_line(match_id)
            if await self.poster.speak(line):
                await self._db(self.backend.db.update_announcement, match_id,
                               voice_raw=int(self.clock()))
        except BrainUnavailable:
            pass
        except Exception:
            logger.exception("Falha ao falar a partida %s na call", match_id[:8])

    async def run_forever(self, interval_s=120):
        while True:
            try:
                r = await self.tick()
                if r["placares"] or r["fofocas"]:
                    logger.info("Dedo-duro: %s", r)
            except Exception:
                logger.exception("Tick do announcer falhou")
            await asyncio.sleep(interval_s)
