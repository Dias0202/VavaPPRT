"""Cliente do Discord: menções, threads, /x9 …, voz, o dedo-duro e os momentos proativos."""
import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import voice_recv

from . import config
from .announcer import Announcer
from .brain import MODES, UNAVAILABLE_REPLY, BrainUnavailable, split_message
from .streaming import LiveMessage
from .telemetry import match_summary
from .tts import PiperEngine, Speaker
from .voice import VoiceSession

logger = logging.getLogger("x9.discord")

NO_MENTIONS = discord.AllowedMentions.none()


class MvpButton(discord.ui.DynamicItem[discord.ui.Button],
                template=r"x9mvp:(?P<mid>[\w-]+):(?P<i>\d)"):
    """Botão de voto no MVP. O custom_id carrega a partida e o índice do
    candidato, então o botão funciona mesmo depois de o bot reiniciar."""

    def __init__(self, match_id, idx, label="?"):
        super().__init__(discord.ui.Button(label=label[:80], style=discord.ButtonStyle.secondary,
                                           custom_id=f"x9mvp:{match_id}:{idx}"))
        self.match_id, self.idx = match_id, idx

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["mid"], int(match["i"]), item.label or "?")

    async def callback(self, interaction):
        await interaction.client.handle_mvp_vote(interaction, self.match_id, self.idx)


class _Poster:
    """Ponte do Announcer com o Discord."""

    def __init__(self, bot):
        self.bot = bot

    async def _channel(self, channel_id):
        ch = self.bot.get_channel(int(channel_id))
        return ch or await self.bot.fetch_channel(int(channel_id))

    async def post_embed(self, channel_id, embed):
        ch = await self._channel(channel_id)
        msg = await ch.send(embed=discord.Embed.from_dict(embed))
        return msg.id

    async def edit_embed(self, channel_id, message_id, embed):
        ch = await self._channel(channel_id)
        msg = await ch.fetch_message(int(message_id))
        await msg.edit(embed=discord.Embed.from_dict(embed))
        return True

    async def post_text(self, channel_id, text):
        ch = await self._channel(channel_id)
        last = None
        for part in split_message(text):
            last = await ch.send(part, allowed_mentions=NO_MENTIONS)
        return last.id if last else None

    async def _thread(self, channel_id, message_id, title):
        ch = await self._channel(channel_id)
        try:
            msg = await ch.fetch_message(int(message_id))
        except discord.NotFound:
            return ch  # placar apagado: vai solto no canal mesmo
        if msg.thread:
            return msg.thread
        try:
            return await msg.create_thread(name=title[:90], auto_archive_duration=1440)
        except discord.HTTPException:
            # Thread já existe (id da thread = id da mensagem).
            return await self.bot.fetch_channel(msg.id)

    async def post_in_thread(self, channel_id, message_id, title, text):
        target = await self._thread(channel_id, message_id, title)
        for part in split_message(text):
            await target.send(part, allowed_mentions=NO_MENTIONS)
        return target.id if isinstance(target, discord.Thread) else None

    async def react(self, channel_id, message_id, emojis):
        ch = await self._channel(channel_id)
        msg = await ch.fetch_message(int(message_id))
        for e in emojis:
            try:
                await msg.add_reaction(e)
            except discord.HTTPException:
                pass

    async def post_mvp_vote(self, channel_id, message_id, match_id, labels):
        target = await self._thread(channel_id, message_id, "X9 · dedo-duro")
        view = discord.ui.View(timeout=None)
        for i, label in enumerate(labels):
            view.add_item(MvpButton(match_id, i, label))
        await target.send("🗳️ **Quem foi o MVP de verdade?** (vota aí)", view=view,
                          allowed_mentions=NO_MENTIONS)

    def in_voice(self):
        return any(s.connected() for s in self.bot.voice_sessions.values())

    async def speak(self, text):
        spoke = False
        for s in list(self.bot.voice_sessions.values()):
            if s.connected():
                spoke = await s.say(text) or spoke
        return spoke


