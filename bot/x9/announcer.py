"""Loop do dedo-duro (docs/BOT_X9.md §2.1, docs/PLANO_X9_V2.md Fases 0, 1 e 5).

Caminhos:
1. ao-vivo (`announce_now`): o bot acabou de baixar a partida. Faz o claim do
   placar E da fofoca, gera a fofoca EM PARALELO com o post do embed e a põe na
   thread assim que o placar sai;
2. placar do poller: a mensagem do webhook chega pelo gateway e o bot chama
   `kick()` — a fofoca sai em segundos, sem esperar o tick;
3. tick (rede de segurança): placar de fallback para o que ninguém anunciou e
   fofoca do que ficou pendente; muito atraso (PC desligado) vira resumo único.

Depois da fofoca: fala na call (a mesma geração traz a versão falada), reações
no placar, apostas liquidadas, votação de MVP e lore (momentos e apelidos).

Concorrência: `gossip_pending` roda sob uma trava, e cada fofoca é reservada no
banco (`claim_gossip`) — ao-vivo, tick, kick e até uma 2ª instância do bot
nunca postam a mesma fofoca duas vezes. Falha que não é de LLM conta tentativa
e guarda o texto já gerado; depois de `max_tries` o X9 desiste e posta solto.

Não importa discord: fala com o mundo através de um `poster`:
    async post_embed(channel_id, embed) -> message_id
    async post_in_thread(channel_id, message_id, title, text) -> thread_id
    async speak(text) -> bool              in_voice() -> bool
    opcionais: edit_embed, post_text, react, post_mvp_vote
"""
import asyncio
import logging
import time

from .brain import BrainUnavailable, Gossip

logger = logging.getLogger("x9.announcer")

DIGEST_THRESHOLD = 3


