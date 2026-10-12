"""Call de voz: ouve (Whisper), reconhece "X9, …", responde falando (edge-tts ou Piper).

O Discord não manda pacote durante o silêncio, então uma fala termina quando o
usuário fica `SILENCE_S` sem pacote. A recepção usa o fork do
discord-ext-voice-recv com suporte a DAVE (E2EE obrigatório desde mar/2026).
"""
import asyncio
import io
import logging
import threading
import time
from collections import defaultdict

import discord
from discord.ext import voice_recv

from . import audio, wake
from .brain import UNAVAILABLE_REPLY, BrainUnavailable

logger = logging.getLogger("x9.voice")

SILENCE_S = 0.8
MIN_UTTERANCE_S = 0.4
MAX_UTTERANCE_S = 15.0


class UtteranceSink(voice_recv.AudioSink):
    """Junta o PCM de cada pessoa até ela parar de falar."""

    def __init__(self, clock=time.monotonic):
        super().__init__()
        self.clock = clock
        self._lock = threading.Lock()
        self._buf = defaultdict(bytearray)
        self._last = {}
        self._who = {}

    def wants_opus(self):
        return False

    def write(self, user, data):
        if user is None or getattr(user, "bot", False) or not data.pcm:
            return
        with self._lock:
            self._buf[user.id] += data.pcm
            self._last[user.id] = self.clock()
            self._who[user.id] = user

    def cleanup(self):
        with self._lock:
            self._buf.clear()

    def pop_finished(self):
        """[(membro, pcm)] das falas que terminaram (silêncio ou tamanho máximo)."""
        now = self.clock()
        out = []
        with self._lock:
            for uid in list(self._buf):
                pcm = self._buf[uid]
                done = now - self._last.get(uid, now) >= SILENCE_S
                if done or audio.pcm_seconds(pcm) >= MAX_UTTERANCE_S:
                    if audio.pcm_seconds(pcm) >= MIN_UTTERANCE_S:
                        out.append((self._who[uid], bytes(pcm)))
                    del self._buf[uid]
        return out


class VoiceSession:
    """Dois estágios: o Whisper transcreve todo mundo sem parar e só as falas com
    "X9" vão para a fila de respostas. Antes, o mesmo worker transcrevia E
    esperava o LLM, então durante uma resposta as falas se acumulavam, a fila
    enchia e até o "X9, …" seguinte era descartado."""

    ACK_TEXT = "Deixa eu ver."

    def __init__(self, vc, brain, transcriber, text_channel, backend, speaker):
        self.vc, self.brain, self.transcriber = vc, brain, transcriber
        self.text_channel, self.backend, self.speaker = text_channel, backend, speaker
        self.sink = UtteranceSink()
        self._speak_lock = asyncio.Lock()
        self._heard = asyncio.Queue(maxsize=32)
        self._asked = asyncio.Queue(maxsize=4)
        self._ack_pcm = None
        self._tasks = []

    def start(self):
        self.vc.listen(self.sink)
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._collect()), loop.create_task(self._transcribe()),
                       loop.create_task(self._answer())]

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        if self.vc.is_listening():
            self.vc.stop_listening()
        await self.vc.disconnect(force=True)

    def connected(self):
        return self.vc.is_connected()

    @staticmethod
    def _put(queue, item, what):
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            # Fala velha perde para a nova: a conversa já andou.
            queue.get_nowait()
            queue.put_nowait(item)
            logger.warning("Fila de %s cheia; descartei a mais antiga.", what)

    async def _collect(self):
        while True:
            await asyncio.sleep(0.1)
            for member, pcm in self.sink.pop_finished():
                self._put(self._heard, (member, pcm), "fala")

    async def _transcribe(self):
        while True:
            member, pcm = await self._heard.get()
            try:
                text = await asyncio.to_thread(self.transcriber.transcribe,
                                               audio.discord_to_whisper(pcm))
            except Exception:
                logger.exception("Falha ao transcrever fala de %s", member)
                continue
            if not text:
                continue
            question = wake.strip_wake(text)
            logger.info("🎙️ %s: %s%s", member.display_name, text, "" if question is None else "  [X9]")
            if question is not None:
                self._put(self._asked, (member, question), "perguntas")

    async def _answer(self):
        while True:
            member, question = await self._asked.get()
            try:
                await self._handle(member, question)
            except Exception:
                logger.exception("Falha ao responder %s", member)

    async def _ack(self):
        """Resposta imediata enquanto o LLM pensa (o áudio é sintetizado uma vez só)."""
        try:
            if self._ack_pcm is None:
                self._ack_pcm = await self.speaker.synthesize(self.ACK_TEXT)
            await self.play(self._ack_pcm)
        except Exception as e:
            logger.warning("Não consegui tocar o aviso: %s", e)

    async def _handle(self, member, question):
        if not question:
            await self.say("Fala, tô ouvindo.")
            return
        t0 = time.monotonic()
        ack = asyncio.create_task(self._ack())
        label = await asyncio.to_thread(self.backend.botapi.linked_label, member.id)
        try:
            reply = await self.brain.answer(f"voice:{self.vc.channel.id}", label or member.display_name,
                                            question, speaker_discord_id=member.id,
                                            speaker_linked=bool(label), voice=True)
        except BrainUnavailable:
            reply = UNAVAILABLE_REPLY
        logger.info("X9 respondeu %s em %.1fs", member.display_name, time.monotonic() - t0)
        if self.text_channel:
            try:
                await self.text_channel.send(f"🎙️ **{member.display_name}:** {question}\n"
                                             f"🕵️ **X9:** {reply}"[:1990])
            except discord.HTTPException:
                pass
        await ack
        await self.say(reply)

    async def say(self, text):
        """Fala na call (uma frase por vez; não se atropela)."""
        if not self.connected():
            return False
        return await self.play(await self.speaker.synthesize(text))

    async def play(self, pcm):
        if not self.connected():
            return False
        async with self._speak_lock:
            done = asyncio.Event()
            loop = asyncio.get_running_loop()

            def after(err):
                if err:
                    logger.warning("Erro tocando áudio: %s", err)
                loop.call_soon_threadsafe(done.set)

            if self.vc.is_playing():
                self.vc.stop()
            self.vc.play(discord.PCMAudio(io.BytesIO(pcm)), after=after)
            await done.wait()
        return True
