"""Cliente do Discord: menções, threads, /x9 …, voz e o loop do dedo-duro."""
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import voice_recv

from . import config
from .announcer import Announcer
from .brain import UNAVAILABLE_REPLY, BrainUnavailable, mention_ids, split_message
from .tts import PiperEngine, Speaker
from .voice import VoiceSession

logger = logging.getLogger("x9.discord")


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

    async def post_in_thread(self, channel_id, message_id, title, text):
        ch = await self._channel(channel_id)
        try:
            msg = await ch.fetch_message(int(message_id))
        except discord.NotFound:
            # Placar apagado: fofoca vai solta no canal mesmo.
            for part in split_message(text):
                await ch.send(part)
            return None
        try:
            thread = await msg.create_thread(name=title[:90], auto_archive_duration=1440)
        except discord.HTTPException:
            # Thread já existe (id da thread = id da mensagem).
            thread = await self.bot.fetch_channel(msg.id)
        for part in split_message(text):
            await thread.send(part)
        return thread.id

    def in_voice(self):
        return any(s.connected() for s in self.bot.voice_sessions.values())

    async def speak(self, text):
        spoke = False
        for s in list(self.bot.voice_sessions.values()):
            if s.connected():
                spoke = await s.say(text) or spoke
        return spoke


class X9Bot(discord.Client):
    def __init__(self, brain, backend, transcriber):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.brain, self.backend, self.transcriber = brain, backend, transcriber
        self.tree = app_commands.CommandTree(self)
        self.speaker = Speaker(config.TTS_ENGINE, config.TTS_VOICE, PiperEngine(config.PIPER_VOICE))
        self.voice_sessions = {}  # guild_id -> VoiceSession
        self.poster = _Poster(self)
        self.announcer = Announcer(brain, self.poster, backend,
                                   channel_id=config.ANNOUNCE_CHANNEL_ID,
                                   max_age_h=config.GOSSIP_MAX_AGE_H,
                                   claim_after_min=config.BOT_CLAIM_AFTER_MIN,
                                   post_scoreboard=config.BOT_POSTS_SCOREBOARD)
        self._announcer_task = None
        self.tree.add_command(build_commands(self))

    async def setup_hook(self):
        # Comandos globais: valem em qualquer servidor onde o X9 entrar.
        await self.tree.sync()
        # Cópias por servidor (de versões antigas) apareceriam DUPLICADAS ao lado
        # das globais: limpa nos servidores listados.
        for gid in config.DISCORD_GUILD_IDS:
            guild = discord.Object(id=gid)
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)

    async def on_ready(self):
        logger.info("X9 online como %s em %d servidor(es).", self.user, len(self.guilds))
        if self._announcer_task is None:
            self._announcer_task = asyncio.create_task(
                self.announcer.run_forever(config.GOSSIP_POLL_S))

    # --- conversa por texto ------------------------------------------------
    async def on_message(self, message):
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

    async def ask(self, key, author, text, mentions=None):
        label = await asyncio.to_thread(self.backend.botapi.linked_label, author.id)
        try:
            return await self.brain.answer(key, label or author.display_name, text,
                                           speaker_discord_id=author.id,
                                           speaker_linked=bool(label), mentions=mentions)
        except BrainUnavailable:
            return UNAVAILABLE_REPLY

    async def reply(self, channel, author, text, mentions=None):
        async with channel.typing():
            answer = await self.ask(f"text:{channel.id}", author, text, mentions)
        for part in split_message(answer):
            await channel.send(part, allowed_mentions=discord.AllowedMentions.none())

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
        session = VoiceSession(vc, self.brain, self.transcriber, text_channel, self.backend,
                               self.speaker)
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


def _is_admin(inter):
    """Gerenciar Servidor. Checado na mão: o Discord só aplica
    default_member_permissions em comando de topo, não em subcomando."""
    perms = getattr(inter.user, "guild_permissions", None)
    return bool(perms and perms.manage_guild)


def build_commands(bot):
    group = app_commands.Group(name="x9", description="X9, o dedo-duro do grupo")

    @group.command(name="pergunta", description="Pergunta qualquer coisa sobre as partidas")
    @app_commands.describe(texto="Ex.: quem carregou na última? o que rolou no round 13?")
    async def pergunta(inter: discord.Interaction, texto: str):
        await inter.response.defer(thinking=True)
        answer = await bot.ask(f"text:{inter.channel_id}", inter.user, texto)
        parts = split_message(answer)
        await inter.followup.send(f"> {texto[:180]}\n{parts[0]}"[:2000],
                                  allowed_mentions=discord.AllowedMentions.none())
        for part in parts[1:]:
            await inter.followup.send(part, allowed_mentions=discord.AllowedMentions.none())

    @group.command(name="ultima", description="Placar da última partida (sem IA)")
    @app_commands.describe(jogador="Apelido ou Nome#TAG (opcional)")
    async def ultima(inter: discord.Interaction, jogador: str = None):
        await inter.response.defer()
        b = bot.backend

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
        b = bot.backend
        try:
            label = await asyncio.to_thread(b.botapi.link_member, membro.id, riot_id, inter.user.id)
        except b.botapi.ToolError as e:
            await inter.response.send_message(str(e), ephemeral=True)
            return
        await inter.response.send_message(f"{membro.mention} agora é **{label}**. Não tem mais onde se esconder.",
                                          allowed_mentions=discord.AllowedMentions.none())

    @group.command(name="desvincular", description="Remove o vínculo de um membro")
    async def desvincular(inter: discord.Interaction, membro: discord.Member):
        if not _is_admin(inter):
            await inter.response.send_message("Só quem tem **Gerenciar Servidor** desvincula.", ephemeral=True)
            return
        n = await asyncio.to_thread(bot.backend.db.unlink_discord, membro.id)
        await inter.response.send_message("Desvinculado." if n else "Ele não estava vinculado.",
                                          ephemeral=True)

    @group.command(name="vinculos", description="Quem é quem")
    async def vinculos(inter: discord.Interaction):
        links = await asyncio.to_thread(bot.backend.botapi.list_links)
        if not links:
            await inter.response.send_message("Ninguém vinculado ainda. Admin: `/x9 vincular`.",
                                              ephemeral=True)
            return
        lines = [f"<@{l['discord_id']}> → **{l['nome']}**" for l in links]
        await inter.response.send_message("\n".join(lines),
                                          allowed_mentions=discord.AllowedMentions.none())

    return group