class X9Bot(discord.Client):
    def __init__(self, brain, backend, transcriber, live=None, moments=None, telemetry=None):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        if live:
            # presença ("jogando VALORANT") precisa dos dois intents privilegiados
            intents.presences = True
            intents.members = True
        super().__init__(intents=intents)
        self.live, self.moments, self.telemetry = live, moments, telemetry
        self._links, self._links_at = {}, 0.0
        self.brain, self.backend, self.transcriber = brain, backend, transcriber
        self.tree = app_commands.CommandTree(self)
        self.speaker = Speaker(config.TTS_ENGINE, config.TTS_VOICE, PiperEngine(config.PIPER_VOICE))
        self.voice_sessions = {}  # guild_id -> VoiceSession
        self.poster = _Poster(self)
        self.announcer = Announcer(brain, self.poster, backend,
                                   channel_id=config.ANNOUNCE_CHANNEL_ID,
                                   max_age_h=config.GOSSIP_MAX_AGE_H,
                                   claim_after_min=config.BOT_CLAIM_AFTER_MIN,
                                   post_scoreboard=config.BOT_POSTS_SCOREBOARD,
                                   reactions=config.GOSSIP_REACTIONS, mvp_vote=config.MVP_VOTE,
                                   rank_posts=config.PROACTIVE_RANK, telemetry=telemetry,
                                   mode_for=brain.mode_for)
        self._tasks = {}
        if live:
            live.on_new_matches = self.announcer.announce_now
            live.on_ranks = self.announcer.update_rr
        if moments:
            moments.on_prefila = self.post_prefila if config.PROACTIVE_PREFILA else None
            moments.on_night_end = self.post_night if config.PROACTIVE_NIGHT else None
            moments.on_oscar = self.post_oscar if config.PROACTIVE_OSCAR else None
        self.tree.add_command(build_commands(self))

    async def setup_hook(self):
        self.add_dynamic_items(MvpButton)
        # Comandos globais: valem em qualquer servidor onde o X9 entrar.
        await self.tree.sync()
        # Cópias por servidor (de versões antigas) apareceriam DUPLICADAS ao lado
        # das globais: limpa nos servidores listados.
        for gid in config.DISCORD_GUILD_IDS:
            guild = discord.Object(id=gid)
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)

    def _start(self, name, coro_fn):
        if name not in self._tasks:
            self._tasks[name] = asyncio.create_task(coro_fn())

    async def on_ready(self):
        logger.info("X9 online como %s em %d servidor(es).", self.user, len(self.guilds))
        if "modes" not in self._tasks:
            self._tasks["modes"] = True
            await asyncio.to_thread(self.brain.load_modes)
        self._start("announcer", lambda: self.announcer.run_forever(config.GOSSIP_POLL_S))
        if self.live and "live" not in self._tasks:
            await self._seed_presences()
            self._start("live", self.live.run_forever)
        if self.moments:
            self._start("moments", self.moments.run_forever)
        self._start("prewarm", self.brain.prewarm)

    # --- ao vivo (presença) ---------------------------------------------------
    async def _puuid_of(self, user_id):
        loop = asyncio.get_running_loop()
        if loop.time() - self._links_at > 60:
            self._links = await asyncio.to_thread(self.backend.db.discord_links)
            self._links_at = loop.time()
        return self._links.get(str(user_id))

    @staticmethod
    def valorant_activity(member):
        for a in getattr(member, "activities", None) or ():
            if (getattr(a, "name", "") or "").lower() == "valorant":
                start = getattr(a, "start", None)
                party = getattr(a, "party", None) or {}
                return {"details": getattr(a, "details", None), "state": getattr(a, "state", None),
                        "start": start.timestamp() if start else None,
                        "party": party.get("id") if isinstance(party, dict) else None}
        return None

    def _presence(self, puuid, activity, quiet=False):
        self.live.on_presence(puuid, activity)
        if self.moments:
            self.moments.on_presence(puuid, activity is not None, quiet=quiet)

    async def _seed_presences(self):
        await asyncio.to_thread(self.live.roster)
        seen = set()
        for g in self.guilds:
            for m in g.members:
                if m.bot or m.id in seen:
                    continue
                seen.add(m.id)
                pu = await self._puuid_of(m.id)
                if pu:
                    self._presence(pu, self.valorant_activity(m), quiet=True)

    async def on_presence_update(self, before, after):
        if not self.live or after.bot:
            return
        pu = await self._puuid_of(after.id)
        if pu:
            self._presence(pu, self.valorant_activity(after))

    # --- conversa por texto ------------------------------------------------
    async def on_message(self, message):
        if message.webhook_id and config.ANNOUNCE_CHANNEL_ID \
                and message.channel.id == config.ANNOUNCE_CHANNEL_ID and message.embeds:
            # Placar do poller acabou de sair: partida nova no banco. Fofoca já.
            self.backend.botapi.bump_version()
            self.announcer.kick()
            asyncio.create_task(self.brain.prewarm())
            return
        if message.author.bot or not message.guild or not self.user:
            return
        in_my_thread = (isinstance(message.channel, discord.Thread)
                        and message.channel.owner_id == self.user.id)
        mentioned = self.user in message.mentions
        if not (mentioned or in_my_thread):
            return
        text = message.content.replace(f"<@{self.user.id}>", "").replace(
            f"<@!{self.user.id}>", "").strip()
        if not text:
            text = "fala aí"
        target = message.channel
        if not isinstance(target, discord.Thread):
            try:
                target = await message.create_thread(name=f"X9 · {text[:60]}",
                                                     auto_archive_duration=1440)
            except discord.HTTPException:
                target = message.channel
        mentions = {m.id: m.display_name for m in message.mentions if m.id != self.user.id}
        await self.reply(target, message.author, text, mentions)

    def _mode_channel(self, channel):
        # thread herda o modo do canal-pai
        return getattr(channel, "parent_id", None) or channel.id

    async def ask(self, key, author, text, mentions=None, mode=None, on_text=None, on_wait=None):
        label = await asyncio.to_thread(self.backend.botapi.linked_label, author.id)
        try:
            return await self.brain.answer(key, label or author.display_name, text,
                                           speaker_discord_id=author.id,
                                           speaker_linked=bool(label), mentions=mentions,
                                           mode=mode, on_text=on_text, on_wait=on_wait)
        except BrainUnavailable:
            return UNAVAILABLE_REPLY

    async def _streamed(self, send, edit, key, author, text, mentions=None, mode=None):
        """Pergunta com a resposta escrita ao vivo (edições a cada ~1 s)."""
        live = LiveMessage(send, edit) if config.STREAM_REPLIES else None
        warned = []

        async def on_wait(seconds):
            if not warned:
                warned.append(1)
                await send(f"⏳ Bati no limite de cota do LLM; respondo em ~{int(seconds) + 1} s.")
        answer = await self.ask(key, author, text, mentions, mode,
                                on_text=live.on_text if live else None, on_wait=on_wait)
        if live:
            return await live.finish(answer)
        return [await send(p) for p in split_message(answer)]

    async def reply(self, channel, author, text, mentions=None):
        async def send(t):
            return await channel.send(t, allowed_mentions=NO_MENTIONS)

        async def edit(msg, t):
            await msg.edit(content=t, allowed_mentions=NO_MENTIONS)

        async with channel.typing():
            await self._streamed(send, edit, f"text:{channel.id}", author, text, mentions,
                                 self.brain.mode_for(self._mode_channel(channel)))

    # --- MVP ------------------------------------------------------------------
    async def handle_mvp_vote(self, inter, match_id, idx):
        cands = await asyncio.to_thread(self.announcer.mvp_candidates, match_id)
        if idx >= len(cands):
            await inter.response.send_message("Esse botão ficou velho.", ephemeral=True)
            return
        db = self.backend.db
        await asyncio.to_thread(db.cast_mvp_vote, match_id, inter.user.id, cands[idx][0])
        votes = await asyncio.to_thread(db.mvp_votes, match_id)
        tally = " · ".join(f"**{name}** {votes.get(pu, 0)}" for pu, name in cands[:5])
        await inter.response.edit_message(
            content=f"🗳️ **Quem foi o MVP de verdade?** (vota aí)\n{tally}",
            allowed_mentions=NO_MENTIONS)

    # --- momentos proativos -----------------------------------------------------
    async def _post_announce(self, text):
        if text and config.ANNOUNCE_CHANNEL_ID:
            await self.poster.post_text(config.ANNOUNCE_CHANNEL_ID, text)
            return True
        return False

    def _channel_mode(self):
        return self.brain.mode_for(config.ANNOUNCE_CHANNEL_ID)

    async def post_prefila(self, puuids):
        try:
            text = await self.brain.prefila(puuids, mode=self._channel_mode())
        except BrainUnavailable:
            text = await asyncio.to_thread(prefila_fallback, self.backend.botapi, puuids)
        if text:
            await self._post_announce("🎯 **Pré-fila do X9**\n" + text)

    async def post_night(self, since):
        try:
            text = await self.brain.night_recap(mode=self._channel_mode(), since=since)
        except BrainUnavailable:
            return
        if text:
            await self._post_announce("🌙 **Resenha da noite**\n" + text)
            await self.poster.speak("A noite acabou. A resenha tá no canal.")

    async def post_oscar(self):
        try:
            text = await self.brain.oscar(mode=self._channel_mode())
        except BrainUnavailable:
            return
        if text:
            await self._post_announce("🏆 **Oscar do X9 — a semana**\n" + text)

    # --- voz ------------------------------------------------------------------
    async def join(self, member, text_channel):
        if not member.voice or not member.voice.channel:
            return "Entra numa call primeiro."
        gid = member.guild.id
        old = self.voice_sessions.pop(gid, None)
        if old:
            await old.stop()
        vc = await member.voice.channel.connect(cls=voice_recv.VoiceRecvClient)
        await asyncio.to_thread(self.transcriber.warmup)
        await self.speaker.warmup()
        early = {"1": True, "0": False}.get(config.VOICE_EARLY_WAKE,
                                             self.transcriber.device == "cuda")
        session = VoiceSession(vc, self.brain, self.transcriber, text_channel, self.backend,
                               self.speaker, telemetry=self.telemetry, early_wake=early,
                               silence_s=config.VOICE_SILENCE_S, mode_for=self.brain.mode_for)
        session.start()
        self.voice_sessions[gid] = session
        asyncio.create_task(session.say("X9 na escuta. Chama X9 e pergunta."))
        return f"Entrei em **{member.voice.channel.name}**. Fala \"X9, …\" que eu respondo."

    async def leave(self, guild):
        s = self.voice_sessions.pop(guild.id, None)
        if not s:
            return "Não tô em call nenhuma."
        await s.stop()
        return "Fui. Continuo dedurando por texto."

    async def on_voice_state_update(self, member, before, after):
        # Call esvaziou (só sobrou o bot): sai sozinho.
        s = self.voice_sessions.get(member.guild.id)
        if not s or not s.connected():
            return
        ch = s.vc.channel
        if ch and not [m for m in ch.members if not m.bot]:
            await self.leave(member.guild)

    # --- status -----------------------------------------------------------------
    def provider_status(self):
        seen, out = set(), []
        for route in self.brain.routes.values():
            for st in (route.status() if hasattr(route, "status") else []):
                if st["provedor"] not in seen:
                    seen.add(st["provedor"])
                    out.append(st)
        return out

    def status_text(self):
        """Painel do /x9 status (bloqueante: lê o banco)."""
        db, botapi = self.backend.db, self.backend.botapi
        lines = []
        for route, label in (("texto", "Respostas (texto)"), ("voz", "Respostas (voz)"),
                             ("fofoca", "Fofocas/posts")):
            s = self.telemetry.answer_summary(route) if self.telemetry else None
            if s:
                lines.append(f"**{label}** p50 {s['p50_s']} s · p95 {s['p95_s']} s · "
                             f"{s['sem_tool_pct']}% sem ferramenta (n={s['n']})")
        v = self.telemetry.voice_summary() if self.telemetry else None
        if v:
            lines.append(f"**Voz** fim da fala → 1º áudio: p50 {v['p50_s']} s · p95 {v['p95_s']} s "
                         f"(n={v['n']})")
        m = match_summary(db.announcement_timings(20))
        if m:
            lines.append(f"**Partidas** fim → placar: p50 {m['placar_p50_min']} min · p95 "
                         f"{m['placar_p95_min']} min · placar → fofoca: p50 "
                         f"{m['fofoca_p50_s'] if m['fofoca_p50_s'] is not None else '?'} s "
                         f"({m['pelo_bot_pct']}% pelo bot, n={m['n']})")
        prov = []
        for p in self.provider_status():
            prov.append(f"{p['provedor']} " + ("✅" if p["ok"] else
                                                f"⏳ volta em {p['volta_em_s']} s"))
        if prov:
            lines.append("**LLM** " + " · ".join(prov))
        if self.live:
            st = self.live.status()
            playing = [r["nome"] for r in st["jogadores"] if r["jogando"]]
            nxt = ", ".join(f"{r['nome']} em {r['proxima_em_s']} s" for r in st["jogadores"][:3])
            lines.append(f"**Ao vivo** jogando: {', '.join(playing) or 'ninguém'} · próximas: "
                         f"{nxt or '—'} · {st['consultas_ultimo_min']} consulta(s) no último min")
        runs = db.recent_ingest_runs(1)
        if runs:
            r = runs[0]
            ago = int((time.time() - (r.get("finished_raw") or r.get("started_raw") or 0)) // 60)
            lines.append(f"**Poller** última execução há {ago} min "
                         f"{'✅' if r.get('ok') else '❌'} ({r.get('new_matches') or 0} partida(s) nova(s))")
        cs = getattr(botapi, "cache_stats", None)
        if cs and (cs["hits"] + cs["misses"]):
            lines.append(f"**Cache** {round(100 * cs['hits'] / (cs['hits'] + cs['misses']))}% de "
                         f"acerto ({cs['hits'] + cs['misses']} leituras)")
        return "\n".join(lines) or "Sem medições ainda: pergunte algo e espere uma partida."


def prefila_fallback(botapi, puuids):
    """Pré-fila sem LLM (cota estourada): só os números."""
    mat = botapi.material_prefila(list(puuids))
    if not mat:
        return None
    lines = [f"Jogando: {', '.join(mat['jogadores'])}"]
    for m in mat["mapas"]:
        if m["veredito"] in ("filar", "evitar"):
            lines.append(f"{'✅' if m['veredito'] == 'filar' else '⛔'} {m['mapa']}: "
                         f"{m['win_rate']}% em {m['partidas']} ({m['base']})")
    return "\n".join(lines) if len(lines) > 1 else None


def _is_admin(inter):
    """Gerenciar Servidor. Checado na mão: o Discord só aplica
    default_member_permissions em comando de topo, não em subcomando."""
    perms = getattr(inter.user, "guild_permissions", None)
    return bool(perms and perms.manage_guild)


def _fmt_rows(rows, fmt):
    return "\n".join(fmt(r) for r in rows) or "—"


def build_commands(bot):
    group = app_commands.Group(name="x9", description="X9, o dedo-duro do grupo")
    b = bot.backend

    @group.command(name="pergunta", description="Pergunta qualquer coisa sobre as partidas")
    @app_commands.describe(texto="Ex.: quem carregou na última? o que rolou no round 13?")
    async def pergunta(inter: discord.Interaction, texto: str):
        await inter.response.defer(thinking=True)
        first = {}

        async def send(t):
            if not first:
                first["msg"] = await inter.followup.send(f"> {texto[:180]}\n{t}"[:2000], wait=True,
                                                         allowed_mentions=NO_MENTIONS)
                return first["msg"]
            return await inter.followup.send(t, wait=True, allowed_mentions=NO_MENTIONS)

        async def edit(msg, t):
            prefix = f"> {texto[:180]}\n" if msg is first.get("msg") else ""
            await msg.edit(content=f"{prefix}{t}"[:2000], allowed_mentions=NO_MENTIONS)

        channel = inter.channel
        mode = bot.brain.mode_for(bot._mode_channel(channel)) if channel else None
        await bot._streamed(send, edit, f"text:{inter.channel_id}", inter.user, texto, mode=mode)

    @group.command(name="ultima", description="Placar da última partida (sem IA)")
    @app_commands.describe(jogador="Apelido ou Nome#TAG (opcional)")
    async def ultima(inter: discord.Interaction, jogador: str = None):
        await inter.response.defer()

        def build():
            ctx = b.botapi._Ctx(inter.user.id)
            mid, _ = b.botapi._resolve_match(ctx, "ultima", jogador)
            return b.announce.build_embed(mid, ctx)

        try:
            embed = await asyncio.to_thread(build)
        except b.botapi.ToolError as e:
            await inter.followup.send(str(e))
            return
        await inter.followup.send(embed=discord.Embed.from_dict(embed))

    @group.command(name="hoje", description="A noite de hoje: saldo, RR e quem tiltou (sem IA)")
    async def hoje(inter: discord.Interaction):
        await inter.response.defer()
        n = await asyncio.to_thread(b.botapi.call_tool, "noite_de_hoje", {}, inter.user.id)
        if not n.get("tem_noite"):
            await inter.followup.send("Ninguém jogou nas últimas horas.")
            return
        head = (f"🌙 **Noite** desde {n['comecou']}: {n['partidas']} partida(s), "
                f"**{n['vitorias']}V {n['derrotas']}D** `{n['resultados']}`")
        rows = _fmt_rows(n["jogadores"], lambda p: (
            f"**{p['nome']}** {p['vitorias']}V {p['derrotas']}D · K/D {p['kd']} · ACS {p['acs']}"
            + (f" · {p['rr']:+d} RR" if p.get("rr") is not None else "")
            + (f" · tilt: {p['tilt']}" if p.get("tilt") else "")))
        tail = (f"\n👑 MVP: **{n['mvp_da_noite']}**" if n.get("mvp_da_noite") else "") + \
               (f" · 🐟 bagre: **{n['bagre_da_noite']}**" if n.get("bagre_da_noite") else "")
        await inter.followup.send(f"{head}\n{rows}{tail}"[:2000], allowed_mentions=NO_MENTIONS)

    @group.command(name="elo", description="Elo e RR de todo mundo (sem IA)")
    async def elo(inter: discord.Interaction):
        await inter.response.defer()
        out = await asyncio.to_thread(b.botapi.call_tool, "elo_e_rr", {}, inter.user.id)
        rows = _fmt_rows([e for e in out.get("elo", []) if e.get("disponivel", True)], lambda e: (
            f"**{e['jogador']}** {e['elo']}" + (f" · {e['rr']} RR" if e.get("rr") is not None else "")
            + (f" · hoje {e['saldo_hoje']:+d}" if e.get("saldo_hoje") else "")
            + (f" · 7 dias {e['saldo_7_dias']:+d}" if e.get("saldo_7_dias") else "")
            + (f" · pico {e['pico']}" if e.get("pico") else "")))
        await inter.followup.send(f"📊 **Elo agora**\n{rows}"[:2000], allowed_mentions=NO_MENTIONS)

    @group.command(name="ranking", description="Ranking do grupo (sem IA)")
    async def ranking(inter: discord.Interaction):
        await inter.response.defer()
        out = await asyncio.to_thread(b.botapi.call_tool, "ranking_grupo", {}, inter.user.id)
        rows = _fmt_rows(out.get("ranking", []), lambda r: (
            f"`{r['pos']}.` **{r['nome']}** · {r['win_rate']}% WR · K/D {r['kd']} · ACS {r['acs']} "
            f"({r['partidas']} partidas)"))
        await inter.followup.send(f"🏆 **Ranking**\n{rows}"[:2000], allowed_mentions=NO_MENTIONS)

    @group.command(name="fila", description="Pré-fila: mapas para aceitar/evitar e escalação (sem IA)")
    @app_commands.describe(jogadores="Quem vai jogar, separado por vírgula (padrão: quem está on)")
    async def fila(inter: discord.Interaction, jogadores: str = None):
        await inter.response.defer()
        if not jogadores and bot.live:
            on = [p["nome"] for p in bot.live.playing_now()["jogando_agora"]]
            me = await asyncio.to_thread(b.botapi.linked_label, inter.user.id)
            jogadores = ", ".join(dict.fromkeys(on + ([me] if me else [])))
        if not jogadores:
            await inter.followup.send("Diz quem vai jogar: `/x9 fila jogadores: fulano, ciclano`.")
            return
        out = await asyncio.to_thread(b.botapi.call_tool, "pre_fila", {"jogadores": jogadores},
                                      inter.user.id)
        if "erro" in out:
            await inter.followup.send(out["erro"])
            return
        icon = {"filar": "✅", "neutro": "➖", "evitar": "⛔", "sem amostra": "❔"}
        rows = _fmt_rows(out["mapas"][:8], lambda m: (
            f"{icon.get(m['veredito'], '')} **{m['mapa']}** "
            + (f"{m['win_rate']}% em {m['partidas']} ({m['base']})" if m["win_rate"] is not None
               else "sem amostra")
            + (f"\n  ↳ {' · '.join(m['escalacao'])}" if m["veredito"] == "filar" else "")))
        await inter.followup.send(f"🎯 **Pré-fila** — {', '.join(out['jogadores'])}\n{rows}"[:2000],
                                  allowed_mentions=NO_MENTIONS)

    @group.command(name="modo", description="Personalidade do X9 neste canal")
    @app_commands.choices(modo=[app_commands.Choice(name=m, value=m) for m in MODES])
    async def modo(inter: discord.Interaction, modo: app_commands.Choice[str]):
        channel = bot._mode_channel(inter.channel)
        await asyncio.to_thread(bot.brain.set_mode, channel, modo.value)
        desc = {"zoeira": "zoeira sem filtro (padrão)", "coach": "coach sério, foco em melhorar",
                "narrador": "narrador esportivo empolgado"}[modo.value]
        await inter.response.send_message(f"Modo deste canal: **{modo.value}** — {desc}.")

    @group.command(name="lore", description="Apelidos e momentos que o X9 lembra")
    @app_commands.describe(apagar="Número do item para apagar (admin) ou 0 para apagar tudo")
    async def lore(inter: discord.Interaction, apagar: int = None):
        if apagar is not None:
            if not _is_admin(inter):
                await inter.response.send_message("Só quem tem **Gerenciar Servidor** apaga lore.",
                                                  ephemeral=True)
                return
            n = await asyncio.to_thread(b.db.delete_lore, None if apagar == 0 else apagar)
            b.botapi.invalidate_cache()
            await inter.response.send_message(f"Apaguei {n} item(ns) da lore.", ephemeral=True)
            return
        items = await asyncio.to_thread(b.db.active_lore)
        rows = _fmt_rows(items[:20], lambda i: f"`{i['id']}` {i['text']}")
        await inter.response.send_message(f"📜 **Lore do grupo**\n{rows}"[:2000],
                                          allowed_mentions=NO_MENTIONS)

    @group.command(name="aposta", description="Aposta no resultado da próxima partida do grupo")
    @app_commands.choices(resultado=[app_commands.Choice(name="vitória", value="vitoria"),
                                     app_commands.Choice(name="derrota", value="derrota")])
    async def aposta(inter: discord.Interaction, resultado: app_commands.Choice[str]):
        await asyncio.to_thread(b.db.place_bet, inter.user.id, resultado.value)
        await inter.response.send_message(
            f"🎲 {inter.user.mention} apostou em **{resultado.name}** na próxima partida do grupo.",
            allowed_mentions=NO_MENTIONS)

    @group.command(name="apostas", description="Quem mais acerta as apostas")
    async def apostas(inter: discord.Interaction):
        rows = await asyncio.to_thread(b.db.bet_ranking)
        text = _fmt_rows(rows[:10], lambda r: (f"<@{r['discord_id']}> {r['acertos'] or 0}/{r['total']} "
                                               f"({round(100 * (r['acertos'] or 0) / r['total'])}%)"))
        opened = await asyncio.to_thread(b.db.open_bets)
        if opened:
            text += "\nAbertas: " + ", ".join(f"<@{o['discord_id']}> {o['pick']}" for o in opened)
        await inter.response.send_message(f"🎲 **Apostas**\n{text}"[:2000], allowed_mentions=NO_MENTIONS)

    @group.command(name="status", description="Saúde e velocidade do X9 (p50/p95)")
    async def status(inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        text = await asyncio.to_thread(bot.status_text)
        await inter.followup.send(f"🩺 **X9 status**\n{text}"[:2000], ephemeral=True)

    @group.command(name="entrar", description="X9 entra na sua call e passa a ouvir")
    async def entrar(inter: discord.Interaction):
        await inter.response.defer(thinking=True)
        try:
            msg = await bot.join(inter.user, inter.channel)
        except Exception as e:
            logger.exception("Falha ao entrar na call")
            msg = f"Não consegui entrar na call: {e}"
        await inter.followup.send(msg)

    @group.command(name="sair", description="X9 sai da call")
    async def sair(inter: discord.Interaction):
        await inter.response.send_message(await bot.leave(inter.guild))

    @group.command(name="vincular", description="Liga um membro do Discord a um jogador do grupo")
    @app_commands.describe(membro="Quem no Discord", riot_id="Nome#TAG ou apelido no grupo")
    async def vincular(inter: discord.Interaction, membro: discord.Member, riot_id: str):
        if not _is_admin(inter):
            await inter.response.send_message("Só quem tem **Gerenciar Servidor** vincula.", ephemeral=True)
            return
        try:
            label = await asyncio.to_thread(b.botapi.link_member, membro.id, riot_id, inter.user.id)
        except b.botapi.ToolError as e:
            await inter.response.send_message(str(e), ephemeral=True)
            return
        bot._links_at = 0.0
        await inter.response.send_message(f"{membro.mention} agora é **{label}**. Não tem mais onde se esconder.",
                                          allowed_mentions=NO_MENTIONS)

    @group.command(name="desvincular", description="Remove o vínculo de um membro")
    async def desvincular(inter: discord.Interaction, membro: discord.Member):
        if not _is_admin(inter):
            await inter.response.send_message("Só quem tem **Gerenciar Servidor** desvincula.", ephemeral=True)
            return
        n = await asyncio.to_thread(b.botapi.unlink_member, membro.id)
        bot._links_at = 0.0
        await inter.response.send_message("Desvinculado." if n else "Ele não estava vinculado.",
                                          ephemeral=True)

    @group.command(name="vinculos", description="Quem é quem")
    async def vinculos(inter: discord.Interaction):
        links = await asyncio.to_thread(b.botapi.list_links)
        if not links:
            await inter.response.send_message("Ninguém vinculado ainda. Admin: `/x9 vincular`.",
                                              ephemeral=True)
            return
        lines = [f"<@{l['discord_id']}> → **{l['nome']}**" for l in links]
        await inter.response.send_message("\n".join(lines), allowed_mentions=NO_MENTIONS)

    return group