class Announcer:
    def __init__(self, brain, poster, backend, *, channel_id=None, max_age_h=48,
                 claim_after_min=80, post_scoreboard=True, clock=time.time, max_tries=3,
                 reactions=True, mvp_vote=True, rank_posts=True, telemetry=None,
                 sleep=asyncio.sleep, mode_for=None):
        self.brain, self.poster, self.backend = brain, poster, backend
        self.channel_id = channel_id
        self.max_age_h, self.claim_after_min = max_age_h, claim_after_min
        self.post_scoreboard = post_scoreboard
        self.clock, self.sleep = clock, sleep
        self.max_tries = max_tries
        self.reactions, self.mvp_vote, self.rank_posts = reactions, mvp_vote, rank_posts
        self.telemetry = telemetry
        self.mode_for = mode_for or (lambda channel_id: "zoeira")
        self._lock = asyncio.Lock()
        self._kick_task = None

    async def _db(self, fn, *a, **k):
        return await asyncio.to_thread(fn, *a, **k)

    def _mode(self, channel_id):
        try:
            return self.mode_for(channel_id)
        except Exception:
            return "zoeira"

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
            if not await self._post_scoreboard(mid):
                break
            posted += 1
        return posted

    async def _post_scoreboard(self, mid):
        db, announce = self.backend.db, self.backend.announce
        try:
            embed = await self._db(announce.build_embed, mid)
            msg_id = await self.poster.post_embed(self.channel_id, embed)
        except Exception:
            logger.exception("Falha ao postar o placar de %s", mid[:8])
            await self._db(db.release_announcement, mid)
            return None
        ended = await self._db(announce.match_end_raw, mid) if hasattr(announce, "match_end_raw") \
            else None
        await self._db(db.update_announcement, mid, channel_id=str(self.channel_id),
                       message_id=str(msg_id), ended_raw=ended, posted_raw=int(self.clock()))
        if ended:
            logger.info("Placar de %s no ar %.0f s depois do fim da partida.", mid[:8],
                        self.clock() - ended)
        return msg_id

    # --- 1b. placar na hora, do que o próprio bot acabou de baixar ----------
    async def announce_now(self, match_ids):
        """O ao-vivo baixou partidas: placar já (sem esperar a vez do poller —
        o claim impede post duplo) e a fofoca gerada em paralelo."""
        db = self.backend.db
        bump = getattr(self.backend.botapi, "bump_version", None)
        if bump:
            bump()
        posted = 0
        if self.channel_id:
            for mid in match_ids:
                if not await self._db(db.claim_announcement, mid, "bot"):
                    continue
                gossip_mine = await self._db(db.claim_gossip, mid, int(self.clock()))
                task = asyncio.create_task(self._generate(mid)) if gossip_mine else None
                msg_id = await self._post_scoreboard(mid)
                if not msg_id:
                    if task:
                        task.cancel()
                    continue
                posted += 1
                if task:
                    ann = {"match_id": mid, "channel_id": str(self.channel_id),
                           "message_id": str(msg_id), "gossip_tries": 0,
                           "posted_raw": int(self.clock())}
                    await self._finish(ann, task)
        prewarm = getattr(self.brain, "prewarm", None)
        if prewarm:
            asyncio.create_task(prewarm())
        await self.gossip_pending()
        return posted

    # --- 2/3. fofoca --------------------------------------------------------
    def kick(self, delays=(1.5, 4, 9)):
        """O placar do poller acabou de chegar: fofoca já (o poller grava o
        message_id logo depois do post, por isso as tentativas espaçadas)."""
        if self._kick_task and not self._kick_task.done():
            return self._kick_task

        async def run():
            for d in delays:
                await self.sleep(d)
                try:
                    if await self.gossip_pending():
                        return
                except Exception:
                    logger.exception("Fofoca do kick falhou")
        self._kick_task = asyncio.create_task(run())
        return self._kick_task

    async def gossip_pending(self):
        db = self.backend.db
        async with self._lock:
            since = int(self.clock() - self.max_age_h * 3600)
            pend = await self._db(db.announcements_pending_gossip, since, int(self.clock()))
            if not pend:
                return 0
            if len(pend) > DIGEST_THRESHOLD:
                return await self._digest(pend)
            done = 0
            for ann in pend:
                if not await self._db(db.claim_gossip, ann["match_id"], int(self.clock())):
                    continue
                if ann.get("gossip_text"):
                    cached = Gossip(ann["gossip_text"], ann.get("voice_text") or "",
                                    ann.get("gossip_format"))
                    fut = asyncio.get_running_loop().create_future()
                    fut.set_result(cached)
                    task = fut
                else:
                    task = asyncio.create_task(self._generate(ann["match_id"]))
                result = await self._finish(ann, task)
                if result == "llm":
                    break  # sem LLM: as outras também falhariam agora
                done += result == "ok"
            return done

    async def _generate(self, mid):
        return await self.brain.gossip(mid, mode=self._mode(self.channel_id))

    async def _finish(self, ann, task):
        """Espera a fofoca e entrega. Devolve 'ok' | 'llm' | 'erro'."""
        db, mid = self.backend.db, ann["match_id"]
        try:
            gossip = await task
        except BrainUnavailable as e:
            logger.warning("Sem LLM para a fofoca de %s (tenta no próximo ciclo): %s", mid[:8], e)
            await self._db(db.release_gossip, mid)
            return "llm"
        except Exception:
            logger.exception("Fofoca de %s falhou ao gerar", mid[:8])
            return await self._failed(ann, None)
        if isinstance(gossip, str):
            gossip = Gossip(gossip, "")
        try:
            thread = await self.poster.post_in_thread(ann["channel_id"], ann["message_id"],
                                                      "X9 · dedo-duro", gossip.text)
        except Exception:
            logger.exception("Fofoca de %s falhou ao postar", mid[:8])
            return await self._failed(ann, gossip)
        now = int(self.clock())
        await self._db(db.update_announcement, mid, gossip_raw=now,
                       thread_id=str(thread) if thread else None, gossip_text=gossip.text,
                       voice_text=gossip.voice, gossip_format=gossip.fmt)
        if self.telemetry and ann.get("posted_raw"):
            self.telemetry.record_gossip(now - ann["posted_raw"])
        await self._extras(ann, gossip, thread)
        return "ok"

    async def _failed(self, ann, gossip):
        db, mid = self.backend.db, ann["match_id"]
        tries = (ann.get("gossip_tries") or 0) + 1
        if tries < self.max_tries:
            await self._db(db.release_gossip, mid, gossip.text if gossip else None,
                           gossip.voice if gossip else None, gossip.fmt if gossip else None)
            return "erro"
        # Desiste: a fofoca vai solta no canal (ou some, se nem isso der).
        logger.warning("Fofoca de %s desistiu depois de %d tentativas.", mid[:8], tries)
        post_text = getattr(self.poster, "post_text", None)
        if gossip and post_text:
            try:
                await post_text(ann["channel_id"], gossip.text)
            except Exception:
                logger.exception("Nem solta no canal a fofoca de %s saiu", mid[:8])
        await self._db(db.update_announcement, mid, gossip_raw=int(self.clock()), gossip_tries=tries,
                       gossip_text=gossip.text if gossip else None)
        return "erro"

    async def _digest(self, pend):
        db = self.backend.db
        mine = [a for a in pend if await self._db(db.claim_gossip, a["match_id"], int(self.clock()))]
        if not mine:
            return 0
        latest = mine[-1]
        try:
            g = await self.brain.gossip_digest([p["match_id"] for p in mine],
                                               mode=self._mode(self.channel_id))
            if isinstance(g, str):
                g = Gossip(g, "")
            thread = await self.poster.post_in_thread(
                latest["channel_id"], latest["message_id"], "X9 · enquanto eu tava off", g.text)
        except BrainUnavailable as e:
            logger.warning("Sem LLM para o resumo (tenta no próximo ciclo): %s", e)
            for p in mine:
                await self._db(db.release_gossip, p["match_id"])
            return 0
        except Exception:
            logger.exception("Resumo do atraso falhou")
            for p in mine:
                await self._db(db.release_gossip, p["match_id"])
            return 0
        now = int(self.clock())
        for p in mine:
            await self._db(db.update_announcement, p["match_id"], gossip_raw=now,
                           thread_id=str(thread) if p is latest else None,
                           gossip_format="digest", gossip_text=g.text if p is latest else None)
        return len(mine)

    # --- depois da fofoca ---------------------------------------------------
    async def _extras(self, ann, gossip, thread):
        mid = ann["match_id"]
        await self._speak(mid, gossip.voice or gossip.text)
        steps = (self._react(ann, gossip), self._bets(ann, thread), self._mvp(ann, thread),
                 self._lore(mid, gossip))
        for coro in steps:
            try:
                await coro
            except Exception:
                logger.exception("Pós-fofoca de %s falhou", mid[:8])

    async def _speak(self, match_id, line):
        if not line or not getattr(self.poster, "in_voice", lambda: False)():
            return
        try:
            if await self.poster.speak(line):
                await self._db(self.backend.db.update_announcement, match_id,
                               voice_raw=int(self.clock()))
        except Exception:
            logger.exception("Falha ao falar a partida %s na call", match_id[:8])

    async def _react(self, ann, gossip):
        react = getattr(self.poster, "react", None)
        if not (self.reactions and react):
            return
        tipos = {h.get("tipo") for h in gossip.hooks or ()}
        textos = " ".join(h.get("texto", "") for h in gossip.hooks or ())
        emojis = []
        if "ace" in tipos:
            emojis.append("🎯")
        if "clutch" in tipos:
            emojis.append("🧊")
        if "virada" in tipos:
            emojis.append("🔄")
        if "elo" in tipos:
            emojis.append("📈" if "SUBIU" in textos else "📉")
        if "ultimo_lobby" in tipos or "pior_da_vida" in tipos:
            emojis.append("💀")
        if "sequencia" in tipos and "derrota seguida" in textos:
            emojis.append("🫠")
        if "recorde" in tipos or "melhor_recente" in tipos:
            emojis.append("🔥")
        if emojis:
            await react(ann["channel_id"], ann["message_id"], emojis[:4])

    async def _bets(self, ann, thread):
        db, mid = self.backend.db, ann["match_id"]
        if not hasattr(db, "settle_bets"):
            return
        row = await self._db(db.get_match_row, mid) or {}
        res = await self._db(self.backend.botapi.amigos_e_resultado, mid)
        settled = await self._db(db.settle_bets, mid, row.get("started_raw"), res["venceu"],
                                 int(self.clock()))
        if not settled:
            return
        hits = [f"<@{b['discord_id']}>" for b in settled if b["hit"]]
        miss = [f"<@{b['discord_id']}>" for b in settled if not b["hit"]]
        text = "🎲 **Apostas**: " + (f"acertou: {', '.join(hits)}" if hits else "ninguém acertou")
        if miss:
            text += f" · errou: {', '.join(miss)}"
        await self.poster.post_in_thread(ann["channel_id"], ann["message_id"], "X9 · dedo-duro", text)

    def mvp_candidates(self, match_id):
        """[(puuid, nome)] dos amigos na partida, em ordem estável (os botões usam o índice)."""
        res = self.backend.botapi.amigos_e_resultado(match_id)
        ctx = self.backend.botapi._Ctx()
        return [(p, ctx.label(p)) for p in res["amigos_puuids"]]

    async def _mvp(self, ann, thread):
        post = getattr(self.poster, "post_mvp_vote", None)
        if not (self.mvp_vote and post):
            return
        cands = await self._db(self.mvp_candidates, ann["match_id"])
        if len(cands) < 2:
            return
        await post(ann["channel_id"], ann["message_id"], ann["match_id"],
                   [name for _, name in cands[:5]])

    async def _lore(self, mid, gossip):
        fn = getattr(self.backend.botapi, "atualizar_lore", None)
        if fn:
            await self._db(fn, mid, gossip.hooks or None)

    # --- RR chegou depois do placar ------------------------------------------
    async def update_rr(self, match_ids):
        """Reescreve o placar postado pelo bot com o RR e anuncia quem mudou de elo."""
        db, announce = self.backend.db, self.backend.announce
        bump = getattr(self.backend.botapi, "bump_version", None)
        if bump:
            bump()
        for mid in match_ids:
            ann = await self._db(db.get_announcement, mid)
            if not ann or not ann.get("message_id"):
                continue
            edit = getattr(self.poster, "edit_embed", None)
            if ann.get("claimed_by") == "bot" and edit:
                try:
                    await edit(ann["channel_id"], ann["message_id"],
                               await self._db(announce.build_embed, mid))
                except Exception:
                    logger.exception("Não consegui pôr o RR no placar de %s", mid[:8])
            if not self.rank_posts:
                continue
            ctx = await self._db(self.backend.botapi._Ctx)
            changes = await self._db(announce.rank_changes, mid, ctx)
            moved = [(n, c) for n, c in changes.items() if c.get("subiu") is not None]
            if not moved:
                continue
            lines = [f"{'📈' if c['subiu'] else '📉'} **{n}** "
                     f"{'SUBIU pra' if c['subiu'] else 'caiu pro'} **{c['tier']}**"
                     + (f" ({c['rr']:+d} RR)" if c.get("rr") is not None else "")
                     for n, c in moved]
            try:
                await self.poster.post_in_thread(ann["channel_id"], ann["message_id"],
                                                 "X9 · dedo-duro", "\n".join(lines))
            except Exception:
                logger.exception("Anúncio de elo de %s falhou", mid[:8])
            spoken = "; ".join(f"{n} {'subiu pra' if c['subiu'] else 'caiu pro'} {c['tier']}"
                               for n, c in moved)
            await self._speak(mid, f"Atenção: {spoken}.")

    async def run_forever(self, interval_s=120):
        while True:
            try:
                r = await self.tick()
                if r["placares"] or r["fofocas"]:
                    logger.info("Dedo-duro: %s", r)
            except Exception:
                logger.exception("Tick do announcer falhou")
            await self.sleep(interval_s)
